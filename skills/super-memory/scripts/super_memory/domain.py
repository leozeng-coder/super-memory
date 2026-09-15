from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
RULE_ID = re.compile(r"^RULE-[A-Z0-9][A-Z0-9-]*$")
CHINESE = re.compile(r"[\u3400-\u9fff]")
STATUSES = ("candidate", "active", "suspect", "deprecated", "superseded", "archived")


class MemoryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def revision(rule: dict) -> str:
    canonical = json.dumps(rule, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Record:
    rule: dict
    path: Path

    @property
    def revision(self) -> str:
        return revision(self.rule)

    def public(self) -> dict:
        return {"rule": self.rule, "revision": self.revision}


@lru_cache(maxsize=1)
def rule_schema() -> dict:
    return json.loads((SKILL_ROOT / "assets" / "memory-rule.schema.json").read_text(encoding="utf-8"))


def schema_errors(value: Any, schema: dict, location: str = "rule") -> list[str]:
    """执行本项目 Schema 使用的类型、约束和条件，规则定义只维护一份。"""
    errors = []
    types = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "number": lambda x: type(x) is int or (type(x) is float and math.isfinite(x)),
        "integer": lambda x: type(x) is int,
        "boolean": lambda x: type(x) is bool,
        "null": lambda x: x is None,
    }
    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(types[name](value) for name in allowed):
            return [f"{location}：类型应为 {' / '.join(allowed)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}：值必须为 {schema['const']}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}：值不在允许范围内")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{location}：缺少字段 {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(f"{location}：未定义字段 {key}" for key in value.keys() - properties.keys())
        for key, child in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], child, f"{location}.{key}"))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{location}：数组长度不符合要求")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{location}：不得包含重复项")
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema.get("items", {}), f"{location}[{index}]"))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{location}：内容不能为空")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{location}：格式不符合要求")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
                if parsed.tzinfo is None or "T" not in value:
                    raise ValueError()
            except ValueError:
                errors.append(f"{location}：必须是带时区的 ISO 时间")
    if type(value) in (int, float):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{location}：数值超出允许范围")
    for child in schema.get("allOf", []):
        errors.extend(schema_errors(value, child, location))
    if "if" in schema and not schema_errors(value, schema["if"], location):
        errors.extend(schema_errors(value, schema.get("then", {}), location))
    return errors


def validate_rule(rule: Any) -> None:
    errors = schema_errors(rule, rule_schema())
    if not errors:
        ids = [item["id"] for item in rule["evidence"]]
        if len(ids) != len(set(ids)):
            errors.append("rule.evidence：证据 ID 不得重复")
        scope = rule["scope"]
        if scope["level"] == "project" and not scope["projects"]:
            errors.append("rule.scope：项目规则必须指定项目")
        if scope["level"] == "module" and not scope["modules"]:
            errors.append("rule.scope：模块规则必须指定模块")
        features = scope.get("features", [])
        if features and (not scope["projects"] or not scope["modules"] or scope["level"] != "feature"):
            errors.append("rule.scope：功能规则必须指定项目、模块，并使用 feature 层级")
        if scope["level"] == "feature" and not features:
            errors.append("rule.scope：功能规则必须指定功能")
    if errors:
        raise MemoryError("validation_error", "\n".join(errors))


def require_reason(reason: str) -> None:
    if not isinstance(reason, str) or not CHINESE.search(reason):
        raise MemoryError("validation_error", "操作原因必须包含中文。")


def require_revision(record: Record, expected: str) -> None:
    if expected != record.revision:
        raise MemoryError("revision_conflict", "规则已被其他操作修改，请重新读取、合并后重试。")


def prepare_rule(incoming: Any, existing: Record | None = None) -> dict:
    validate_rule(incoming)
    rule = copy.deepcopy(incoming)
    if existing:
        if rule["id"] != existing.rule["id"]:
            raise MemoryError("validation_error", "规则 ID 不可修改。")
        rule["lifecycle"]["created_at"] = existing.rule["lifecycle"]["created_at"]
        evidence = copy.deepcopy(existing.rule["evidence"])
        by_id = {item["id"]: item for item in evidence}
        for item in rule["evidence"]:
            if item["id"] in by_id and by_id[item["id"]] != item:
                raise MemoryError("evidence_conflict", f"已有证据 {item['id']} 不可覆盖，请用新 ID 追加修正说明。")
            if item["id"] not in by_id:
                evidence.append(item)
                by_id[item["id"]] = item
        rule["evidence"] = evidence
    rule["lifecycle"]["updated_at"] = now()
    return rule


def normalize_scopes(scopes: Any) -> list:
    if not isinstance(scopes, list):
        raise MemoryError("validation_error", "项目作用域必须是数组。")
    result = copy.deepcopy(scopes)
    def normalize(items: list, kind: str) -> None:
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise MemoryError("validation_error", f"{kind} ID 无效。")
            item["id"] = item["id"].strip()
            if item["id"] in seen:
                raise MemoryError("validation_error", f"{kind} ID 重复：{item['id']}")
            seen.add(item["id"])
            item["label"] = str(item.get("label") or item["id"]).strip()
            item["created_at"] = item.get("created_at") or now()
            item["updated_at"] = item.get("updated_at") or item["created_at"]
    normalize(result, "项目")
    for project in result:
        modules = project.setdefault("modules", [])
        if not isinstance(modules, list):
            raise MemoryError("validation_error", "模块必须是数组。")
        normalize(modules, "模块")
        for module in modules:
            features = module.setdefault("features", [])
            if not isinstance(features, list):
                raise MemoryError("validation_error", "功能必须是数组。")
            normalize(features, "功能")
            names = set()
            for feature in features:
                for field in ("aliases", "paths"):
                    values = feature.setdefault(field, [])
                    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                        raise MemoryError("validation_error", f"功能 {field} 必须是非空字符串数组。")
                    feature[field] = list(dict.fromkeys(value.strip() for value in values))
                identifiers = {feature["id"].casefold(), *(value.casefold() for value in feature["aliases"])}
                if names & identifiers:
                    raise MemoryError("validation_error", "同一模块内的功能名称或别名存在歧义。")
                names.update(identifiers)
                for pattern in feature["paths"]:
                    if pattern.startswith(("/", "\\")) or ":" in pattern or ".." in pattern.replace("\\", "/").split("/"):
                        raise MemoryError("validation_error", "功能路径须为项目内的相对路径或通配模式。")
    return result
