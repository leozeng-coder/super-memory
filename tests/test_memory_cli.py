from __future__ import annotations

import json
import copy
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "skills" / "super-memory" / "scripts" / "memory_cli.py"
SCHEMA_ASSET = (
    PROJECT_ROOT / "skills" / "super-memory" / "assets" / "memory-rule.schema.json"
)
CLI_SPEC = importlib.util.spec_from_file_location("memory_cli_under_test", CLI_PATH)
CLI = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(CLI)


class MemoryCliTests(unittest.TestCase):
    def run_cli(
        self,
        memory_root: Path | None,
        *arguments: str,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("SUPER_MEMORY_HOME", None)
        if env_updates:
            environment.update(env_updates)
        command = [sys.executable, "-X", "utf8", str(CLI_PATH)]
        if memory_root is not None:
            command.extend(("--memory-root", str(memory_root)))
        command.extend(arguments)
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            encoding="utf-8",
            env=environment,
        )

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def make_rule(self, status: str = "candidate") -> dict[str, object]:
        return {
            "id": "RULE-PYTHON-TEST",
            "type": "Rule",
            "title": "运行 Python 单元测试",
            "status": status,
            "priority": "high",
            "confidence": 0.9,
            "scope": {
                "level": "global",
                "languages": ["Python"],
                "technologies": ["unittest"],
                "projects": [],
                "modules": [],
            },
            "trigger": {
                "description": "修改 Python 代码后需要验证行为",
                "keywords": ["python", "unittest"],
            },
            "instruction": {
                "do": "运行相关单元测试。",
                "avoid": "不要跳过失败用例。",
            },
            "rationale": {
                "problem": "未验证的修改可能引入回归。",
                "root_cause": "缺少自动化验证。",
            },
            "exceptions": [],
            "relations": {
                "conflicts_with": [],
                "supersedes": [],
                "replacement": None,
            },
            "evidence": [
                {
                    "id": "EVIDENCE-1",
                    "source": "user-instruction",
                    "summary": "用户要求用单元测试验证。",
                    "project": None,
                    "commit": None,
                    "files": [],
                    "observed_at": "2026-08-26T00:00:00+00:00",
                }
            ],
            "lifecycle": {
                "created_at": "2026-08-26T00:00:00+00:00",
                "updated_at": None,
                "last_validated_at": None,
                "review_after": None,
                "success_count": 0,
                "violation_count": 0,
                "exception_count": 0,
            },
            "tags": ["python", "测试"],
        }

    def seed_rules(self, memory_root: Path, rules: list[dict]) -> None:
        self.assert_success(self.run_cli(memory_root, "init"))
        for rule in rules:
            directory = CLI.directory_for_status(rule["status"])
            CLI.write_json(memory_root / "graph" / "rules" / directory / f"{rule['id']}.json", rule)

    def test_retrieve_normalizes_languages_and_chinese_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule("active")
            rule["scope"]["languages"] = ["cpp"]
            rule["title"] = "函数参数保持一致"
            rule["trigger"]["keywords"] = ["参数列表"]
            self.seed_rules(root, [rule])
            for query in ("C++", "cpp", "cxx", "修改函数参数列表", "修改C++函数"):
                result = self.run_cli(root, "retrieve", "--query", query)
                self.assert_success(result)
                self.assertEqual([item["id"] for item in json.loads(result.stdout)], [rule["id"]], query)
            rule["scope"]["languages"] = ["C"]
            self.seed_rules(root, [rule])
            self.assertEqual(len(json.loads(self.run_cli(root, "retrieve", "--query", "C").stdout)), 1)

    def test_scope_filters_and_project_names_do_not_boost_relevance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rules = []
            for suffix, project, module, language in (("GLOBAL", None, None, None), ("PROJECT", "server", None, "cpp"), ("SCENE", "server", "svr_scene", "cpp"), ("GATEWAY", "server", "svr_gateway", "cpp"), ("OTHER", "other", "svr_scene", "cpp"), ("GO", "server", None, "go")):
                rule = self.make_rule("active")
                rule["id"] = "RULE-SCOPE-" + suffix
                rule["scope"].update(level="module" if module else "project" if project else "global", projects=[project] if project else [], modules=[module] if module else [], languages=[language] if language else [])
                rule["trigger"]["keywords"] = ["ownership"]
                rules.append(rule)
            self.seed_rules(root, rules)
            args = ("retrieve", "--query", "ownership", "--project", "server", "--language", "C++")
            result = self.run_cli(root, *args, "--module", "svr_scene")
            self.assert_success(result)
            self.assertEqual({r["id"] for r in json.loads(result.stdout)}, {"RULE-SCOPE-GLOBAL", "RULE-SCOPE-PROJECT", "RULE-SCOPE-SCENE"})
            project_only = self.run_cli(root, *args)
            self.assertEqual({r["id"] for r in json.loads(project_only.stdout)}, {"RULE-SCOPE-GLOBAL", "RULE-SCOPE-PROJECT"})
            self.assertEqual(json.loads(self.run_cli(root, "retrieve", "--query", "server svr_scene").stdout), [])
            self.assertNotEqual(self.run_cli(root, "retrieve", "--query", "ownership", "--module", "svr_scene").returncode, 0)

    def test_generic_description_words_do_not_displace_specific_chinese_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = self.make_rule("active")
            target["trigger"]["keywords"] = ["参数列表"]
            target["priority"] = "low"
            noise = []
            for index in range(10):
                rule = self.make_rule("active")
                rule["id"] = f"RULE-NOISE-{index}"
                rule["priority"] = "critical"
                rule["trigger"]["description"] = "修改 SceneEvent 列表 时处理冷却。"
                noise.append(rule)
            self.seed_rules(root, [target, *noise])
            result = self.run_cli(root, "retrieve", "--query", "修改函数参数列表")
            self.assert_success(result)
            self.assertEqual([r["id"] for r in json.loads(result.stdout)], [target["id"]])

    def test_always_apply_requires_known_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule("active")
            rule["trigger"]["always_apply"] = True
            rule["scope"].update(level="project", projects=["server"], technologies=[])
            self.seed_rules(root, [rule])
            missing = self.run_cli(root, "retrieve", "--query", "unrelated", "--format", "context")
            self.assertEqual(json.loads(missing.stdout)["rules"], [])
            known = self.run_cli(root, "retrieve", "--query", "unrelated", "--project", "server", "--language", "python", "--format", "context")
            self.assertTrue(json.loads(known.stdout)["rules"][0]["always_apply"])

    def test_context_returns_requirements_exceptions_and_budget_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule("active")
            rule["exceptions"] = [{"condition": "用户明确要求跳过", "requirement": "说明未验证的范围"}]
            self.seed_rules(root, [rule])
            result = self.run_cli(root, "retrieve", "--query", "python", "--format", "context")
            self.assert_success(result)
            context = json.loads(result.stdout)
            entry = context["rules"][0]
            for field in ("instruction", "exceptions", "scope", "trigger", "relations"):
                self.assertEqual(entry[field], rule[field])
            self.assertEqual(entry["path"], f"graph/rules/active/{rule['id']}.json")
            self.assertEqual(context["omitted_count"], 0)
            budget = self.run_cli(root, "retrieve", "--query", "python", "--format", "context", "--max-chars", "512")
            self.assert_success(budget)
            limited = json.loads(budget.stdout)
            self.assertEqual(limited["rules"], [])
            self.assertEqual(limited["omitted_count"], 1)
            self.assertTrue(limited["warnings"])
            self.assertLessEqual(len(budget.stdout.strip()), 512)

    def test_always_apply_rules_survive_limit_and_candidates_are_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rules = []
            for index in range(3):
                rule = self.make_rule("active" if index < 2 else "candidate")
                rule["id"] = f"RULE-ALWAYS-{index}"
                rule["trigger"]["always_apply"] = True
                rule["scope"]["languages"] = []
                rule["scope"]["technologies"] = []
                rules.append(rule)
            self.seed_rules(root, rules)
            result = self.run_cli(root, "retrieve", "--query", "unrelated", "--limit", "1", "--format", "context")
            self.assert_success(result)
            self.assertEqual(len(json.loads(result.stdout)["rules"]), 2)
            small = self.run_cli(root, "retrieve", "--query", "unrelated", "--format", "context", "--max-chars", "512")
            self.assertNotEqual(small.returncode, 0)
            self.assertIn("未截断", small.stderr)
            result = self.run_cli(root, "retrieve", "--query", "python", "--include-candidates", "--format", "context")
            candidate = next(r for r in json.loads(result.stdout)["rules"] if r["status"] == "candidate")
            self.assertFalse(candidate["always_apply"])

    def test_save_preserves_evidence_and_does_not_claim_practical_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule()
            self.seed_rules(root, [rule])
            new_rule = copy.deepcopy(rule)
            new_rule["evidence"][0]["id"] = "EVIDENCE-2"
            draft = root / "draft.json"
            CLI.write_json(draft, new_rule)
            result = self.run_cli(root, "save", "--input", str(draft), "--reason", "追加已确认的证据", "--json")
            self.assert_success(result)
            saved = json.loads(result.stdout)
            self.assertEqual([e["id"] for e in saved["evidence"]], ["EVIDENCE-1", "EVIDENCE-2"])
            self.assertIsNone(saved["lifecycle"]["last_validated_at"])
            self.assertEqual(saved["lifecycle"]["created_at"], rule["lifecycle"]["created_at"])
            self.assert_success(self.run_cli(root, "save", "--input", str(draft), "--reason", "重复保存证据"))
            saved = CLI.load_json(root / "graph/rules/candidate/RULE-PYTHON-TEST.json")
            self.assertEqual(len(saved["evidence"]), 2)
            new_rule["evidence"][0]["summary"] = "尝试覆盖证据"
            CLI.write_json(draft, new_rule)
            rejected = self.run_cli(root, "save", "--input", str(draft), "--reason", "检查旧证据保护")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("不可覆盖", rejected.stderr)

    def test_concurrent_edits_reject_stale_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule("active")
            self.seed_rules(root, [rule])
            drafts = []
            for index in range(2):
                edited = copy.deepcopy(rule)
                edited["title"] = f"并发修改 {index}"
                draft = root / f"draft-{index}.json"
                CLI.write_json(draft, edited)
                drafts.append(draft)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda draft: self.run_cli(root, "save", "--input", str(draft), "--expected-updated-at", "null", "--reason", "验证并发编辑"), drafts))
            self.assertEqual(sorted(result.returncode for result in results), [0, 1])
            self.assertIn("已被其他操作修改", next(r.stderr for r in results if r.returncode))
            self.assertEqual(len((root / "history/events.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            self.assert_success(self.run_cli(root, "validate"))

    def test_concurrent_creates_preserve_all_index_entries_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.seed_rules(root, [])
            drafts = []
            for index in range(5):
                rule = self.make_rule("active")
                rule["id"] = f"RULE-CONCURRENT-{index}"
                draft = root / f"draft-{index}.json"
                CLI.write_json(draft, rule)
                drafts.append(draft)
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda draft: self.run_cli(root, "save", "--input", str(draft), "--mode", "create", "--reason", "验证并发新增"), drafts))
            for result in results:
                self.assert_success(result)
            self.assertEqual(len(CLI.load_json(root / "indexes/rules.json")["rules"]), 5)
            self.assertEqual(len((root / "history/events.jsonl").read_text(encoding="utf-8").splitlines()), 5)

    def test_failed_event_write_restores_rules_index_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = self.make_rule("candidate")
            self.seed_rules(root, [rule])
            self.assert_success(self.run_cli(root, "reindex"))
            paths = [root / "graph/rules/candidate/RULE-PYTHON-TEST.json", root / "indexes/rules.json", root / "history/events.jsonl"]
            before = {path: path.read_bytes() for path in paths}
            draft = root / "draft.json"
            CLI.write_json(draft, rule)
            with CLI.memory_lock(root), patch.object(CLI, "append_event", side_effect=OSError("模拟日志写入失败")):
                with self.assertRaisesRegex(RuntimeError, "已回滚"):
                    CLI.command_save(root, draft, "active", "user", "验证事务恢复")
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            self.assertFalse((root / "graph/rules/active/RULE-PYTHON-TEST.json").exists())
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_invalid_enum_type_is_reported_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.seed_rules(root, [])
            for field in ("status", "priority"):
                rule = self.make_rule()
                rule[field] = []
                draft = root / "draft.json"
                CLI.write_json(draft, rule)
                result = self.run_cli(root, "save", "--input", str(draft), "--reason", "检查畸形输入")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("输入规则校验失败", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_import_rejects_invalid_batch_without_partial_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.seed_rules(root, [])
            first = self.make_rule("active")
            second = copy.deepcopy(first)
            second["id"] = "RULE-INVALID-IMPORT"
            second["instruction"] = "无效内容"
            bundle = root / "bundle.json"
            CLI.write_json(bundle, {"format": "super-memory-export", "schema_version": 1, "rules": [first, second]})
            result = self.run_cli(root, "import", "--input", str(bundle))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(CLI.rule_paths(root), [])
            self.assertEqual((root / "history/events.jsonl").read_bytes(), b"")

    def test_init_creates_empty_store_and_validate_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_root = Path(temporary) / "memory"
            self.assert_success(self.run_cli(memory_root, "init"))

            config = json.loads(
                (memory_root / "memory.config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["scope_hierarchy"], [])
            self.assertEqual(config["source_projects"], [])
            for directory in ("active", "candidate", "archive", "trash"):
                self.assertTrue((memory_root / "graph" / "rules" / directory).is_dir())
            self.assertEqual(
                (memory_root / "schemas" / "memory-rule.schema.json").read_bytes(),
                SCHEMA_ASSET.read_bytes(),
            )
            self.assertTrue((memory_root / "history" / "events.jsonl").is_file())
            self.assertEqual(
                json.loads(
                    (memory_root / "indexes" / "rules.json").read_text(
                        encoding="utf-8"
                    )
                )["rules"],
                [],
            )

            result = self.run_cli(memory_root, "validate")
            self.assert_success(result)
            self.assertIn("0 条规则", result.stdout)

    def test_memory_root_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explicit_root = root / "explicit"
            configured_root = root / "configured"
            environment = {
                "SUPER_MEMORY_HOME": str(configured_root),
            }

            self.assert_success(
                self.run_cli(explicit_root, "init", env_updates=environment)
            )
            self.assertTrue((explicit_root / "memory.config.json").exists())
            self.assertFalse((configured_root / "memory.config.json").exists())

            self.assert_success(
                self.run_cli(None, "init", env_updates=environment)
            )
            self.assertTrue((configured_root / "memory.config.json").exists())

    def test_force_only_refreshes_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_root = Path(temporary) / "memory"
            self.assert_success(self.run_cli(memory_root, "init"))
            config_path = memory_root / "memory.config.json"
            schema_path = memory_root / "schemas" / "memory-rule.schema.json"
            index_path = memory_root / "indexes" / "rules.json"
            history_path = memory_root / "history" / "events.jsonl"
            rule_path = memory_root / "graph" / "rules" / "candidate" / "keep.json"
            config_path.write_text('{"custom": true}\n', encoding="utf-8")
            schema_path.write_text('{"custom": true}\n', encoding="utf-8")
            index_path.write_text('{"keep": true}\n', encoding="utf-8")
            history_path.write_text("保留事件\n", encoding="utf-8")
            rule_path.write_text('{"keep": true}\n', encoding="utf-8")

            self.assert_success(self.run_cli(memory_root, "init"))
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), {"custom": True})
            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), {"custom": True})

            self.assert_success(self.run_cli(memory_root, "init", "--force"))
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8"))["scope_hierarchy"],
                [],
            )
            self.assertEqual(schema_path.read_bytes(), SCHEMA_ASSET.read_bytes())
            self.assertEqual(index_path.read_text(encoding="utf-8"), '{"keep": true}\n')
            self.assertEqual(history_path.read_text(encoding="utf-8"), "保留事件\n")
            self.assertTrue(rule_path.exists())

    def test_trash_is_excluded_from_validation_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_root = Path(temporary) / "memory"
            self.assert_success(self.run_cli(memory_root, "init"))
            trash_rule = memory_root / "graph" / "rules" / "trash" / "invalid.json"
            trash_rule.write_text("{}\n", encoding="utf-8")

            self.assert_success(self.run_cli(memory_root, "validate"))
            self.assert_success(self.run_cli(memory_root, "reindex"))
            index = json.loads(
                (memory_root / "indexes" / "rules.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["rules"], [])

    def test_save_rejects_rule_that_does_not_match_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_root = Path(temporary) / "memory"
            self.assert_success(self.run_cli(memory_root, "init"))
            invalid_rule = self.make_rule()
            invalid_rule["priority"] = "urgent"
            invalid_rule["instruction"] = "运行测试"
            draft_path = memory_root / "invalid.json"
            draft_path.write_text(
                json.dumps(invalid_rule, ensure_ascii=False), encoding="utf-8"
            )

            result = self.run_cli(
                memory_root,
                "save",
                "--input",
                str(draft_path),
                "--reason",
                "用户确认保存候选规则",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("输入规则校验失败", result.stderr)
            self.assertFalse(
                (
                    memory_root
                    / "graph"
                    / "rules"
                    / "candidate"
                    / "RULE-PYTHON-TEST.json"
                ).exists()
            )

    def test_save_promotes_candidate_and_retrieve_finds_active_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_root = Path(temporary) / "memory"
            self.assert_success(self.run_cli(memory_root, "init"))
            draft_path = memory_root / "draft.json"
            draft_path.write_text(
                json.dumps(self.make_rule(), ensure_ascii=False), encoding="utf-8"
            )

            self.assert_success(
                self.run_cli(
                    memory_root,
                    "save",
                    "--input",
                    str(draft_path),
                    "--status",
                    "candidate",
                    "--reason",
                    "用户确认保存为候选规则",
                )
            )
            candidate_path = (
                memory_root
                / "graph"
                / "rules"
                / "candidate"
                / "RULE-PYTHON-TEST.json"
            )
            active_path = (
                memory_root
                / "graph"
                / "rules"
                / "active"
                / "RULE-PYTHON-TEST.json"
            )
            self.assertTrue(candidate_path.exists())

            self.assert_success(
                self.run_cli(
                    memory_root,
                    "save",
                    "--input",
                    str(draft_path),
                    "--status",
                    "active",
                    "--reason",
                    "用户确认激活规则",
                )
            )
            self.assertFalse(candidate_path.exists())
            self.assertTrue(active_path.exists())
            self.assertEqual(
                json.loads(active_path.read_text(encoding="utf-8"))["status"],
                "active",
            )

            result = self.run_cli(memory_root, "retrieve", "--query", "python")
            self.assert_success(result)
            matches = json.loads(result.stdout)
            self.assertEqual([match["id"] for match in matches], ["RULE-PYTHON-TEST"])


if __name__ == "__main__":
    unittest.main()
