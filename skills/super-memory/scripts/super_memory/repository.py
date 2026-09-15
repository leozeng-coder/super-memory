from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import MemoryError, Record, SKILL_ROOT, json_bytes, now, validate_rule

RULE_DIRECTORIES = ("active", "candidate", "archive")
CONFIG = {"schema_version": 1, "scope_hierarchy": [], "source_projects": []}


def resolve_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    configured = os.environ.get("SUPER_MEMORY_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = SKILL_ROOT.parent.parent
    return checkout if (checkout / "memory.config.json").is_file() else (Path.home() / ".super-memory").resolve()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MemoryError("storage_error", f"读取 {path.name} 失败：{exc}") from exc


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def directory_for_status(status: str) -> str:
    if status == "active":
        return "active"
    return "archive" if status in {"archived", "superseded"} else "candidate"


class RuleStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config_path = self.root / "memory.config.json"
        self.index_path = self.root / "indexes" / "rules.json"
        self.events_path = self.root / "history" / "events.jsonl"
        self.journal_path = self.root / "history" / "transaction.json"

    @contextmanager
    def session(self, initialize: bool = False, timeout: float = 10):
        if not initialize and not self.config_path.is_file():
            raise MemoryError("not_initialized", f"记忆库尚未初始化：{self.root}。请确认目录后运行 init。")
        lock_path = self.root / "history" / ".write.lock"
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
                        raise MemoryError("busy", "记忆库正被其他操作使用，请稍后重试。")
                    time.sleep(0.05)
            try:
                self.recover()
                yield self
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def initialize(self) -> dict:
        with self.session(initialize=True):
            for directory in (*RULE_DIRECTORIES, "trash"):
                (self.root / "graph" / "rules" / directory).mkdir(parents=True, exist_ok=True)
            if not self.config_path.exists():
                write_bytes(self.config_path, json_bytes(CONFIG))
            if not self.events_path.exists():
                write_bytes(self.events_path, b"")
            if not self.index_path.exists():
                write_bytes(self.index_path, json_bytes(self.index()))
            return {"memory_root": str(self.root), "initialized": True}

    def config(self) -> dict:
        config = read_json(self.config_path)
        if not isinstance(config, dict) or config.get("schema_version") != 1:
            raise MemoryError("validation_error", "记忆库配置格式无效。")
        return config

    def records(self) -> list[Record]:
        records, seen = [], set()
        for directory in RULE_DIRECTORIES:
            for path in sorted((self.root / "graph" / "rules" / directory).glob("*.json")):
                rule = read_json(path)
                validate_rule(rule)
                if rule["id"] in seen:
                    raise MemoryError("validation_error", f"规则 ID 重复：{rule['id']}")
                if path.stem != rule["id"] or directory != directory_for_status(rule["status"]):
                    raise MemoryError("validation_error", f"规则路径与 ID 或状态不一致：{path.name}")
                seen.add(rule["id"])
                records.append(Record(rule, path))
        return records

    def get(self, rule_id: str) -> Record:
        for record in self.records():
            if record.rule["id"] == rule_id:
                return record
        raise MemoryError("not_found", f"找不到规则：{rule_id}")

    def destination(self, rule: dict) -> Path:
        return self.root / "graph" / "rules" / directory_for_status(rule["status"]) / (rule["id"] + ".json")

    def managed_path(self, path: Path) -> bool:
        path = path.resolve()
        if path in {self.config_path, self.index_path, self.events_path}:
            return True
        return path.suffix == ".json" and path.parent in {self.root / "graph" / "rules" / directory for directory in (*RULE_DIRECTORIES, "trash")}

    def index(self) -> dict:
        return {"schema_version": 1, "generated_at": now(), "rules": [
            {"id": record.rule["id"], "title": record.rule["title"], "status": record.rule["status"], "scope": record.rule["scope"], "revision": record.revision, "path": record.path.relative_to(self.root).as_posix()}
            for record in self.records()
        ]}

    def recover(self) -> None:
        if not self.journal_path.exists():
            return
        journal = read_json(self.journal_path)
        if not isinstance(journal, dict) or not isinstance(journal.get("files"), list):
            raise MemoryError("storage_error", "事务日志无效，无法恢复。")
        changes = {}
        for item in journal["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or "before" not in item:
                raise MemoryError("storage_error", "事务日志包含无效记录。")
            destination = (self.root / item["path"]).resolve()
            if not self.managed_path(destination):
                raise MemoryError("storage_error", "事务恢复路径超出规则库范围。")
            try:
                changes[destination] = None if item["before"] is None else base64.b64decode(item["before"], validate=True)
            except (ValueError, TypeError) as exc:
                raise MemoryError("storage_error", "事务日志的原始数据无效。") from exc
        for destination, content in changes.items():
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                write_bytes(destination, content)
        self.journal_path.unlink()

    def commit(self, changes: dict[Path, bytes | None], events: list[dict]) -> None:
        touched = {*changes, self.index_path, self.events_path}
        snapshots = []
        for path in sorted(touched):
            if not self.managed_path(path):
                raise MemoryError("storage_error", "写入路径超出规则库范围。")
            previous = base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None
            snapshots.append({"path": path.relative_to(self.root).as_posix(), "before": previous})
        write_bytes(self.journal_path, json_bytes({"files": snapshots}))
        try:
            for path, content in changes.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    write_bytes(path, content)
            write_bytes(self.index_path, json_bytes(self.index()))
            previous = self.events_path.read_bytes() if self.events_path.exists() else b""
            if events and previous and not previous.endswith(b"\n"):
                previous += b"\n"
            lines = b"".join((json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8") for event in events)
            write_bytes(self.events_path, previous + lines)
            self.journal_path.unlink()
        except Exception as exc:
            try:
                self.recover()
            except Exception as recovery:
                raise MemoryError("storage_error", f"操作失败且恢复未完成：{recovery}") from exc
            raise MemoryError("storage_error", f"操作失败，已恢复原数据：{exc}") from exc
