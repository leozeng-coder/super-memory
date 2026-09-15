import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from support import SCRIPTS, sample_rule


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="super-memory-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "memory"
        self.run_cli("init")

    def run_cli(self, *args, payload=None, success=True, extra_env=None):
        environment = {**os.environ, **(extra_env or {})}
        process = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "memory.py"), "--root", str(self.root), *args], input=json.dumps(payload, ensure_ascii=False) if payload is not None else None, capture_output=True, text=True, encoding="utf-8", env=environment)
        if success:
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)
        return process

    def test_commands_use_json_and_support_stdin_and_export_file(self):
        created = self.run_cli("create", "--reason", "测试新增", payload=sample_rule())
        listed = self.run_cli("list")
        self.assertEqual(listed["records"], [created])
        self.assertEqual(self.run_cli("get", created["rule"]["id"]), created)
        output = Path(self.temporary.name) / "export.json"
        self.run_cli("export", "--output", str(output))
        self.assertFalse(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.run_cli("delete", created["rule"]["id"], "--revision", created["revision"], "--reason", "测试删除")
        imported = self.run_cli("import", "--input", str(output), "--reason", "测试导入")
        self.assertEqual(imported["imported"], [created["rule"]["id"]])

    def test_update_requires_revision_and_reports_structured_conflicts(self):
        created = self.run_cli("create", "--reason", "测试新增", payload=sample_rule())
        missing = self.run_cli("update", created["rule"]["id"], "--reason", "缺少版本", payload=created["rule"], success=False)
        self.assertNotEqual(missing.returncode, 0)
        edited = copy.deepcopy(created["rule"])
        edited["title"] = "修改标题"
        self.run_cli("update", edited["id"], "--revision", created["revision"], "--reason", "测试更新", payload=edited)
        stale = self.run_cli("delete", edited["id"], "--revision", created["revision"], "--reason", "旧版本删除", success=False)
        self.assertEqual(json.loads(stale.stderr)["error"]["code"], "revision_conflict")

    def test_concurrent_creates_preserve_all_rules_events_and_index(self):
        def create(index):
            rule = sample_rule()
            rule["id"] = "RULE-CONCURRENT-" + str(index)
            return self.run_cli("create", "--reason", "测试并发新增", payload=rule)
        with ThreadPoolExecutor(max_workers=5) as pool:
            records = list(pool.map(create, range(5)))
        self.assertEqual(len(self.run_cli("list")["records"]), len(records))
        self.assertEqual(len(json.loads((self.root / "indexes/rules.json").read_text(encoding="utf-8"))["rules"]), 5)
        self.assertEqual(len((self.root / "history/events.jsonl").read_text(encoding="utf-8").splitlines()), 5)

    def test_concurrent_edits_allow_only_one_revision(self):
        created = self.run_cli("create", "--reason", "测试新增", payload=sample_rule())
        def edit(index):
            rule = copy.deepcopy(created["rule"])
            rule["title"] = "并发修改 " + str(index)
            return self.run_cli("update", rule["id"], "--revision", created["revision"], "--reason", "测试并发更新", payload=rule, success=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, range(2)))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])

    def test_explicit_root_takes_precedence_and_info_does_not_initialize(self):
        other = Path(self.temporary.name) / "other"
        info = self.run_cli("info", extra_env={"SUPER_MEMORY_HOME": str(other)})
        self.assertEqual(Path(info["memory_root"]), self.root)
        environment = {**os.environ, "SUPER_MEMORY_HOME": str(other)}
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "memory.py"), "info"], capture_output=True, text=True, encoding="utf-8", env=environment, check=True)
        self.assertFalse(json.loads(result.stdout)["initialized"])
        self.assertFalse(other.exists())

    def test_process_exit_during_write_is_recovered_on_next_access(self):
        created = self.run_cli("create", "--reason", "测试新增", payload=sample_rule())
        code = """
import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from super_memory.repository import RuleStore
from super_memory.service import MemoryService
service=MemoryService(RuleStore(Path(sys.argv[2])))
record=service.get('RULE-PYTHON-TEST')
rule=record['rule']
rule['title']='uncommitted'
service.store.index=lambda: os._exit(17)
service.update(rule['id'],rule,record['revision'],'进程中断验证')
"""
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(SCRIPTS), str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(self.run_cli("get", created["rule"]["id"]), created)
        self.assertFalse((self.root / "history/transaction.json").exists())
