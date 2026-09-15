from __future__ import annotations

import copy
import uuid

from .domain import MemoryError, Record, json_bytes, normalize_scopes, now, prepare_rule, require_reason, require_revision, validate_rule
from .repository import RuleStore
from .search import SearchRequest, search
from .scopes import catalog, resolve_features


class MemoryService:
    def __init__(self, store: RuleStore):
        self.store = store

    def info(self) -> dict:
        return {"memory_root": str(self.store.root), "initialized": self.store.config_path.is_file()}

    def list(self) -> dict:
        with self.store.session():
            records = self.store.records()
            return {"records": [record.public() for record in records], "scopes": catalog(self.store.config().get("scope_hierarchy", []), records)}

    def scopes(self) -> dict:
        with self.store.session():
            return {"scopes": catalog(self.store.config().get("scope_hierarchy", []), self.store.records())}

    def get(self, rule_id: str) -> dict:
        with self.store.session():
            return self.store.get(rule_id).public()

    def search(self, request: SearchRequest) -> dict:
        with self.store.session():
            records = self.store.records()
            request, warnings = resolve_features(request, catalog(self.store.config().get("scope_hierarchy", []), records))
        return search(records, request, self.store.root, warnings)

    def validate(self) -> dict:
        with self.store.session():
            records = self.store.records()
            normalize_scopes(self.store.config().get("scope_hierarchy", []))
            return {"valid": True, "rules": len(records)}

    def reindex(self) -> dict:
        with self.store.session():
            self.store.commit({}, [])
            return {"indexed": len(self.store.records())}

    def plan(self, rule: dict, previous: Record | None, reason: str) -> tuple[dict, dict]:
        destination = self.store.destination(rule)
        changes = {}
        if previous and previous.path != destination:
            changes[previous.path] = None
        changes[destination] = json_bytes(rule)
        action = "created" if previous is None else "updated"
        old_status = previous.rule["status"] if previous else None
        if old_status != rule["status"]:
            action = "activated" if rule["status"] == "active" else action if previous is None else "status_changed"
        event = {"event_id": "EVT-" + uuid.uuid4().hex, "time": now(), "action": action, "node_id": rule["id"], "actor": "user", "reason": reason, "old_status": old_status, "new_status": rule["status"]}
        return changes, event

    def create(self, incoming: dict, reason: str) -> dict:
        require_reason(reason)
        rule = prepare_rule(incoming)
        with self.store.session():
            if any(record.rule["id"] == rule["id"] for record in self.store.records()):
                raise MemoryError("already_exists", "规则 ID 已存在。")
            changes, event = self.plan(rule, None, reason)
            self.store.commit(changes, [event])
            return Record(rule, self.store.destination(rule)).public()

    def update(self, rule_id: str, incoming: dict, expected: str, reason: str) -> dict:
        require_reason(reason)
        with self.store.session():
            existing = self.store.get(rule_id)
            require_revision(existing, expected)
            rule = prepare_rule(incoming, existing)
            changes, event = self.plan(rule, existing, reason)
            self.store.commit(changes, [event])
            return Record(rule, self.store.destination(rule)).public()

    def delete(self, rule_id: str, expected: str, reason: str) -> dict:
        require_reason(reason)
        with self.store.session():
            existing = self.store.get(rule_id)
            require_revision(existing, expected)
            trash = self.store.root / "graph" / "rules" / "trash" / (rule_id + "-" + uuid.uuid4().hex + ".json")
            event = {"event_id": "EVT-" + uuid.uuid4().hex, "time": now(), "action": "deleted-to-trash", "node_id": rule_id, "actor": "user", "reason": reason, "trash_path": trash.relative_to(self.store.root).as_posix()}
            self.store.commit({existing.path: None, trash: existing.path.read_bytes()}, [event])
            return {"deleted": rule_id}

    def export(self) -> dict:
        with self.store.session():
            return {"format": "super-memory-export", "schema_version": 1, "exported_at": now(), "rules": [record.rule for record in self.store.records()], "scope_hierarchy": self.store.config().get("scope_hierarchy", [])}

    def import_bundle(self, bundle: dict, conflict: str, reason: str) -> dict:
        require_reason(reason)
        if not isinstance(bundle, dict) or bundle.get("format") != "super-memory-export" or bundle.get("schema_version") != 1 or not isinstance(bundle.get("rules"), list):
            raise MemoryError("validation_error", "不是有效的规则导出文件。")
        if conflict not in ("skip", "replace") or len(bundle["rules"]) > 10000:
            raise MemoryError("validation_error", "导入策略无效或规则超过 10000 条。")
        seen = set()
        for rule in bundle["rules"]:
            validate_rule(rule)
            if rule["id"] in seen:
                raise MemoryError("validation_error", f"导入文件包含重复 ID：{rule['id']}")
            seen.add(rule["id"])
        scopes = normalize_scopes(bundle.get("scope_hierarchy", []))
        with self.store.session():
            existing = {record.rule["id"]: record for record in self.store.records()}
            changes, events, imported, skipped = {}, [], [], []
            for incoming in bundle["rules"]:
                previous = existing.get(incoming["id"])
                if previous and conflict == "skip":
                    skipped.append(incoming["id"])
                    continue
                rule = prepare_rule(incoming, previous)
                planned, event = self.plan(rule, previous, reason)
                changes.update(planned)
                events.append(event)
                imported.append(rule["id"])
            if scopes:
                config = copy.deepcopy(self.store.config())
                config["scope_hierarchy"] = scopes
                changes[self.store.config_path] = json_bytes(config)
            if changes:
                self.store.commit(changes, events)
            return {"imported": imported, "skipped": skipped}
