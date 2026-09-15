#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
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
SAVE_STATUSES = VALID_STATUSES
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
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def memory_lock(memory_root: Path, timeout: float = 10):
    """所有命令共享进程锁；退出或崩溃时由操作系统释放。"""
    lock_path = memory_root / "history" / ".write.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("记忆库正被其他操作使用，请稍后重试。")
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    if not isinstance(rule.get("status"), str) or rule["status"] not in VALID_STATUSES:
        errors.append(f"{path}：status 状态值无效")
    if not is_non_empty_string(rule.get("title")):
        errors.append(f"{path}：title 必须是非空字符串")
    if not isinstance(rule.get("priority"), str) or rule["priority"] not in PRIORITIES:
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
        if not isinstance(scope.get("level"), str) or scope["level"] not in SCOPE_LEVELS:
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
        if "always_apply" in trigger and not isinstance(trigger["always_apply"], bool):
            errors.append(f"{path}：trigger.always_apply 必须是布尔值")
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
        evidence_ids: set[str] = set()
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"{path}：evidence[{index}] 必须是对象")
                continue
            evidence_id = item.get("id")
            if not is_non_empty_string(evidence_id):
                errors.append(f"{path}：evidence[{index}].id 必须是非空字符串")
            elif evidence_id in evidence_ids:
                errors.append(f"{path}：证据 ID 重复：{evidence_id}")
            else:
                evidence_ids.add(evidence_id)
            if not isinstance(item.get("source"), str) or item["source"] not in EVIDENCE_SOURCES:
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


def directory_for_status(status: str) -> str:
    if status == "active":
        return "active"
    return "archive" if status in {"archived", "superseded"} else "candidate"


def checked_rules(memory_root: Path) -> list[dict[str, Any]]:
    rules, errors = load_valid_rules(memory_root)
    if errors:
        raise ValueError("\n".join(errors))
    return rules


def public_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in rule.items() if key != "_path"}


def prepare_rule(incoming: Any, existing: dict[str, Any] | None, mode: str = "upsert", expected_updated_at: str | None = None) -> dict[str, Any]:
    errors = validate_rule(incoming, Path("输入规则"))
    if errors:
        raise ValueError("输入规则校验失败：\n" + "\n".join(errors))
    if mode == "create" and existing:
        raise ValueError("规则 ID 已存在")
    if mode == "update" and not existing:
        raise ValueError("找不到需要编辑的规则")
    if expected_updated_at is not None:
        if not existing:
            raise ValueError("规则已被删除，请刷新后重试。")
        actual = existing["lifecycle"].get("updated_at")
        expected = None if expected_updated_at == "null" else expected_updated_at
        if actual != expected:
            raise ValueError("规则已被其他操作修改，请刷新后重新审核修改内容。")

    rule = dict(incoming)
    lifecycle = dict(rule["lifecycle"])
    if existing:
        lifecycle["created_at"] = existing["lifecycle"]["created_at"]
        evidence = list(existing["evidence"])
        evidence_by_id = {item["id"]: item for item in evidence}
        for item in rule["evidence"]:
            previous = evidence_by_id.get(item["id"])
            if previous is not None and previous != item:
                raise ValueError(f"已有证据 {item['id']} 不可覆盖；请用新的证据 ID 追加修正说明。")
            if previous is None:
                evidence.append(item)
                evidence_by_id[item["id"]] = item
        rule["evidence"] = evidence
    lifecycle["updated_at"] = datetime.now(timezone.utc).isoformat()
    # 保存只表示结构校验通过，不代表规则经过实践验证。
    rule["lifecycle"] = lifecycle
    return rule


def make_event(rule_id: str, action: str, actor: str, reason: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "event_id": f"EVT-{now.strftime('%Y%m%d%H%M%S%f')}-{rule_id}",
        "time": now.isoformat(),
        "action": action,
        "node_id": rule_id,
        "actor": actor,
        "reason": reason,
    }


def commit_changes(memory_root: Path, changes: dict[Path, bytes | None], events: list[dict[str, Any]]) -> None:
    """调用者持有库锁；任一步骤报错时恢复规则、索引与事件。"""
    index_path = memory_root / "indexes" / "rules.json"
    history_path = memory_root / "history" / "events.jsonl"
    snapshots = {path: path.read_bytes() if path.exists() else None for path in {*changes, index_path, history_path}}
    try:
        for path, content in changes.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                write_bytes(path, content)
        write_json(index_path, build_index(memory_root))
        for event in events:
            append_event(history_path, event)
    except Exception as exc:
        try:
            restore_files(snapshots)
        except Exception as rollback_exc:
            raise RuntimeError(f"保存失败且回滚异常：{rollback_exc}；原始错误：{exc}") from exc
        raise RuntimeError(f"保存失败，已回滚：{exc}") from exc


def plan_save(memory_root: Path, rule: dict[str, Any], existing: dict[str, Any] | None, actor: str, reason: str) -> tuple[dict[Path, bytes | None], dict[str, Any]]:
    destination = memory_root / "graph" / "rules" / directory_for_status(rule["status"]) / f"{rule['id']}.json"
    existing_path = memory_root / existing["_path"] if existing else None
    if destination.exists() and destination != existing_path:
        raise ValueError(f"目标文件已被其他规则占用：{destination}")
    changes: dict[Path, bytes | None] = {}
    if existing_path and existing_path != destination:
        changes[existing_path] = None
    changes[destination] = (json.dumps(rule, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    old_status = existing["status"] if existing else None
    action = "created" if not existing else "updated"
    if old_status != rule["status"]:
        action = "activated" if rule["status"] == "active" else ("moved" if existing else "created")
    event = make_event(rule["id"], action, actor, reason)
    event.update({"old_status": old_status, "new_status": rule["status"]})
    return changes, event


def command_save(memory_root: Path, input_path: Path, status_override: str | None, actor: str, reason: str, mode: str = "upsert", expected_updated_at: str | None = None, json_output: bool = False) -> int:
    if not CHINESE_PATTERN.search(reason):
        raise ValueError("--reason 必须填写包含中文的保存原因。")
    incoming = load_json(input_path.resolve())
    if isinstance(incoming, dict) and status_override:
        incoming["status"] = status_override
    rules = checked_rules(memory_root)
    rule_id = incoming.get("id") if isinstance(incoming, dict) else None
    existing = next((rule for rule in rules if rule["id"] == rule_id), None)
    rule = prepare_rule(incoming, existing, mode, expected_updated_at)
    changes, event = plan_save(memory_root, rule, existing, actor, reason)
    commit_changes(memory_root, changes, [event])
    if json_output:
        print(json.dumps(rule, ensure_ascii=False))
    else:
        print(f"规则保存完成：{rule['id']} -> {rule['status']}")
    return 0


def command_delete(memory_root: Path, rule_id: str, reason: str) -> int:
    if not CHINESE_PATTERN.search(reason):
        raise ValueError("--reason 必须填写包含中文的删除原因。")
    existing = next((rule for rule in checked_rules(memory_root) if rule["id"] == rule_id), None)
    if not existing:
        raise ValueError("找不到需要删除的规则")
    source = memory_root / existing["_path"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    trash = memory_root / "graph" / "rules" / "trash" / f"{rule_id}-{stamp}.json"
    event = make_event(rule_id, "deleted-to-trash", "user", reason)
    event["trash_path"] = trash.relative_to(memory_root).as_posix()
    commit_changes(memory_root, {source: None, trash: source.read_bytes()}, [event])
    print(json.dumps({"ok": True}))
    return 0


def command_import(memory_root: Path, input_path: Path, overwrite: bool) -> int:
    bundle = load_json(input_path)
    if not isinstance(bundle, dict) or bundle.get("format") != "super-memory-export" or bundle.get("schema_version") != 1 or not isinstance(bundle.get("rules"), list):
        raise ValueError("不是有效的 Super Memory 导出文件")
    if len(bundle["rules"]) > 10000:
        raise ValueError("一次最多导入 10000 条规则")
    rules = {rule["id"]: rule for rule in checked_rules(memory_root)}
    changes: dict[Path, bytes | None] = {}
    events = []
    imported, skipped = [], []
    seen_ids: set[str] = set()
    for incoming in bundle["rules"]:
        errors = validate_rule(incoming, input_path)
        if errors:
            raise ValueError("\n".join(errors))
        rule_id = incoming["id"]
        if rule_id in seen_ids:
            raise ValueError(f"导入文件中的规则 ID 重复：{rule_id}")
        seen_ids.add(rule_id)
        existing = rules.get(rule_id)
        if existing and not overwrite:
            skipped.append(rule_id)
            continue
        rule = prepare_rule(incoming, existing)
        planned, event = plan_save(memory_root, rule, existing, "user", "通过管理台导入规则")
        changes.update(planned)
        events.append(event)
        imported.append(rule_id)
    scopes = bundle.get("scope_hierarchy", [])
    if not isinstance(scopes, list):
        raise ValueError("项目作用域必须是数组")
    for project in scopes:
        if not isinstance(project, dict) or not is_non_empty_string(project.get("id")) or not project["id"].strip() or not isinstance(project.get("modules", []), list):
            raise ValueError("导入文件包含无效的项目作用域")
        project["id"] = project["id"].strip()
        project["label"] = str(project.get("label") or project["id"]).strip()
        project["created_at"] = project.get("created_at") or datetime.now(timezone.utc).isoformat()
        project["updated_at"] = project.get("updated_at") or project["created_at"]
        project.setdefault("modules", [])
        for module in project.get("modules", []):
            if not isinstance(module, dict) or not is_non_empty_string(module.get("id")) or not module["id"].strip():
                raise ValueError("导入文件包含无效的模块作用域")
            module["id"] = module["id"].strip()
            module["label"] = str(module.get("label") or module["id"]).strip()
            module["created_at"] = module.get("created_at") or datetime.now(timezone.utc).isoformat()
            module["updated_at"] = module.get("updated_at") or module["created_at"]
    if scopes:
        config_path = memory_root / "memory.config.json"
        config = load_json(config_path)
        config["scope_hierarchy"] = scopes
        changes[config_path] = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if changes:
        commit_changes(memory_root, changes, events)
    print(json.dumps({"imported": imported, "skipped": skipped}, ensure_ascii=False))
    return 0


def command_list(memory_root: Path, export: bool = False) -> int:
    rules = [public_rule(rule) for rule in checked_rules(memory_root)]
    result = {"rules": rules}
    if export:
        result.update({"format": "super-memory-export", "schema_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "scope_hierarchy": load_json(memory_root / "memory.config.json").get("scope_hierarchy", [])})
    print(json.dumps(result, ensure_ascii=False))
    return 0


LANGUAGE_ALIASES = {"c++": "cpp", "cxx": "cpp", "cplusplus": "cpp", "js": "javascript", "ts": "typescript", "py": "python", "c#": "csharp", "golang": "go", "nodejs": "node.js"}
SCOPE_WEIGHT = {"module": 4, "project": 3, "technology": 2, "language": 1, "global": 0}


def normalize_term(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return LANGUAGE_ALIASES.get(value, value)


def tokenize(value: str) -> set[str]:
    return {normalize_term(token) for token in re.findall(r"[a-z0-9_][a-z0-9_.+#:-]*|[\u3400-\u9fff]+", unicodedata.normalize("NFKC", value).casefold())}


def scope_matches(rule: dict[str, Any], project: str | None, modules: list[str], languages: list[str], technologies: list[str]) -> bool:
    scope = rule["scope"]
    for field, requested in (("projects", [project] if project else []), ("modules", modules), ("languages", languages), ("technologies", technologies)):
        declared = {normalize_term(value) for value in scope[field]}
        if declared and requested and declared.isdisjoint(normalize_term(value) for value in requested):
            return False
    # 已知项目而模块未知时，只应用项目及更广范围的规则。
    if project and not modules and scope["modules"]:
        return False
    return True


def match_terms(rule: dict[str, Any], query: str) -> list[str]:
    # 项目和模块只用于过滤，不作为相关度加分项。
    fields = [rule["title"], rule["trigger"]["description"], *rule["trigger"]["keywords"], *rule["tags"], *rule["scope"]["languages"], *rule["scope"]["technologies"]]
    query_tokens = tokenize(query)
    entry_tokens = tokenize(" ".join(fields))
    matched = query_tokens & entry_tokens
    normalized_query = unicodedata.normalize("NFKC", query).casefold()
    normalized_fields = unicodedata.normalize("NFKC", " ".join(fields)).casefold()
    # 保留英文标识符边界；中文允许从句子中识别规则中已有的词。
    phrase_tokens = tokenize(" ".join([rule["title"], *rule["trigger"]["keywords"], *rule["tags"]]))
    matched.update(token for token in phrase_tokens if len(token) >= 2 and CHINESE_PATTERN.search(token) and token in normalized_query)
    matched.update(token for token in query_tokens if len(token) >= 2 and CHINESE_PATTERN.search(token) and token in normalized_fields)
    return sorted(matched)


def command_retrieve(memory_root: Path, query: str, limit: int, project: str | None = None, modules: list[str] | None = None, languages: list[str] | None = None, technologies: list[str] | None = None, output_format: str = "summary", max_chars: int = 16000, include_candidates: bool = False) -> int:
    if limit < 1 or max_chars < 512:
        raise ValueError("--limit 必须大于 0，--max-chars 不得小于 512。")
    if modules and not project:
        raise ValueError("--module 必须与 --project 一起使用。")
    ranked = []
    for rule in checked_rules(memory_root):
        if rule["status"] != "active" and not (include_candidates and rule["status"] == "candidate"):
            continue
        if not scope_matches(rule, project, modules or [], languages or [], technologies or []):
            continue
        terms = match_terms(rule, query)
        known_scope = all(not rule["scope"][field] or supplied for field, supplied in (("projects", project), ("modules", modules), ("languages", languages), ("technologies", technologies)))
        required = rule["status"] == "active" and rule["trigger"].get("always_apply", False) and known_scope
        if not terms and not required:
            continue
        priority = {"critical": 4, "high": 3, "medium": 2, "low": 1}[rule["priority"]]
        score = len(terms) * 10 + priority + rule["confidence"]
        ranked.append((required, score, rule, terms))
    ranked.sort(key=lambda item: (not item[0], item[2]["status"] != "active", -item[1], -SCOPE_WEIGHT[item[2]["scope"]["level"]], item[2]["id"]))
    chosen = []
    optional_count = 0
    for item in ranked:
        if item[0] or optional_count < limit:
            chosen.append(item)
            if not item[0]:
                optional_count += 1
    if output_format == "summary":
        print(json.dumps([{"score": round(score, 2), "id": rule["id"], "title": rule["title"], "path": rule["_path"]} for _, score, rule, _ in chosen], ensure_ascii=False, indent=2))
        return 0

    result = {"memory_root": str(memory_root), "rules": [], "matched_count": len(ranked), "omitted_count": len(ranked), "warnings": []}
    for required, score, rule, terms in chosen:
        entry = {
            "id": rule["id"], "title": rule["title"], "status": rule["status"],
            "score": round(score, 2), "priority": rule["priority"],
            "scope": rule["scope"], "trigger": rule["trigger"],
            "instruction": rule["instruction"], "exceptions": rule["exceptions"],
            "relations": rule["relations"], "path": rule["_path"],
            "updated_at": rule["lifecycle"].get("updated_at"),
            "matched_terms": terms, "always_apply": required,
        }
        result["rules"].append(entry)
        result["omitted_count"] -= 1
        if len(json.dumps(result, ensure_ascii=False, indent=2)) + 160 > max_chars:
            result["rules"].pop()
            result["omitted_count"] += 1
            if required:
                raise ValueError("必须应用的规则超出输出预算，请增大 --max-chars；未截断规则正文或例外。")
    if result["omitted_count"]:
        result["warnings"].append("部分匹配规则因数量或字符预算未返回；请缩小任务范围，或增大 --limit / --max-chars。")
    print(json.dumps(result, ensure_ascii=False, indent=2))
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
    retrieve.add_argument("--project", help="当前项目 ID")
    retrieve.add_argument("--module", action="append", default=[], help="当前模块 ID，可重复")
    retrieve.add_argument("--language", action="append", default=[], help="任务涉及的语言，可重复")
    retrieve.add_argument("--technology", action="append", default=[], help="任务涉及的技术，可重复")
    retrieve.add_argument("--format", choices=("summary", "context"), default="summary", help="context 返回完整要求、例外和来源")
    retrieve.add_argument("--max-chars", type=int, default=16000, help="context 输出字符预算，至少 512")
    retrieve.add_argument("--include-candidates", action="store_true", help="复盘时检索候选规则；候选规则不约束代码")
    save = subparsers.add_parser("save")
    save.add_argument("--input", required=True, type=Path, help="待保存的完整规则 JSON 文件")
    save.add_argument("--status", choices=sorted(SAVE_STATUSES), help="覆盖输入规则的状态")
    save.add_argument("--actor", default="user", help="执行保存的主体，默认为 user")
    save.add_argument("--reason", required=True, help="包含中文的保存原因")
    save.add_argument("--mode", choices=("upsert", "create", "update"), default="upsert")
    save.add_argument("--expected-updated-at", help="读取规则时的 updated_at；没有时间时传 null，防止覆盖较新修改")
    save.add_argument("--json", action="store_true", help="返回保存后的规则 JSON")
    delete = subparsers.add_parser("delete", help="移入回收区并记录事件")
    delete.add_argument("--id", required=True)
    delete.add_argument("--reason", required=True)
    listing = subparsers.add_parser("list", help="在库锁内读取规则快照")
    listing.add_argument("--export", action="store_true")
    importer = subparsers.add_parser("import", help="一次事务导入规则和作用域")
    importer.add_argument("--input", required=True, type=Path)
    importer.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    memory_root = resolve_memory_root(args.memory_root)
    if args.command != "init" and not (memory_root / "memory.config.json").exists():
        print(
            f"指定路径不是记忆根目录，请先运行 init：{memory_root}",
            file=sys.stderr,
        )
        return 2
    try:
        with memory_lock(memory_root):
            if args.command == "init":
                return command_init(memory_root, args.force)
            if args.command == "validate":
                return command_validate(memory_root)
            if args.command == "reindex":
                return command_reindex(memory_root)
            if args.command == "retrieve":
                return command_retrieve(memory_root, args.query, args.limit, args.project, args.module, args.language, args.technology, args.format, args.max_chars, args.include_candidates)
            if args.command == "delete":
                return command_delete(memory_root, args.id, args.reason)
            if args.command == "list":
                return command_list(memory_root, args.export)
            if args.command == "import":
                return command_import(memory_root, args.input, args.overwrite)
            return command_save(memory_root, args.input, args.status, args.actor, args.reason, args.mode, args.expected_updated_at, args.json)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
