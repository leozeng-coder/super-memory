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
        for query in ("C++", "cpp", "cxx", "修改函数参数列表"):
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

    def test_member_namespace_and_identifier_styles_share_search_forms(self):
        rule = sample_rule()
        rule["trigger"]["keywords"] = ["std::vector", "push_back", "logic_scene_frame", "map_uid", "m_walkSpeed"]
        for query in (
            "values.push_back(x)",
            "std::vector<int>",
            "logicSceneFrame",
            "frame wrapper uses logic-scene-frame",
            "mapUid",
            "m_walk_speed",
            "ＬｏｇｉｃＳｃｅｎｅＦｒａｍｅ",
        ):
            self.assertEqual([item["id"] for item in self.search([rule], query)["rules"]], [rule["id"]], query)

    def test_language_context_and_identifier_components_do_not_become_topic_noise(self):
        language_only = sample_rule()
        language_only["scope"]["languages"] = ["cpp"]
        language_only["scope"]["technologies"] = []
        language_only["title"] = "C++ 通用代码要求"
        language_only["tags"] = ["C++"]
        language_only["trigger"]["keywords"] = ["unrelated"]
        self.assertEqual(self.search([language_only], "新增一个 C++ 类")["rules"], [])

        conditional = sample_rule()
        conditional["id"] = "RULE-IF"
        conditional["trigger"]["keywords"] = ["if"]
        algorithm = sample_rule()
        algorithm["id"] = "RULE-REMOVE-IF"
        algorithm["trigger"]["keywords"] = ["std::remove_if"]
        result = self.search([conditional, algorithm], "std::remove_if(executions.begin(), executions.end(), pred)")
        self.assertEqual([item["id"] for item in result["rules"]], ["RULE-REMOVE-IF"])
        algorithm["trigger"]["keywords"] = ["remove-if"]
        result = self.search([conditional, algorithm], "remove-if")
        self.assertEqual([item["id"] for item in result["rules"]], ["RULE-REMOVE-IF"])
        self.assertEqual(self.search([conditional], "这是问题?原因:未知")["rules"], [])

    def test_single_generic_concept_and_generator_component_do_not_match(self):
        signature = sample_rule()
        signature["id"] = "RULE-SIGNATURE"
        signature["trigger"]["keywords"] = ["函数参数列表", "单行平铺"]
        include_order = sample_rule()
        include_order["id"] = "RULE-INCLUDE"
        include_order["trigger"]["keywords"] = ["protocol gen", "#include"]
        client_order = sample_rule()
        client_order["id"] = "RULE-CLIENT-ORDER"
        client_order["trigger"]["keywords"] = ["客户端", "操作顺序"]
        for query in ("命令行参数解析", "函数参数校验", "换行输出日志"):
            self.assertEqual(self.search([signature], query)["rules"], [], query)
        self.assertEqual(self.search([include_order], "gen_nav.exe")["rules"], [])
        self.assertEqual(self.search([client_order], "客户端缓存更新")["rules"], [])
        self.assertEqual([item["id"] for item in self.search([include_order], "gen")["rules"]], ["RULE-INCLUDE"])
        self.assertEqual(
            [item["id"] for item in self.search([signature], "函数参数保持在同一行")["rules"]],
            ["RULE-SIGNATURE"],
        )

    def test_ternary_detection_requires_code_context(self):
        ternary = sample_rule()
        ternary["id"] = "RULE-TERNARY"
        ternary["trigger"]["keywords"] = ["?:"]
        for query in (
            "a ? b : c",
            "a ? b : c ? d : e",
            "condition ? left.member : right.member",
            "result = a ? b : c;",
            "condition ? foo() : bar()",
            "ptr ? ptr->value : 0",
            'flag ? "yes" : "no"',
        ):
            self.assertEqual([item["id"] for item in self.search([ternary], query)["rules"]], ["RULE-TERNARY"], query)
        for query in ("why?because:reason", "why ? because : reason", "https://host/path?key:value", "这是问题?原因:未知"):
            self.assertEqual(self.search([ternary], query)["rules"], [], query)

    def test_cpp_symbols_and_controlled_concepts_improve_recall(self):
        pointer = sample_rule()
        pointer["id"] = "RULE-POINTER"
        pointer["trigger"]["keywords"] = ["shared_ptr", "weak_ptr", "循环引用"]
        pointer["instruction"]["do"] = "观察关系使用 weak_ptr，确保对象能够释放。"
        pointer["rationale"]["problem"] = "相互引用会导致对象无法回收。"
        signature = sample_rule()
        signature["id"] = "RULE-SIGNATURE"
        signature["trigger"]["keywords"] = ["参数列表", "单行平铺"]
        ternary = sample_rule()
        ternary["id"] = "RULE-TERNARY"
        ternary["trigger"]["keywords"] = ["?:", "嵌套三元"]
        cases = (
            (pointer, "std::shared_ptr<Foo>", "RULE-POINTER"),
            (pointer, "对象互相持有后一直不析构", "RULE-POINTER"),
            (signature, "函数的所有形参别换行", "RULE-SIGNATURE"),
            (ternary, "result = a ? b : c ? d : e;", "RULE-TERNARY"),
        )
        for rule, query, expected in cases:
            self.assertEqual([item["id"] for item in self.search([rule], query)["rules"]], [expected], query)

    def test_instruction_rationale_and_evidence_paths_are_low_weight_searchable_fields(self):
        rule = sample_rule()
        rule["trigger"]["keywords"] = ["unrelated-keyword"]
        rule["instruction"]["avoid"] = "不要修改 SceneLogicContext 的对象绑定。"
        rule["rationale"]["root_cause"] = "忽略对象所有权会导致资源泄漏。"
        rule["evidence"][0]["files"] = ["src/scene/logic_scene_context.cpp"]
        for query in ("scene_logic_context", "资源泄漏", "LogicSceneContext"):
            self.assertEqual([item["id"] for item in self.search([rule], query)["rules"]], [rule["id"]], query)

        evidence_only = sample_rule()
        evidence_only["id"] = "RULE-EVIDENCE-SYMBOL"
        evidence_only["trigger"]["keywords"] = ["unrelated-keyword"]
        evidence_only["evidence"][0]["summary"] = "故障定位到 EntityExistTimeEvent 的生命周期。"
        self.assertEqual([item["id"] for item in self.search([evidence_only], "EntityExistTimeEvent")["rules"]], [evidence_only["id"]])

    def test_rare_chinese_bigram_fallback_recalls_paraphrase_without_generic_noise(self):
        session = sample_rule()
        session["id"] = "RULE-SESSION-CLEANUP"
        session["title"] = "连接结束时回收会话资源"
        session["trigger"]["keywords"] = ["unrelated-keyword"]
        session["instruction"]["do"] = "连接断开后清理会话缓存并撤销订阅关系。"
        noise = sample_rule()
        noise["id"] = "RULE-NOISE"
        noise["trigger"]["keywords"] = ["unrelated-keyword"]
        noise["instruction"]["do"] = "调用者负责提供每个线程的随机上下文。"

        result = self.search([session, noise], "会话断开以后要清掉缓存，同时撤销消息订阅")
        self.assertEqual([item["id"] for item in result["rules"]], [session["id"]])
        self.assertEqual(self.search([session, noise], "修改代码需要检查问题")["rules"], [])

    def test_local_search_concepts_are_data_driven(self):
        rule = sample_rule()
        rule["id"] = "RULE-CUSTOM-CONCEPTS"
        rule["trigger"]["keywords"] = ["runtime_authority", "temporary_motion"]
        root = Path.cwd()
        records = [Record(rule, root / "graph/rules/active/RULE-CUSTOM-CONCEPTS.json")]
        request = SearchRequest("本地负责模拟，并带有短时移动效果")
        self.assertEqual(search(records, request, root)["rules"], [])
        result = search(
            records,
            request,
            root,
            configured_concepts={
                "运行权威": ["runtime_authority", "本地负责模拟"],
                "短时移动": ["temporary_motion", "短时移动效果"],
            },
        )
        self.assertEqual([item["id"] for item in result["rules"]], [rule["id"]])

    def test_result_distinguishes_required_rules_from_topical_matches(self):
        rule = sample_rule()
        rule["scope"].update(languages=[], technologies=[])
        rule["trigger"]["always_apply"] = True
        result = self.search([rule], "完全不相关的主题")
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["topical_matched_count"], 0)
        self.assertEqual(result["required_count"], 1)
        self.assertIn("required", result["rules"][0]["selection_reason"])
        self.assertTrue(any("查询主题没有命中" in warning for warning in result["warnings"]))

    def test_direct_matches_precede_feature_only_matches_with_limited_space(self):
        feature = sample_rule()
        feature["id"] = "RULE-FEATURE"
        feature["scope"].update(level="feature", projects=["server"], modules=["scene"], features=["Feature"])
        feature["trigger"]["keywords"] = ["unrelated"]
        noise = []
        for index in range(4):
            rule = sample_rule()
            rule["id"] = f"RULE-NOISE-{index}"
            rule["scope"].update(languages=[], technologies=[])
            rule["trigger"]["keywords"] = ["needle"]
            noise.append(rule)
        result = self.search([feature, *noise], "needle", project="server", modules=("scene",), features=("Feature",), limit=1)
        self.assertEqual(result["rules"][0]["selection_reason"], ["lexical"])
        self.assertEqual(result["omitted_count"], len(noise))
        feature["trigger"]["keywords"] = ["needle"]
        both = self.search([feature, *noise], "needle", project="server", modules=("scene",), features=("Feature",), limit=1)
        self.assertEqual([row["id"] for row in both["rules"]], ["RULE-FEATURE"])

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
