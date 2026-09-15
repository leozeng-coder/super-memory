import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import sample_rule
from super_memory import repository
from super_memory.domain import MemoryError, json_bytes
from super_memory.repository import RuleStore
from super_memory.service import MemoryService


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="super-memory-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = RuleStore(self.root)
        self.store.initialize()
        self.service = MemoryService(self.store)

    def test_init_preserves_configuration_and_creates_no_schema_copy(self):
        config = json.loads(self.store.config_path.read_text(encoding="utf-8"))
        config["source_projects"] = [{"id": "test"}]
        repository.write_bytes(self.store.config_path, json_bytes(config))
        self.store.initialize()
        self.assertEqual(self.store.config()["source_projects"], [{"id": "test"}])
        self.assertFalse((self.root / "schemas").exists())

    def test_create_update_move_and_delete_use_content_revisions(self):
        created = self.service.create(sample_rule(), "测试新增")
        updated_rule = copy.deepcopy(created["rule"])
        updated_rule["status"] = "archived"
        updated = self.service.update(updated_rule["id"], updated_rule, created["revision"], "测试归档")
        self.assertNotEqual(updated["revision"], created["revision"])
        self.assertFalse((self.root / "graph/rules/active/RULE-PYTHON-TEST.json").exists())
        with self.assertRaises(MemoryError) as stale:
            self.service.delete(updated_rule["id"], created["revision"], "测试旧版本删除")
        self.assertEqual(stale.exception.code, "revision_conflict")
        self.service.delete(updated_rule["id"], updated["revision"], "测试删除")
        self.assertEqual(self.service.list()["records"], [])
        self.assertEqual(len(list((self.root / "graph/rules/trash").glob("*.json"))), 1)
        self.assertEqual(len(self.store.events_path.read_text(encoding="utf-8").splitlines()), 3)

    def test_out_of_band_change_is_detected_even_with_unchanged_timestamp(self):
        created = self.service.create(sample_rule(), "测试新增")
        rule = copy.deepcopy(created["rule"])
        rule["title"] = "手工编辑但未更新时间"
        repository.write_bytes(self.store.destination(rule), json_bytes(rule))
        with self.assertRaises(MemoryError) as error:
            self.service.update(rule["id"], created["rule"], created["revision"], "测试旧版本更新")
        self.assertEqual(error.exception.code, "revision_conflict")

    def test_search_reads_rules_instead_of_stale_index_and_ignores_trash(self):
        created = self.service.create(sample_rule(), "测试新增")
        repository.write_bytes(self.store.index_path, b"invalid")
        repository.write_bytes(self.root / "graph/rules/trash/invalid.json", b"invalid")
        self.assertEqual(self.service.get(created["rule"]["id"])["revision"], created["revision"])
        self.assertTrue(self.service.validate()["valid"])

    def test_failed_event_write_restores_all_files(self):
        created = self.service.create(sample_rule(), "测试新增")
        paths = [self.store.destination(created["rule"]), self.store.index_path, self.store.events_path]
        before = {path: path.read_bytes() for path in paths}
        real_write = repository.write_bytes
        failed = False
        def fail_once(path, content):
            nonlocal failed
            if path == self.store.events_path and not failed:
                failed = True
                raise OSError("模拟事件写入失败")
            real_write(path, content)
        incoming = copy.deepcopy(created["rule"])
        incoming["status"] = "archived"
        with patch.object(repository, "write_bytes", side_effect=fail_once):
            with self.assertRaises(MemoryError):
                self.service.update(incoming["id"], incoming, created["revision"], "测试失败恢复")
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        self.assertFalse(self.store.journal_path.exists())
        self.assertFalse((self.root / "graph/rules/archive/RULE-PYTHON-TEST.json").exists())

    def test_next_access_recovers_an_interrupted_transaction(self):
        created = self.service.create(sample_rule(), "测试新增")
        original = copy.deepcopy(created["rule"])
        incoming = copy.deepcopy(original)
        incoming["title"] = "未提交的修改"
        with patch.object(self.store, "index", side_effect=SystemExit("模拟进程中断")):
            with self.assertRaises(SystemExit):
                self.service.update(incoming["id"], incoming, created["revision"], "测试中断恢复")
        self.assertTrue(self.store.journal_path.exists())
        self.assertEqual(MemoryService(RuleStore(self.root)).get(original["id"])["rule"], original)
        self.assertFalse(self.store.journal_path.exists())

    def test_recovery_rejects_path_escape_without_touching_other_files(self):
        journal = {"files": [{"path": "../outside.json", "before": base64.b64encode(b"bad").decode()}]}
        repository.write_bytes(self.store.journal_path, json_bytes(journal))
        with self.assertRaises(MemoryError):
            self.service.list()

    def test_batch_validation_precedes_writes_and_conflicts_preserve_evidence(self):
        rule = sample_rule()
        bad = copy.deepcopy(rule)
        bad.update(id="RULE-BAD", confidence="bad")
        bundle = {"format": "super-memory-export", "schema_version": 1, "rules": [rule, bad]}
        with self.assertRaises(MemoryError):
            self.service.import_bundle(bundle, "skip", "测试批量校验")
        self.assertEqual(self.service.list()["records"], [])
        first = self.service.create(rule, "测试已有规则")
        incoming = copy.deepcopy(first["rule"])
        incoming["evidence"][0]["id"] = "EVIDENCE-IMPORT"
        bundle["rules"] = [incoming]
        self.assertEqual(self.service.import_bundle(bundle, "skip", "跳过冲突")["skipped"], [rule["id"]])
        self.service.import_bundle(bundle, "replace", "确认导入更新")
        self.assertEqual(len(self.service.get(rule["id"])["rule"]["evidence"]), 2)
