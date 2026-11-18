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
        process = subprocess.run([sys.executable, "-B", str(SCRIPTS / "memory.py"), "--root", str(self.root), *args], input=json.dumps(payload, ensure_ascii=False) if payload is not None else None, capture_output=True, text=True, encoding="utf-8", env=environment)
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

    def test_compact_and_full_search_accept_minimal_convention(self):
        rule = sample_rule("candidate")
        del rule["instruction"]["avoid"], rule["rationale"], rule["exceptions"]
        created = self.run_cli("create", "--reason", "新增项目约定", payload=rule)
        args = ("search", "--query", "运行 Python 单元测试", "--include-candidates")
        compact = self.run_cli(*args)["rules"][0]
        full = self.run_cli(*args, "--full")["rules"][0]
        self.assertEqual(compact["instruction"], rule["instruction"])
        self.assertEqual(compact["when"], rule["trigger"]["description"])
        self.assertEqual(compact["status"], "candidate")
        self.assertEqual(compact["id"], full["id"])
        self.assertTrue({"path", "score", "matched_terms", "revision", "exceptions", "relations"}.isdisjoint(compact))
        self.assertEqual(full["revision"], created["revision"])
        self.assertEqual(full["trigger"], rule["trigger"])
        self.assertEqual(full["exceptions"], [])
        self.assertEqual(self.run_cli("get", rule["id"]), created)

    def test_compact_preserves_actionable_boundaries(self):
        rule = sample_rule()
        rule["scope"].update(languages=[], technologies=[])
        rule["trigger"]["always_apply"] = True
        rule["exceptions"] = [{"condition": "只改文档", "requirement": "跳过编译"}]
        rule["relations"]["conflicts_with"] = ["RULE-OTHER"]
        self.run_cli("create", "--reason", "测试保留边界", payload=rule)
        row = self.run_cli("search", "--query", "unrelated")["rules"][0]
        self.assertTrue(row["required"])
        self.assertEqual(row["exceptions"], rule["exceptions"])
        self.assertEqual(row["relations"], {"conflicts_with": ["RULE-OTHER"]})
        self.assertEqual(row["instruction"], rule["instruction"])

    def test_review_keeps_first_round_rules_and_checks_new_implementation_concerns(self):
        for name, keyword in (("PLAN", "renderHeader"), ("ACTUAL", "scheduleCallback"), ("SHARED", "common")):
            rule = sample_rule()
            rule["id"] = "RULE-" + name
            rule["scope"].update(languages=[], technologies=[])
            rule["trigger"].update(keywords=[keyword], always_apply=name == "SHARED")
            self.run_cli("create", "--reason", "测试交付前二次审查", payload=rule)

        before = self.run_cli("search", "--query", "renderHeader")
        before_ids = [rule["id"] for rule in before["rules"]]
        self.assertCountEqual(before_ids, ["RULE-PLAN", "RULE-SHARED"])
        result = self.run_cli("review", "--query", "scheduleCallback", "--before", *before_ids)
        after = result["search"]
        self.assertEqual(after, self.run_cli("search", "--query", "scheduleCallback"))
        self.assertCountEqual([rule["id"] for rule in after["rules"]], ["RULE-ACTUAL", "RULE-SHARED"])
        self.assertEqual(result["review_ids"], [*before_ids, "RULE-ACTUAL"])

        # The fix removed the callback. Keep its rule on the checklist for rechecking.
        revised = self.run_cli("review", "--query", "renderHeader", "--before", *result["review_ids"])
        self.assertEqual(revised["review_ids"], result["review_ids"])
        first_round_empty = self.run_cli("review", "--query", "scheduleCallback")
        self.assertEqual(first_round_empty["review_ids"], [rule["id"] for rule in after["rules"]])

    def test_feature_catalog_and_cli_scope_arguments(self):
        rule = sample_rule()
        rule["scope"].update(level="feature", projects=["server"], modules=["svr_scene"], features=["SceneEvent"])
        self.run_cli("create", "--reason", "测试功能规则", payload=rule)
        catalog = self.run_cli("scopes")["scopes"]
        self.assertEqual(catalog[0]["modules"][0]["features"][0]["id"], "SceneEvent")
        result = self.run_cli("search", "--query", "新增功能", "--project", "server", "--module", "svr_scene", "--feature", "SceneEvent")
        self.assertEqual(result["rules"][0]["id"], rule["id"])
        self.assertEqual(result["context"]["features"], ["SceneEvent"])

    def test_feature_previews_expand_on_demand_in_search_and_review(self):
        for name, keyword in (("ALWAYS", "other"), ("DELAY", "DelayMs"), ("MAP", "MapUid")):
            rule = sample_rule("candidate" if name == "MAP" else "active")
            rule.update(id="RULE-" + name, title=keyword)
            rule["trigger"].update(description="检查 " + keyword, keywords=[keyword], always_apply=name == "ALWAYS")
            rule["scope"].update(level="feature", projects=["server"], modules=["scene"],
                                 features=["SceneEvent"], languages=[], technologies=[])
            rule["exceptions"] = [{"condition": "复用同一配置", "requirement": "保留原值"}]
            rule["relations"]["conflicts_with"] = ["RULE-OTHER"]
            self.run_cli("create", "--reason", "测试按需读取", payload=rule)

        scope = ("--project", "server", "--module", "scene", "--feature", "SceneEvent", "--include-candidates")
        first = self.run_cli("search", *scope, "--query", "DelayMs")
        rows = {row["id"]: row for row in first["rules"]}
        self.assertEqual(set(rows), {"RULE-ALWAYS", "RULE-DELAY", "RULE-MAP"})
        preview = rows["RULE-MAP"]
        self.assertEqual(preview["selection_reason"], ["feature_scope"])
        self.assertTrue(preview["preview"])
        self.assertEqual((preview["title"], preview["when"], preview["status"]), ("MapUid", "检查 MapUid", "candidate"))
        self.assertTrue({"instruction", "exceptions", "relations"}.isdisjoint(preview))
        for rule_id in ("RULE-ALWAYS", "RULE-DELAY"):
            row = rows[rule_id]
            stored = self.run_cli("get", rule_id)["rule"]
            for key in ("instruction", "exceptions"):
                self.assertEqual(row[key], stored[key])
            self.assertEqual(row["relations"]["conflicts_with"], ["RULE-OTHER"])

        # An explicitly unrelated ticket must not promote a preview in either round.
        mixed = "DelayMs。历史工单提到 MapUid，与这次改动无关。"
        self.assertEqual(self.run_cli("search", *scope, "--query", mixed), first)
        repeat = self.run_cli("review", *scope, "--query", mixed, "--before", *rows)
        self.assertEqual(repeat["search"], first)
        self.assertEqual(repeat["review_ids"], list(rows))

        expanded = self.run_cli("get", preview["id"])["rule"]
        full = self.run_cli("search", *scope, "--query", "DelayMs", "--full")
        diagnostic = next(row for row in full["rules"] if row["id"] == preview["id"])
        self.assertEqual(diagnostic["instruction"], expanded["instruction"])
        # The implementation actually uses MapUid now. Its rule needs the full body.
        revised = self.run_cli("review", *scope, "--query", "MapUid", "--before", *rows)
        self.assertEqual(revised["review_ids"], list(rows))
        updated = {row["id"]: row for row in revised["search"]["rules"]}
        self.assertEqual(updated["RULE-MAP"]["exceptions"], expanded["exceptions"])
        self.assertEqual(updated["RULE-MAP"]["instruction"], expanded["instruction"])
        self.assertTrue(updated["RULE-DELAY"]["preview"])

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
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "memory.py"), "info"], capture_output=True, text=True, encoding="utf-8", env=environment, check=True)
        self.assertFalse(json.loads(result.stdout)["initialized"])
        self.assertFalse(other.exists())

    def test_cli_forces_utf8_even_when_inherited_encoding_is_gbk(self):
        created = self.run_cli("create", "--reason", "测试中文编码", payload=sample_rule())
        environment = {**os.environ, "PYTHONIOENCODING": "gbk"}
        success = subprocess.run([sys.executable, "-B", str(SCRIPTS / "memory.py"), "--root", str(self.root), "get", created["rule"]["id"]], capture_output=True, env=environment, check=True)
        self.assertEqual(json.loads(success.stdout.decode("utf-8"))["rule"]["title"], "运行 Python 单元测试")
        failure = subprocess.run([sys.executable, "-B", str(SCRIPTS / "memory.py"), "--root", str(self.root), "get", "RULE-MISSING"], capture_output=True, env=environment)
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("找不到规则", json.loads(failure.stderr.decode("utf-8"))["error"]["message"])

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
