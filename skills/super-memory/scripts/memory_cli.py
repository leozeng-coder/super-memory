#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATUSES = {
    "candidate",
    "active",
    "suspect",
    "deprecated",
    "superseded",
    "archived",
}
REQUIRED_FIELDS = {
    "id",
    "type",
    "title",
    "status",
    "priority",
    "confidence",
    "scope",
    "trigger",
    "instruction",
    "rationale",
    "exceptions",
    "relations",
    "evidence",
    "lifecycle",
    "tags",
}
ID_PATTERN = re.compile(r"^RULE-[A-Z0-9][A-Z0-9-]*$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
RULE_DIRECTORIES = ("active", "candidate", "archive")
SAVE_STATUSES = {"active", "candidate"}
PRIORITIES = {"low", "medium", "high", "critical"}
SCOPE_LEVELS = {"global", "language", "technology", "project", "module"}
EVIDENCE_SOURCES = {
    "human-code-review",
    "git-diff",
    "test-failure",
    "incident",
    "user-instruction",
}
MEMORY_HOME_ENV = "SUPER_MEMORY_HOME"
MEMORY_CONFIG_TEMPLATE = {
    "schema_version": 1,
    "default_rule_status": "candidate",
    "activation_requires_confirmation": True,
    "scope_hierarchy": [],
    "source_projects": [],
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def resolve_memory_root(explicit_root: Path | None) -> Path:
    if explicit_root is not None:
        return explicit_root.expanduser().resolve()

    configured_root = os.environ.get(MEMORY_HOME_ENV)
    if configured_root:
        return Path(configured_root).expanduser().resolve()

    source_checkout_root = Path(__file__).resolve().parents[3]
    if (source_checkout_root / "memory.config.json").is_file():
        return source_checkout_root
    return (Path.home() / ".super-memory").resolve()


def command_init(memory_root: Path, force: bool) -> int:
    directories = (
        memory_root / "graph" / "rules" / "active",
        memory_root / "graph" / "rules" / "candidate",
        memory_root / "graph" / "rules" / "archive",
        memory_root / "graph" / "rules" / "trash",
        memory_root / "history",
        memory_root / "indexes",
        memory_root / "schemas",
    )
    try:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        config_path = memory_root / "memory.config.json"
        if force or not config_path.exists():
            write_json(config_path, MEMORY_CONFIG_TEMPLATE)

        schema_source = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "memory-rule.schema.json"
        )
        schema_path = memory_root / "schemas" / "memory-rule.schema.json"
        if force or not schema_path.exists():
            write_bytes(schema_path, schema_source.read_bytes())

        history_path = memory_root / "history" / "events.jsonl"
        if not history_path.exists():
            write_bytes(history_path, b"")

        index_path = memory_root / "indexes" / "rules.json"
        if not index_path.exists():
            write_json(
                index_path,
                {
                    "schema_version": 1,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "rules": [],
                },
            )
    except OSError as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 1

    mode = "已更新模板" if force else "已保留现有文件"
    print(f"初始化完成：{memory_root}（{mode}，规则未被删除）")
    return 0


def rule_paths(memory_root: Path) -> list[Path]:
    rules_root = memory_root / "graph" / "rules"
    return sorted(
        path
        for directory in RULE_DIRECTORIES
        for path in (rules_root / directory).glob("*.json")
    )


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def is_datetime_string(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, str):
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_string_list(
    value: Any,
    path: Path,
    field_name: str,
    *,
    require_item: bool = False,
    maximum_items: int | None = None,
    unique_items: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        return [f"{path}：{field_name} 必须是字符串数组"]
    errors: list[str] = []
    if not all(isinstance(item, str) for item in value):
        errors.append(f"{path}：{field_name} 必须只包含字符串")
    elif unique_items and len(value) != len(set(value)):
        errors.append(f"{path}：{field_name} 不得包含重复项")
    if require_item and not value:
        errors.append(f"{path}：{field_name} 至少需要一项")
    if maximum_items is not None and len(value) > maximum_items:
        errors.append(f"{path}：{field_name} 最多允许 {maximum_items} 项")
    return errors


def validate_rule(rule: Any, path: Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(rule, dict):
        return [f"{path}：根节点必须是对象"]

    missing = sorted(REQUIRED_FIELDS - rule.keys())
    if missing:
        errors.append(f"{path}：缺少字段：{', '.join(missing)}")
    additional = sorted(rule.keys() - REQUIRED_FIELDS)
    if additional:
        errors.append(f"{path}：包含未定义字段：{', '.join(additional)}")

    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not ID_PATTERN.fullmatch(rule_id):
        errors.append(f"{path}：规则 ID 格式无效")
    if rule.get("type") != "Rule":
        errors.append(f"{path}：type 必须为 Rule")
    if rule.get("status") not in VALID_STATUSES:
        errors.append(f"{path}：status 状态值无效")
    if not is_non_empty_string(rule.get("title")):
        errors.append(f"{path}：title 必须是非空字符串")
    if rule.get("priority") not in PRIORITIES:
        errors.append(f"{path}：priority 优先级无效")

    confidence = rule.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        errors.append(f"{path}：confidence 必须在 0 到 1 之间")

    scope = rule.get("scope")
    if not isinstance(scope, dict):
        errors.append(f"{path}：scope 必须是对象")
    else:
        if scope.get("level") not in SCOPE_LEVELS:
            errors.append(f"{path}：scope.level 作用域级别无效")
        for field in ("languages", "technologies"):
            errors.extend(
                validate_string_list(scope.get(field), path, f"scope.{field}")
            )
        for field in ("projects", "modules"):
            errors.extend(
                validate_string_list(
                    scope.get(field),
                    path,
                    f"scope.{field}",
                    maximum_items=1,
                )
            )
        projects = scope.get("projects")
        modules = scope.get("modules")
        if isinstance(modules, list) and modules:
            if not isinstance(projects, list) or not projects:
                errors.append(f"{path}：scope.modules 必须归属于 scope.projects")
            if scope.get("level") != "module":
                errors.append(f"{path}：包含模块时 scope.level 必须为 module")
        elif isinstance(projects, list) and projects and scope.get("level") != "project":
            errors.append(f"{path}：仅包含项目时 scope.level 必须为 project")

    trigger = rule.get("trigger")
    if not isinstance(trigger, dict):
        errors.append(f"{path}：trigger 必须是对象")
    else:
        if not is_non_empty_string(trigger.get("description")):
            errors.append(f"{path}：trigger.description 必须是非空字符串")
        errors.extend(
            validate_string_list(
                trigger.get("keywords"),
                path,
                "trigger.keywords",
                require_item=True,
            )
        )

    instruction = rule.get("instruction")
    if not isinstance(instruction, dict):
        errors.append(f"{path}：instruction 必须是对象")
    else:
        for field in ("do", "avoid"):
            if not is_non_empty_string(instruction.get(field)):
                errors.append(
                    f"{path}：instruction.{field} 必须是非空字符串"
                )

    rationale = rule.get("rationale")
    if not isinstance(rationale, dict):
        errors.append(f"{path}：rationale 必须是对象")
    else:
        for field in ("problem", "root_cause"):
            if not is_non_empty_string(rationale.get(field)):
                errors.append(f"{path}：rationale.{field} 必须是非空字符串")

    exceptions = rule.get("exceptions")
    if not isinstance(exceptions, list):
        errors.append(f"{path}：exceptions 必须是数组")
    else:
        for index, exception in enumerate(exceptions):
            if not isinstance(exception, dict):
                errors.append(f"{path}：exceptions[{index}] 必须是对象")
                continue
            for field in ("condition", "requirement"):
                if not isinstance(exception.get(field), str):
                    errors.append(
                        f"{path}：exceptions[{index}].{field} 必须是字符串"
                    )

    relations = rule.get("relations")
    if not isinstance(relations, dict):
        errors.append(f"{path}：relations 必须是对象")
    else:
        for field in ("conflicts_with", "supersedes"):
            if field in relations:
                errors.extend(
                    validate_string_list(
                        relations.get(field),
                        path,
                        f"relations.{field}",
                        unique_items=False,
                    )
                )
        replacement = relations.get("replacement")
        if replacement is not None and not isinstance(replacement, str):
            errors.append(f"{path}：relations.replacement 必须是字符串或 null")

    evidence = rule.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{path}：至少需要一条证据")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"{path}：evidence[{index}] 必须是对象")
                continue
            if not isinstance(item.get("id"), str):
                errors.append(f"{path}：evidence[{index}].id 必须是字符串")
            if item.get("source") not in EVIDENCE_SOURCES:
                errors.append(f"{path}：evidence[{index}].source 来源无效")
            if not is_non_empty_string(item.get("summary")):
                errors.append(
                    f"{path}：evidence[{index}].summary 必须是非空字符串"
                )
            if not is_datetime_string(item.get("observed_at")):
                errors.append(
                    f"{path}：evidence[{index}].observed_at 必须是带时区的时间"
                )
            for field in ("project", "commit"):
                value = item.get(field)
                if value is not None and not isinstance(value, str):
                    errors.append(
                        f"{path}：evidence[{index}].{field} 必须是字符串或 null"
                    )
            if "files" in item:
                errors.extend(
                    validate_string_list(
                        item.get("files"),
                        path,
                        f"evidence[{index}].files",
                        unique_items=False,
                    )
                )

    lifecycle = rule.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append(f"{path}：lifecycle 必须是对象")
    else:
        if not is_datetime_string(lifecycle.get("created_at")):
            errors.append(f"{path}：lifecycle.created_at 必须是带时区的时间")
        for field in ("last_validated_at", "review_after"):
            if field not in lifecycle:
                errors.append(f"{path}：lifecycle 缺少字段 {field}")
        for field in ("updated_at", "last_validated_at", "review_after"):
            if not is_datetime_string(lifecycle.get(field), nullable=True):
                errors.append(
                    f"{path}：lifecycle.{field} 必须是带时区的时间或 null"
                )
        for field in ("success_count", "violation_count", "exception_count"):
            if field not in lifecycle:
                continue
            value = lifecycle.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(f"{path}：lifecycle.{field} 必须是非负整数")

    errors.extend(validate_string_list(rule.get("tags"), path, "tags"))

    return errors


def load_valid_rules(memory_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rules: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_ids: dict[str, Path] = {}

    for path in rule_paths(memory_root):
        try:
            rule = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path}：读取失败：{exc}")
            continue

        current_errors = validate_rule(rule, path)
        errors.extend(current_errors)
        if current_errors:
            continue

        rule_id = rule["id"]
        if rule_id in seen_ids:
            errors.append(f"{path}：规则 ID 重复，已被 {seen_ids[rule_id]} 使用")
            continue
        seen_ids[rule_id] = path
        rule["_path"] = path.relative_to(memory_root).as_posix()
        rules.append(rule)

    return rules, errors


def command_validate(memory_root: Path) -> int:
    rules, errors = load_valid_rules(memory_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"校验失败：共 {len(errors)} 个错误。", file=sys.stderr)
        return 1
    print(f"校验通过：共 {len(rules)} 条规则。")
    return 0


def index_entry(rule: dict[str, Any]) -> dict[str, Any]:
    scope = rule.get("scope", {})
    trigger = rule.get("trigger", {})
    return {
        "id": rule["id"],
        "title": rule["title"],
        "status": rule["status"],
        "priority": rule["priority"],
        "confidence": rule["confidence"],
        "scope_level": scope.get("level"),
        "languages": scope.get("languages", []),
        "technologies": scope.get("technologies", []),
        "projects": scope.get("projects", []),
        "modules": scope.get("modules", []),
        "keywords": trigger.get("keywords", []),
        "tags": rule.get("tags", []),
        "path": rule["_path"],
    }


def build_index(memory_root: Path) -> dict[str, Any]:
    rules, errors = load_valid_rules(memory_root)
    if errors:
        raise ValueError("\n".join(errors))

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules": [index_entry(rule) for rule in rules],
    }


def command_reindex(memory_root: Path) -> int:
    try:
        payload = build_index(memory_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        print("规则校验失败，索引未发生变化。", file=sys.stderr)
        return 1

    index_path = memory_root / "indexes" / "rules.json"
    write_json(index_path, payload)
    print(f"索引重建完成：共写入 {len(payload['rules'])} 条规则，位置：{index_path}")
    return 0


def restore_files(snapshots: dict[Path, bytes | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            write_bytes(path, content)


def append_event(path: Path, event: dict[str, Any]) -> None:
    previous = path.read_bytes() if path.exists() else b""
    separator = b"" if not previous or previous.endswith(b"\n") else b"\n"
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    write_bytes(path, previous + separator + line + b"\n")


def command_save(
    memory_root: Path,
    input_path: Path,
    status_override: str | None,
    actor: str,
    reason: str,
) -> int:
    if not CHINESE_PATTERN.search(reason):
        print("--reason 必须填写包含中文的保存原因。", file=sys.stderr)
        return 2

    try:
        incoming = load_json(input_path.resolve())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入规则读取失败：{exc}", file=sys.stderr)
        return 1
    if not isinstance(incoming, dict):
        print("输入规则的根节点必须是对象。", file=sys.stderr)
        return 1

    rule = dict(incoming)
    if status_override:
        rule["status"] = status_override
    status = rule.get("status")
    if status not in SAVE_STATUSES:
        print("save 仅支持 active 或 candidate 状态。", file=sys.stderr)
        return 1

    current_rules, errors = load_valid_rules(memory_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print("现有规则库校验失败，保存未执行。", file=sys.stderr)
        return 1

    rule_id = rule.get("id")
    existing = next((item for item in current_rules if item["id"] == rule_id), None)
    now = datetime.now(timezone.utc).isoformat()
    lifecycle = rule.get("lifecycle")
    if not isinstance(lifecycle, dict):
        print("输入规则必须提供 lifecycle 对象。", file=sys.stderr)
        return 1
    lifecycle = dict(lifecycle)
    if existing:
        existing_lifecycle = existing.get("lifecycle", {})
        lifecycle["created_at"] = existing_lifecycle.get("created_at", lifecycle.get("created_at", now))
    else:
        lifecycle["created_at"] = lifecycle.get("created_at") or now
    lifecycle["updated_at"] = now
    lifecycle["last_validated_at"] = now
    rule["lifecycle"] = lifecycle

    destination = memory_root / "graph" / "rules" / status / f"{rule_id}.json"
    candidate_errors = validate_rule(rule, destination)
    if candidate_errors:
        for error in candidate_errors:
            print(error, file=sys.stderr)
        print("输入规则校验失败，保存未执行。", file=sys.stderr)
        return 1

    existing_path = memory_root / existing["_path"] if existing else None
    if destination.exists() and destination != existing_path:
        try:
            occupied = load_json(destination)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"目标文件已存在且无法读取：{exc}", file=sys.stderr)
            return 1
        if occupied.get("id") != rule_id:
            print(f"目标文件已被其他规则占用：{destination}", file=sys.stderr)
            return 1

    planned_ids = [item["id"] for item in current_rules if item["id"] != rule_id]
    planned_ids.append(rule_id)
    if len(planned_ids) != len(set(planned_ids)):
        print("规则 ID 重复，保存未执行。", file=sys.stderr)
        return 1

    index_path = memory_root / "indexes" / "rules.json"
    history_path = memory_root / "history" / "events.jsonl"
    touched = {destination, index_path, history_path}
    if existing_path:
        touched.add(existing_path)
    snapshots = {path: path.read_bytes() if path.exists() else None for path in touched}

    old_status = existing.get("status") if existing else None
    if not existing:
        action = "activated" if status == "active" else "created"
    elif old_status != status:
        action = "activated" if status == "active" else "moved"
    else:
        action = "updated"
    event = {
        "event_id": f"EVT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{rule_id}",
        "time": now,
        "action": action,
        "node_id": rule_id,
        "actor": actor,
        "reason": reason,
        "summary": f"通过统一记忆入口将规则保存为 {status}",
    }

    try:
        write_json(destination, rule)
        if existing_path and existing_path != destination:
            existing_path.unlink()
        payload = build_index(memory_root)
        write_json(index_path, payload)
        append_event(history_path, event)
    except Exception as exc:
        try:
            restore_files(snapshots)
        except Exception as rollback_exc:
            print(f"保存失败且回滚异常：{rollback_exc}", file=sys.stderr)
        print(f"保存失败，已回滚：{exc}", file=sys.stderr)
        return 1

    print(f"规则保存完成：{rule_id} -> {status}；索引共 {len(payload['rules'])} 条规则。")
    return 0


def tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w.+#-]+", value.lower()) if len(token) > 1}


def score_entry(entry: dict[str, Any], query_tokens: set[str]) -> float:
    priority_weight = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    fields = (
        entry.get("title", ""),
        *entry.get("languages", []),
        *entry.get("technologies", []),
        *entry.get("projects", []),
        *entry.get("modules", []),
        *entry.get("keywords", []),
        *entry.get("tags", []),
    )
    entry_tokens = tokenize(" ".join(str(value) for value in fields))
    overlap = len(query_tokens & entry_tokens)
    return (
        overlap * 10
        + priority_weight.get(entry.get("priority"), 0)
        + float(entry.get("confidence", 0))
    )


def command_retrieve(memory_root: Path, query: str, limit: int) -> int:
    rules, errors = load_valid_rules(memory_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print("规则校验失败，检索未执行。", file=sys.stderr)
        return 1

    query_tokens = tokenize(query)
    ranked = []
    for rule in rules:
        entry = index_entry(rule)
        if entry.get("status") != "active":
            continue
        score = score_entry(entry, query_tokens)
        if score >= 10:
            ranked.append((score, entry))

    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    output = [
        {
            "score": round(score, 2),
            "id": entry["id"],
            "title": entry["title"],
            "path": entry["path"],
        }
        for score, entry in ranked[:limit]
    ]
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化、保存、校验、索引和检索记忆规则。")
    parser.add_argument(
        "--memory-root",
        type=Path,
        help=(
            "记忆根目录；省略时依次读取 SUPER_MEMORY_HOME、识别含本地配置的"
            "源码检出目录，最后使用用户目录下的 .super-memory"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="初始化一个空的记忆库")
    init.add_argument(
        "--force",
        action="store_true",
        help="覆盖配置与规则 Schema 模板，但保留现有规则、索引和事件",
    )
    subparsers.add_parser("validate")
    subparsers.add_parser("reindex")
    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--query", required=True, help="中文或技术关键词组成的检索文本")
    retrieve.add_argument("--limit", type=int, default=8, help="最多返回的规则数量")
    save = subparsers.add_parser("save")
    save.add_argument("--input", required=True, type=Path, help="待保存的完整规则 JSON 文件")
    save.add_argument("--status", choices=sorted(SAVE_STATUSES), help="覆盖输入规则的状态")
    save.add_argument("--actor", default="user", help="执行保存的主体，默认为 user")
    save.add_argument("--reason", required=True, help="包含中文的保存原因")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    memory_root = resolve_memory_root(args.memory_root)
    if args.command == "init":
        return command_init(memory_root, args.force)
    if not (memory_root / "memory.config.json").exists():
        print(
            f"指定路径不是记忆根目录，请先运行 init：{memory_root}",
            file=sys.stderr,
        )
        return 2
    if args.command == "validate":
        return command_validate(memory_root)
    if args.command == "reindex":
        return command_reindex(memory_root)
    if args.command == "retrieve":
        return command_retrieve(memory_root, args.query, args.limit)
    return command_save(memory_root, args.input, args.status, args.actor, args.reason)


if __name__ == "__main__":
    raise SystemExit(main())
