from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .domain import CHINESE, MemoryError, Record, identifier_key, normalize_text

ALIASES = {
    "c++": "cpp",
    "cxx": "cpp",
    "cplusplus": "cpp",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "c#": "csharp",
    "golang": "go",
    "nodejs": "node.js",
}
BASE_CONCEPT_GROUPS = {
    "参数": ("参数", "入参", "形参", "parameter", "argument"),
    "行布局": ("单行", "同一行", "换行", "line break", "single line"),
    "观察者": ("观察者", "监听者", "observer", "listener"),
    "shared_ptr": ("shared_ptr", "sharedptr", "shared pointer", "强引用"),
    "weak_ptr": ("weak_ptr", "weakptr", "weak pointer", "弱引用"),
    "循环引用": ("循环引用", "引用环", "相互引用", "互相引用", "相互持有", "互相持有"),
    "释放": ("析构", "释放", "回收", "销毁"),
    "随机生成器": ("rng", "mt19937", "random engine", "随机引擎", "随机生成器", "随机数生成器", "伪随机源", "随机源", "随机上下文"),
    "预分配": ("reserve", "预分配", "预留容量", "容量规划", "提前安排容量", "扩容", "容量"),
    "数值常量": ("字面量", "常量", "literal"),
    "数值类型": ("包装类型", "数值类型", "numeric type"),
    "坐标": ("坐标", "位置", "coordinate", "position"),
    "提前返回": ("提前返回", "early return", "guard clause"),
    "稳定编号": ("稳定编号", "唯一编号", "重启不变", "不变编号", "编辑器编号"),
    "窄整数": ("窄整数", "整数宽度", "业务上界", "取值范围", "上限很小"),
    "配置热更": ("配置热更", "配置刷新", "热更新", "hot reload"),
    "释放后使用": ("悬空指针", "旧表指针", "use-after-free", "释放后使用"),
}
COMMON_TERMS = {
    "修改",
    "不能",
    "不要",
    "上下文",
    "同一",
    "调用",
    "调用者",
    "提供",
    "每个",
    "负责",
    "避免",
    "原因",
    "代码",
    "数据",
    "对象",
    "函数",
    "逻辑",
    "列表",
    "状态",
    "配置",
    "项目",
    "当前",
    "如果",
    "必须",
    "用户",
    "问题",
    "规则",
    "需要",
    "可以",
    "进行",
    "服务",
    "场景事件",
    "事件",
    "使用",
    "检查",
    "新增",
    "更新",
    "所有",
    "客户端",
    "服务端",
    "code",
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "c",
    "cpp",
    "csharp",
    "data",
    "event",
    "entity",
    "exe",
    "exist",
    "for",
    "from",
    "get",
    "go",
    "h",
    "handler",
    "id",
    "in",
    "into",
    "is",
    "java",
    "javascript",
    "js",
    "json",
    "logic",
    "logicsceneevent",
    "map",
    "mdc",
    "node.js",
    "nodejs",
    "of",
    "on",
    "or",
    "out",
    "python",
    "py",
    "push",
    "back",
    "result",
    "rust",
    "scene",
    "sceneevent",
    "set",
    "src",
    "std",
    "the",
    "time",
    "to",
    "ts",
    "typescript",
    "with",
    "without",
    "item",
    "items",
    "object",
    "value",
    "values",
}
ASCII_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#.:/\-]*")
CHINESE_SPAN = re.compile(r"[\u3400-\u9fff]+")
CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
CAMEL_WORD = re.compile(r"([a-z0-9])([A-Z])")
QUESTION_COLON = re.compile(r"\?[^:\r\n]{1,200}:")
TERNARY_ATOM = re.compile(r"[A-Za-z_$0-9][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
SCOPE_ORDER = {"feature": 5, "module": 4, "project": 3, "technology": 2, "language": 1, "global": 0}
PRIORITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
OMITTED = "部分规则因数量或字符预算未返回，请缩小查询范围或增大 --limit / --max-chars。"
TOPIC_MISS = "查询主题没有命中规则；当前仅返回必须应用规则，请补充代码符号、行为语义或文件路径后重试。"
MIN_RELEVANCE = 25

# Only standalone, explicitly excluded background is safe to remove here.
# Task-specific interpretation of free-form prose belongs to the caller.
EXCLUDED_BACKGROUND = re.compile(
    r"^(?:无关背景|与(?:本次|这次)(?:改动|任务|修改)无关(?:的背景)?)\s*[:：]"
    r"|^(?:历史工单|旧工单|背景资料).*?[,，]\s*与(?:本次|这次)(?:改动|任务|修改)无关$"
)


def task_query(text: str) -> str:
    sentences = [part.strip() for part in re.split(r"[。！？；\r\n]+", text) if part.strip()]
    relevant = [part for part in sentences if not EXCLUDED_BACKGROUND.search(part)]
    if len(relevant) == len(sentences):
        return text
    if not relevant:
        raise MemoryError("validation_error", "查询仅含已标为无关的背景，请提供本次任务的操作或符号。")
    return " ".join(relevant)


def normalize_search_concepts(value: object | None) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MemoryError("validation_error", "search_concepts 必须是“概念: 同义词数组”的对象。")
    normalized = {}
    for concept, aliases in value.items():
        if not isinstance(concept, str) or not concept.strip() or not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise MemoryError("validation_error", "search_concepts 的概念名和同义词必须是非空字符串。")
        normalized[concept.strip()] = list(dict.fromkeys(alias.strip() for alias in aliases))
    return normalized


def concept_groups(configured: object | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    merged = {concept: list(aliases) for concept, aliases in BASE_CONCEPT_GROUPS.items()}
    for concept, aliases in normalize_search_concepts(configured).items():
        merged.setdefault(concept, []).extend(aliases)
    return tuple(
        (concept, tuple(dict.fromkeys((concept, *aliases))))
        for concept, aliases in sorted(merged.items())
    )


DEFAULT_CONCEPT_GROUPS = concept_groups()


def normalize(value: str) -> str:
    return normalize_text(value)


def technical(value: str) -> str:
    value = normalize(value)
    return ALIASES.get(value, value)


def _looks_like_bare_ternary(value: str) -> bool:
    questions = re.findall(r"\s+\?\s+", value)
    colons = re.findall(r"\s+:\s+", value)
    if not questions or len(questions) != len(colons):
        return False
    atoms = [part.strip() for part in re.split(r"\s+[?:]\s+", value)]
    if not atoms or any(not TERNARY_ATOM.fullmatch(atom) for atom in atoms):
        return False
    return len(questions) > 1 or any(len(atom) == 1 or any(character in atom for character in "._$") or any(character.isdigit() for character in atom) for atom in atoms)


def identifier_atoms(value: str) -> set[str]:
    atoms = set()
    compatible = unicodedata.normalize("NFKC", value)
    for match in ASCII_TOKEN.finditer(compatible):
        raw = match.group(0).strip(".-:/")
        if not raw:
            continue
        compact = identifier_key(technical(raw))
        if compact:
            atoms.add(compact)
        for member in re.split(r"::|[./]+", raw):
            compact = identifier_key(technical(member))
            if compact:
                atoms.add(compact)
            split_camel = CAMEL_WORD.sub(r"\1 \2", CAMEL_ACRONYM.sub(r"\1 \2", member))
            words = [identifier_key(word) for word in re.split(r"[_\W]+", split_camel) if identifier_key(word)]
            for start in range(len(words)):
                for end in range(start + 2, min(len(words), start + 4) + 1):
                    atoms.add("".join(words[start:end]))
    return atoms


def _identifier_forms(value: str) -> tuple[set[str], set[str]]:
    exact, parts = set(), set()
    compatible = unicodedata.normalize("NFKC", value)
    for match in ASCII_TOKEN.finditer(compatible):
        raw = match.group(0).strip(".-:/")
        if not raw:
            continue
        canonical = technical(raw)
        exact.add(canonical)
        compact = identifier_key(canonical)
        if len(compact) >= 2 or compact == "c":
            exact.add(compact)
            parts.add(compact)
        for member in re.split(r"::|[./:+#]+", raw):
            member = member.strip("_")
            if not member:
                continue
            member_canonical = technical(member)
            exact.add(member_canonical)
            member_compact = identifier_key(member_canonical)
            if len(member_compact) >= 2 or member_compact == "c":
                exact.add(member_compact)
            if len(member_canonical) >= 2 or member_canonical == "c":
                parts.add(member_canonical)
            split_camel = CAMEL_WORD.sub(r"\1 \2", CAMEL_ACRONYM.sub(r"\1 \2", member))
            words = [technical(word) for word in re.split(r"[_\W]+", split_camel) if word]
            parts.update(word for word in words if (len(word) >= 2 or word == "c") and (word != "if" or technical(member) == "if"))
            joined = "".join(identifier_key(word) for word in words)
            if len(joined) >= 2:
                parts.add(joined)
    if "#include" in normalize(value):
        exact.add("#include")
        parts.add("include")
    code_markers = ("=", ";", "(", ")", "->", "::", '"', "'")
    if "?:" in compatible or _looks_like_bare_ternary(compatible) or (QUESTION_COLON.search(compatible) and "://" not in compatible and any(marker in compatible for marker in code_markers)):
        exact.add("?:")
    if normalize(value) != "c":
        exact.discard("c")
        parts.discard("c")
    return exact, parts


@dataclass(frozen=True)
class TextAnalysis:
    exact: frozenset[str]
    identifiers: frozenset[str]
    concepts: frozenset[str]
    chinese: tuple[str, ...]

    @property
    def all_terms(self) -> set[str]:
        return set(self.exact) | set(self.identifiers) | set(self.concepts)


@lru_cache(maxsize=4096)
def analyze(value: str, groups: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CONCEPT_GROUPS) -> TextAnalysis:
    exact, identifiers = _identifier_forms(value)
    chinese = tuple(CHINESE_SPAN.findall(normalize(value)))
    exact.update(chinese)
    normalized = normalize(value)
    available_keys = {identifier_key(term) for term in exact | identifiers if identifier_key(term)}
    concepts = set()
    for concept, aliases in groups:
        for alias in aliases:
            alias_normalized = normalize(alias)
            if CHINESE.search(alias_normalized):
                found = alias_normalized in normalized
            elif " " in alias_normalized:
                found = alias_normalized in normalized
            else:
                found = identifier_key(alias_normalized) in available_keys
            if found:
                concepts.add(concept)
                break
    return TextAnalysis(frozenset(exact), frozenset(identifiers), frozenset(concepts), chinese)


def tokens(value: str, groups: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CONCEPT_GROUPS) -> set[str]:
    return analyze(value, groups).all_terms


def _meaningful(term: str) -> bool:
    return term not in COMMON_TERMS and (len(term) >= 2 or term in {"c", "if", "op", "?:"})


def _ngrams(spans: tuple[str, ...], size: int = 2) -> set[str]:
    return {span[index : index + size] for span in spans for index in range(len(span) - size + 1)}


def _evidence_symbols(value: str) -> str:
    compatible = unicodedata.normalize("NFKC", value)
    symbols = []
    for match in ASCII_TOKEN.finditer(compatible):
        token = match.group(0).strip(".-:/")
        if "_" in token or "::" in token or re.search(r"[a-z0-9][A-Z]", token):
            symbols.append(token)
    return " ".join(symbols)


def _rule_chinese_grams(rule: dict) -> set[str]:
    values = [
        rule["title"],
        *rule["trigger"]["keywords"],
        *rule["tags"],
        rule["trigger"]["description"],
        rule["instruction"]["do"],
        rule["instruction"].get("avoid", ""),
        rule.get("rationale", {}).get("problem", ""),
        rule.get("rationale", {}).get("root_cause", ""),
        *(item["condition"] for item in rule.get("exceptions", [])),
        *(item["requirement"] for item in rule.get("exceptions", [])),
    ]
    return _ngrams(tuple(span for value in values for span in CHINESE_SPAN.findall(normalize(value))))


@lru_cache(maxsize=1)
def _common_chinese_grams() -> set[str]:
    return _ngrams(tuple(term for term in COMMON_TERMS if CHINESE.search(term)))


def _semantic_fallback(matched: Match, query_grams: set[str], rule_grams: set[str], frequencies: Counter, document_count: int) -> Match:
    if matched.score >= MIN_RELEVANCE or not query_grams:
        return matched
    maximum_frequency = max(2, round(document_count * 0.35))
    overlap = sorted((query_grams & rule_grams) - _common_chinese_grams() - set(matched.terms))
    overlap = [gram for gram in overlap if frequencies[gram] <= maximum_frequency]
    if len(overlap) < 4 or len(overlap) / len(query_grams) < 0.12:
        return matched
    points = sum(12 if frequencies[gram] == 1 else 10 if frequencies[gram] == 2 else 8 for gram in overlap)
    terms = tuple(sorted({*matched.terms, *overlap[:8]}))
    return Match(terms, matched.score + points)


@dataclass(frozen=True)
class Match:
    terms: tuple[str, ...]
    score: int


def _field_matches(query: TextAnalysis, query_text: str, value: str, weight: int, high_signal: bool, groups: tuple[tuple[str, tuple[str, ...]], ...]) -> dict[str, int]:
    field = analyze(value, groups)
    points: dict[str, int] = {}

    def add(term: str, value_points: int) -> None:
        if _meaningful(term):
            points[term] = max(points.get(term, 0), value_points)

    # 概念别名只提供组合证据，不能让“参数”“换行”等单个泛概念独立越过
    # 相关性阈值。精确关键字和标识符仍由下面的高权重分支负责。
    for term in query.concepts & field.concepts:
        add(term, weight * 2)
    for term in query.exact & field.exact:
        add(term, weight * 6)
    # 概念词已经在上面按“组合证据”计分，不能再次以普通词项叠加。
    for term in (set(query.exact) & set(field.identifiers)) | (set(query.identifiers) & set(field.exact)):
        add(term, weight * 2)
    for term in query.identifiers & field.identifiers:
        add(term, weight * 2)

    normalized_query, normalized_field = normalize(query_text), normalize(value)
    for phrase in field.chinese:
        if len(phrase) >= 2 and phrase in normalized_query:
            add(phrase, weight * 6)
    for phrase in query.chinese:
        if len(phrase) >= 2 and phrase in normalized_field and (high_signal or len(phrase) >= 4):
            add(phrase, weight * 6 + (12 if not high_signal and len(phrase) >= 4 else 0))

    if high_signal:
        query_bigrams = _ngrams(query.chinese)
        for phrase in field.chinese:
            if len(phrase) < 4:
                continue
            field_bigrams = _ngrams((phrase,))
            overlap = query_bigrams & field_bigrams
            if len(overlap) >= 3 and len(overlap) / len(field_bigrams) >= 0.75:
                add(phrase, weight * 2 * len(overlap))
    return points


def match(rule: dict, query_text: str, groups: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CONCEPT_GROUPS) -> Match:
    query = analyze(query_text, groups)
    fields = [
        (rule["title"], 10, True),
        *((value, 12, True) for value in rule["trigger"]["keywords"]),
        *((value, 8, True) for value in rule["tags"]),
        (rule["trigger"]["description"], 6, False),
        (rule["instruction"]["do"], 4, False),
        (rule["instruction"].get("avoid", ""), 4, False),
        (rule.get("rationale", {}).get("problem", ""), 3, False),
        (rule.get("rationale", {}).get("root_cause", ""), 3, False),
        *((item["condition"], 2, False) for item in rule.get("exceptions", [])),
        *((item["requirement"], 2, False) for item in rule.get("exceptions", [])),
        *((path.replace("\\", "/").rsplit("/", 1)[-1], 5, False) for evidence in rule["evidence"] for path in evidence.get("files", [])),
        *((_evidence_symbols(evidence["summary"]), 5, False) for evidence in rule["evidence"]),
    ]
    evidence: dict[str, int] = {}
    for value, weight, high_signal in fields:
        for term, term_points in _field_matches(query, query_text, value, weight, high_signal, groups).items():
            evidence[term] = max(evidence.get(term, 0), term_points)
    exact_scope_query = technical(query_text)
    for value in (*rule["scope"]["technologies"], *rule["scope"]["languages"]):
        if exact_scope_query == technical(value):
            evidence[technical(value)] = max(evidence.get(technical(value), 0), 30)
    return Match(tuple(sorted(evidence)), sum(evidence.values()))


def match_terms(rule: dict, query: str, groups: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_CONCEPT_GROUPS) -> list[str]:
    return list(match(rule, query, groups).terms)


@dataclass(frozen=True)
class SearchRequest:
    query: str
    project: str | None = None
    modules: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    limit: int = 12
    max_chars: int = 32000
    include_candidates: bool = False
    features: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    compact: bool = False

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


def search(records: list[Record], request: SearchRequest, root: Path, routing_warnings: list[str] | None = None, configured_concepts: object | None = None) -> dict:
    groups = concept_groups(configured_concepts)
    candidates = []
    for record in records:
        rule = record.rule
        if rule["status"] != "active" and not (request.include_candidates and rule["status"] == "candidate"):
            continue
        applies, known = request.scope(rule)
        if not applies:
            continue
        candidates.append((record, known))
    query_grams = _ngrams(tuple(CHINESE_SPAN.findall(normalize(request.query))))
    grams_by_id = {record.rule["id"]: _rule_chinese_grams(record.rule) for record, _ in candidates} if query_grams else {}
    frequencies = Counter(gram for grams in grams_by_id.values() for gram in grams)

    ranked = []
    for record, known in candidates:
        rule = record.rule
        matched = match(rule, request.query, groups)
        if query_grams:
            matched = _semantic_fallback(matched, query_grams, grams_by_id[rule["id"]], frequencies, len(candidates))
        topical = matched.score >= MIN_RELEVANCE
        feature_match = bool(request.features and rule["scope"].get("features"))
        required = rule["status"] == "active" and rule["trigger"].get("always_apply", False) and known
        if topical or required or feature_match:
            reasons = [name for name, selected in (("required", required), ("feature_scope", feature_match), ("lexical", topical)) if selected]
            ranked.append((record, matched, required, feature_match, topical, reasons))
    ranked.sort(key=lambda item: (
        not item[2],
        item[0].rule["status"] != "active",
        not item[4],  # Direct matches take precedence over feature-only previews.
        not item[3],
        -item[1].score,
        -SCOPE_ORDER[item[0].rule["scope"]["level"]],
        -PRIORITY[item[0].rule["priority"]],
        -item[0].rule["confidence"],
        item[0].rule["id"],
    ))
    result = {
        "memory_root": str(root),
        "score_version": "lexical-v3",
        "rules": [],
        "matched_count": len(ranked),
        "topical_matched_count": sum(item[4] for item in ranked),
        "feature_matched_count": sum(item[3] for item in ranked),
        "required_count": sum(item[2] for item in ranked),
        "omitted_count": len(ranked),
        "warnings": [],
    }
    if request.features or request.paths:
        result["context"] = {"project": request.project, "modules": list(request.modules), "features": list(request.features), "paths": list(request.paths), "languages": list(request.languages)}

    def fits() -> bool:
        warnings = [*(routing_warnings or [])]
        if result["omitted_count"]:
            warnings.append(OMITTED)
        if result["required_count"] and not result["topical_matched_count"] and not result["feature_matched_count"]:
            warnings.append(TOPIC_MISS)
        result["warnings"] = warnings
        return len(json.dumps(result, ensure_ascii=False, indent=2)) + 1 <= request.max_chars

    if not fits():
        raise MemoryError("budget_exceeded", "输出元数据超出预算，请增大 --max-chars。")
    ordinary = 0
    for record, matched, required, feature_match, topical, reasons in ranked:
        if not required and ordinary >= request.limit:
            continue
        rule = record.rule
        if request.compact:
            entry = {
                "id": rule["id"],
                "title": rule["title"],
                "when": rule["trigger"]["description"],
                "selection_reason": reasons,
            }
            if required or topical:
                entry["scope"] = {key: value for key, value in rule["scope"].items() if value}
                entry["instruction"] = rule["instruction"]
                if required:
                    entry["required"] = True
                if rule.get("exceptions"):
                    entry["exceptions"] = rule["exceptions"]
                relations = {key: value for key, value in rule["relations"].items() if value}
                if relations:
                    entry["relations"] = relations
            else:
                entry["preview"] = True
            if rule["status"] != "active":
                entry["status"] = rule["status"]
        else:
            entry = {key: rule[key] for key in ("id", "title", "status", "priority", "scope", "trigger", "instruction", "relations")}
            entry.update(
                exceptions=rule.get("exceptions", []),
                revision=record.revision,
                path=record.path.relative_to(root).as_posix(),
                matched_terms=list(matched.terms),
                selection_reason=reasons,
                required=required,
                score=matched.score,
            )
        if feature_match:
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
