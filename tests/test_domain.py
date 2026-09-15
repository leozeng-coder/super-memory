import copy
import unittest
from pathlib import Path

from support import sample_rule
from super_memory.domain import MemoryError, Record, normalize_scopes, prepare_rule, revision, validate_rule


class DomainTests(unittest.TestCase):
    def test_schema_accepts_a_complete_rule(self):
        validate_rule(sample_rule())

    def test_schema_rejects_bad_types_duplicate_evidence_and_missing_scope(self):
        for change in (
            lambda r: r.update(status=[]),
            lambda r: r.update(confidence=float("nan")),
            lambda r: r["trigger"].update(always_apply="true"),
            lambda r: r["evidence"].append(copy.deepcopy(r["evidence"][0])),
            lambda r: r["evidence"][0].update(id=""),
            lambda r: r["scope"].update(level="module"),
            lambda r: r["lifecycle"].update(created_at="2026-09-15"),
        ):
            rule = sample_rule()
            change(rule)
            with self.assertRaises(MemoryError):
                validate_rule(rule)

    def test_revision_depends_on_content_not_field_order_or_timestamp_only(self):
        rule = sample_rule()
        self.assertEqual(revision(rule), revision(dict(reversed(list(rule.items())))))
        other = copy.deepcopy(rule)
        other["title"] = "新的规则内容"
        self.assertNotEqual(revision(rule), revision(other))

    def test_update_appends_evidence_and_preserves_validation_time(self):
        rule = sample_rule()
        incoming = copy.deepcopy(rule)
        incoming["evidence"][0]["id"] = "EVIDENCE-NEW"
        updated = prepare_rule(incoming, Record(rule, Path("rule.json")))
        self.assertEqual([item["id"] for item in updated["evidence"]], ["EVIDENCE-1", "EVIDENCE-NEW"])
        self.assertIsNone(updated["lifecycle"]["last_validated_at"])
        self.assertEqual(updated["lifecycle"]["created_at"], rule["lifecycle"]["created_at"])
        self.assertEqual(len(prepare_rule(updated, Record(updated, Path("rule.json")))["evidence"]), 2)
        incoming["evidence"] = copy.deepcopy(rule["evidence"])
        incoming["evidence"][0]["summary"] = "篡改原有依据"
        with self.assertRaises(MemoryError) as error:
            prepare_rule(incoming, Record(rule, Path("rule.json")))
        self.assertEqual(error.exception.code, "evidence_conflict")

    def test_scope_catalog_normalizes_labels_and_rejects_duplicate_ids(self):
        scopes = normalize_scopes([{"id": " server ", "modules": [{"id": " scene "}]}])
        self.assertEqual(scopes[0]["label"], "server")
        self.assertEqual(scopes[0]["modules"][0]["id"], "scene")
        with self.assertRaises(MemoryError):
            normalize_scopes([{"id": "same"}, {"id": " same "}])
