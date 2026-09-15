import copy
import tempfile
import unittest
from pathlib import Path

from support import sample_rule
from super_memory.domain import MemoryError, normalize_scopes, validate_rule
from super_memory.repository import RuleStore
from super_memory.search import SearchRequest
from super_memory.service import MemoryService


class FeatureScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="super-memory-features-")
        self.addCleanup(self.temporary.cleanup)
        self.store = RuleStore(Path(self.temporary.name))
        self.store.initialize()
        self.service = MemoryService(self.store)
        rules = []
        for name, project, module, feature in (("GLOBAL", None, None, None), ("PROJECT", "server", None, None), ("MODULE", "server", "svr_scene", None), ("EVENT", "server", "svr_scene", "SceneEvent"), ("MOTION", "server", "svr_scene", "Motion"), ("OTHER", "server", "svr_user", "SceneEvent")):
            rule = sample_rule()
            rule["id"] = "RULE-" + name
            rule["scope"].update(level="feature" if feature else "module" if module else "project" if project else "global", projects=[project] if project else [], modules=[module] if module else [], features=[feature] if feature else [])
            rules.append(rule)
        self.service.import_bundle({"format": "super-memory-export", "schema_version": 1, "rules": rules, "scope_hierarchy": [{"id": "server", "modules": [{"id": "svr_scene", "features": [{"id": "SceneEvent", "aliases": ["场景事件"], "paths": ["svr_scene/src/scene/logic_scene_event*"]}]}]}]}, "skip", "测试功能分类")

    def search(self, query="unittest", **kwargs):
        return self.service.search(SearchRequest(query, project="server", modules=("svr_scene",), **kwargs))

    def test_feature_retrieval_inherits_parents_and_excludes_siblings(self):
        result = self.search(features=("SceneEvent",))
        self.assertEqual({r["id"] for r in result["rules"]}, {"RULE-GLOBAL", "RULE-PROJECT", "RULE-MODULE", "RULE-EVENT"})
        self.assertEqual(result["context"]["features"], ["SceneEvent"])
        self.assertEqual({r["id"] for r in self.search()["rules"]}, {"RULE-GLOBAL", "RULE-PROJECT", "RULE-MODULE"})
        self.assertNotIn("RULE-EVENT", {r["id"] for r in self.search(features=("Motion",))["rules"]})

    def test_precise_feature_can_match_without_repeating_rule_keywords(self):
        result = self.search("新的功能开发", features=("场景事件",))
        self.assertEqual([r["id"] for r in result["rules"]], ["RULE-EVENT"])
        self.assertEqual(result["rules"][0]["matched_feature"], "SceneEvent")
        self.assertFalse(result["rules"][0]["required"])

    def test_source_path_routing_and_unknown_path_warning(self):
        result = self.search(paths=(r"svr_scene\src\scene\logic_scene_event.cpp",))
        self.assertEqual(result["context"]["features"], ["SceneEvent"])
        self.assertIn("RULE-EVENT", {r["id"] for r in result["rules"]})
        unknown = self.search(paths=("svr_scene/src/scene/scene.cpp",))
        self.assertEqual(unknown["context"]["features"], [])
        self.assertTrue(unknown["warnings"])
        self.assertNotIn("RULE-EVENT", {r["id"] for r in unknown["rules"]})

    def test_feature_discovery_merges_rules_without_inventing_routing(self):
        scopes = self.service.scopes()["scopes"]
        module = next(m for m in scopes[0]["modules"] if m["id"] == "svr_scene")
        self.assertEqual({f["id"] for f in module["features"]}, {"SceneEvent", "Motion"})
        motion = next(f for f in module["features"] if f["id"] == "Motion")
        self.assertEqual(motion["paths"], [])
        self.assertEqual(self.service.list()["scopes"], scopes)

    def test_bad_context_and_ambiguous_catalog_are_rejected(self):
        for context in ({"features": ("SceneEvent",)}, {"project": "server", "modules": ("svr_scene", "svr_user"), "features": ("SceneEvent",)}):
            with self.assertRaises(MemoryError):
                SearchRequest("query", **context)
        with self.assertRaises(MemoryError):
            self.search(features=("SceneEvnet",))
        with self.assertRaises(MemoryError):
            self.search(paths=("../other/file.cpp",))
        with self.assertRaises(MemoryError):
            normalize_scopes([{"id": "server", "modules": [{"id": "scene", "features": [{"id": "SceneEvent", "aliases": ["event"]}, {"id": "Other", "aliases": ["EVENT"]}]}]}])

    def test_schema_requires_complete_feature_ancestry(self):
        valid = self.service.get("RULE-EVENT")["rule"]
        validate_rule(valid)
        for field, value in (("projects", []), ("modules", []), ("features", []), ("level", "module")):
            invalid = copy.deepcopy(valid)
            invalid["scope"][field] = value
            with self.assertRaises(MemoryError):
                validate_rule(invalid)

    def test_export_import_keeps_feature_routes_and_evidence(self):
        exported = self.service.export()
        record = self.service.get("RULE-EVENT")
        self.service.delete("RULE-EVENT", record["revision"], "测试功能导出恢复")
        self.service.import_bundle(exported, "skip", "恢复功能规则")
        restored = self.service.get("RULE-EVENT")["rule"]
        self.assertEqual(restored["evidence"], record["rule"]["evidence"])
        self.assertEqual(self.search(paths=("svr_scene/src/scene/logic_scene_event.h",))["context"]["features"], ["SceneEvent"])
