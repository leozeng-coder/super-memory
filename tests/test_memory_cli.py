from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "skills" / "super-memory" / "scripts" / "memory_cli.py"
SCHEMA_ASSET = (
    PROJECT_ROOT / "skills" / "super-memory" / "assets" / "memory-rule.schema.json"
)


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
