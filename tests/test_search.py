import copy
import json
import unittest
from pathlib import Path

from support import sample_rule
from super_memory.domain import MemoryError, Record
from super_memory.search import SearchRequest, search


class SearchTests(unittest.TestCase):
    def search(self, rules, query, **kwargs):
        root = Path.cwd()
        records = [Record(rule, root / "graph/rules/active" / (rule["id"] + ".json")) for rule in rules]
        return search(records, SearchRequest(query, **kwargs), root)

    def test_language_aliases_and_chinese_sentence_match(self):
        rule = sample_rule()
        rule["scope"]["languages"] = ["cpp"]
        rule["trigger"]["keywords"] = ["参数列表"]
        for query in ("C++", "cpp", "cxx", "修改函数参数列表", "修改C++函数"):
            self.assertEqual(len(self.search([rule], query)["rules"]), 1, query)
        rule["scope"]["languages"] = ["C"]
        self.assertEqual(len(self.search([rule], "C")["rules"]), 1)

    def test_scope_filters_keep_inherited_rules_and_do_not_score_project_names(self):
        rules = []
        for suffix, project, module, language in (("GLOBAL", None, None, None), ("PROJECT", "server", None, "cpp"), ("SCENE", "server", "scene", "cpp"), ("GATEWAY", "server", "gateway", "cpp"), ("OTHER", "other", "scene", "cpp"), ("GO", "server", None, "go")):
            rule = sample_rule()
            rule["id"] = "RULE-" + suffix
            rule["scope"].update(level="module" if module else "project" if project else "global", projects=[project] if project else [], modules=[module] if module else [], languages=[language] if language else [])
            rules.append(rule)
        scoped = self.search(rules, "unittest", project="server", modules=("scene",), languages=("C++",))
        self.assertEqual({r["id"] for r in scoped["rules"]}, {"RULE-GLOBAL", "RULE-PROJECT", "RULE-SCENE"})
        project_only = self.search(rules, "unittest", project="server", languages=("cpp",))
        self.assertEqual({r["id"] for r in project_only["rules"]}, {"RULE-GLOBAL", "RULE-PROJECT"})
        self.assertEqual(self.search(rules, "server scene")["rules"], [])
        with self.assertRaises(MemoryError):
            self.search(rules, "python", modules=("scene",))

    def test_technical_aliases_do_not_rename_project_ids(self):
        rule = sample_rule()
        rule["scope"].update(level="project", projects=["js"])
        self.assertEqual(self.search([rule], "python", project="javascript")["rules"], [])

    def test_generic_description_words_do_not_displace_specific_rule(self):
        rule = sample_rule()
        rule["trigger"]["keywords"] = ["参数列表"]
        noise = []
        for index in range(10):
            other = sample_rule()
            other.update(id="RULE-NOISE-" + str(index), priority="critical")
            other["trigger"]["description"] = "修改 SceneEvent 列表 时检查冷却。"
            noise.append(other)
        self.assertEqual([r["id"] for r in self.search([rule, *noise], "修改函数参数列表")["rules"]], [rule["id"]])

    def test_budget_is_exact_and_never_truncates_exceptions(self):
        rule = sample_rule()
        rule["exceptions"] = [{"condition": "测试条件", "requirement": "保留完整例外"}]
        full = self.search([rule], "python")
        self.assertEqual(full["rules"][0]["exceptions"], rule["exceptions"])
        self.assertEqual(len(full["rules"][0]["revision"]), 64)
        limited = self.search([rule], "python", max_chars=512)
        self.assertEqual(limited["rules"], [])
        self.assertEqual(limited["omitted_count"], 1)
        self.assertLessEqual(len(json.dumps(limited, ensure_ascii=False, indent=2)) + 1, 512)

    def test_always_apply_rules_require_known_scope_and_survive_count_limit(self):
        rule = sample_rule()
        rule["trigger"]["always_apply"] = True
        self.assertEqual(self.search([rule], "unrelated")["rules"], [])
        rule["scope"].update(languages=[], technologies=[])
        second = copy.deepcopy(rule)
        second["id"] = "RULE-SECOND"
        result = self.search([rule, second], "unrelated", limit=1)
        self.assertEqual(len(result["rules"]), 2)
        self.assertTrue(all(r["required"] for r in result["rules"]))
        with self.assertRaises(MemoryError) as error:
            self.search([rule], "unrelated", max_chars=512)
        self.assertEqual(error.exception.code, "budget_exceeded")

    def test_candidates_are_opt_in_and_archived_rules_never_apply(self):
        candidate, archived = sample_rule("candidate"), sample_rule("archived")
        archived["id"] = "RULE-ARCHIVED"
        candidate["trigger"]["always_apply"] = True
        self.assertEqual(self.search([candidate, archived], "python")["rules"], [])
        result = self.search([candidate, archived], "python", include_candidates=True)
        self.assertEqual(len(result["rules"]), 1)
        self.assertFalse(result["rules"][0]["required"])
