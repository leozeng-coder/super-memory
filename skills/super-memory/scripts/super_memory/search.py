from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .domain import CHINESE, MemoryError, Record

ALIASES = {"c++": "cpp", "cxx": "cpp", "cplusplus": "cpp", "js": "javascript", "ts": "typescript", "py": "python", "c#": "csharp", "golang": "go", "nodejs": "node.js"}
SCOPE_ORDER = {"feature": 5, "module": 4, "project": 3, "technology": 2, "language": 1, "global": 0}
PRIORITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
OMITTED = "部分规则因数量或字符预算未返回，请缩小查询范围或增大 --limit / --max-chars。"


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def technical(value: str) -> str:
    value = normalize(value)
    return ALIASES.get(value, value)


def tokens(value: str) -> set[str]:
    return {technical(token) for token in re.findall(r"[a-z0-9_][a-z0-9_.+#:-]*|[\u3400-\u9fff]+", normalize(value))}


@dataclass(frozen=True)
class SearchRequest:
    query: str
    project: str | None = None
    modules: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    limit: int = 8
    max_chars: int = 16000
    include_candidates: bool = False
    features: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def __post_init__(self):
        if self.limit < 1 or self.max_chars < 512:
            raise MemoryError("validation_error", "--limit 必须大于 0，--max-chars 至少为 512。")
        if self.modules and not self.project:
            raise MemoryError("validation_error", "--module 必须与 --project 一起使用。")
        if (self.features or self.paths) and (not self.project or len(self.modules) != 1):
            raise MemoryError("validation_error", "--feature / --path 必须同时指定项目和一个模块。")

    def scope(self, rule: dict) -> tuple[bool, bool]:
        known = True
        for field, requested in (("projects", (self.project,) if self.project else ()), ("modules", self.modules), ("features", self.features), ("languages", self.languages), ("technologies", self.technologies)):
            convert = normalize if field in {"projects", "modules", "features"} else technical
            declared = {convert(value) for value in rule["scope"].get(field, [])}
            if declared and not requested:
                known = False
            if declared and requested and declared.isdisjoint(convert(value) for value in requested):
                return False, False
        if self.project and not self.modules and rule["scope"]["modules"]:
            return False, False
        if self.modules and not self.features and rule["scope"].get("features"):
            return False, False
        return True, known


def match_terms(rule: dict, query: str) -> list[str]:
    phrases = [rule["title"], *rule["trigger"]["keywords"], *rule["tags"]]
    fields = [*phrases, rule["trigger"]["description"], *rule["scope"]["languages"], *rule["scope"]["technologies"]]
    query_tokens = tokens(query)
    matched = query_tokens & tokens(" ".join(fields))
    matched.update(term for term in tokens(" ".join(phrases)) if len(term) >= 2 and CHINESE.search(term) and term in normalize(query))
    matched.update(term for term in query_tokens if len(term) >= 2 and CHINESE.search(term) and term in normalize(" ".join(fields)))
    return sorted(matched)


def search(records: list[Record], request: SearchRequest, root: Path, routing_warnings: list[str] | None = None) -> dict:
    ranked = []
    for record in records:
        rule = record.rule
        if rule["status"] != "active" and not (request.include_candidates and rule["status"] == "candidate"):
            continue
        applies, known = request.scope(rule)
        if not applies:
            continue
        terms = match_terms(rule, request.query)
        feature_match = bool(request.features and rule["scope"].get("features"))
        required = rule["status"] == "active" and rule["trigger"].get("always_apply", False) and known
        if terms or required or feature_match:
            score = len(terms) * 10 + PRIORITY[rule["priority"]] + rule["confidence"]
            ranked.append((record, terms, required, score))
    ranked.sort(key=lambda item: (not item[2], item[0].rule["status"] != "active", -item[3], -SCOPE_ORDER[item[0].rule["scope"]["level"]], item[0].rule["id"]))
    result = {"memory_root": str(root), "rules": [], "matched_count": len(ranked), "omitted_count": len(ranked), "warnings": []}
    if request.features or request.paths:
        result["context"] = {"project": request.project, "modules": list(request.modules), "features": list(request.features), "paths": list(request.paths)}
    def fits() -> bool:
        result["warnings"] = [*(routing_warnings or []), *([OMITTED] if result["omitted_count"] else [])]
        return len(json.dumps(result, ensure_ascii=False, indent=2)) + 1 <= request.max_chars
    if not fits():
        raise MemoryError("budget_exceeded", "输出元数据超出预算，请增大 --max-chars。")
    ordinary = 0
    for record, terms, required, score in ranked:
        if not required and ordinary >= request.limit:
            continue
        rule = record.rule
        entry = {key: rule[key] for key in ("id", "title", "status", "priority", "scope", "trigger", "instruction", "exceptions", "relations")}
        entry.update(revision=record.revision, path=record.path.relative_to(root).as_posix(), matched_terms=terms, required=required, score=round(score, 2))
        if request.features and rule["scope"].get("features"):
            entry["matched_feature"] = rule["scope"]["features"][0]
        result["rules"].append(entry)
        result["omitted_count"] -= 1
        if not fits():
            result["rules"].pop()
            result["omitted_count"] += 1
            fits()
            if required:
                raise MemoryError("budget_exceeded", "必须应用的规则超出预算，请增大 --max-chars；规则正文和例外未被截断。")
        elif not required:
            ordinary += 1
    return result
