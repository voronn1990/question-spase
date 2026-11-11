"""Structural tests for the EXTERNAL_IMPLEMENTATION EVIDENCE subkind.

These fixtures prove only deterministic local validation.  They do not call a
GitHub tool, fetch a remote object, execute a foreign check, or establish that
an external implementation is safe, independent, or applicable.
"""
from __future__ import annotations

import copy
import unittest
from hashlib import sha256

import yaml
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tests.test_model_contracts import CompleteModel, write


COMMIT = "0123456789abcdef0123456789abcdef01234567"
MATERIAL_HASH = "a" * 64
TEST_HASH = "b" * 64


def external_fields(result_id: str = "E-001", status: str = "TESTED") -> dict[str, Any]:
    repo = "example/reference"
    base = f"https://github.com/{repo}"
    return {
        "evidence_subkind": "EXTERNAL_IMPLEMENTATION",
        "authority_class": "C_EXTERNAL_IMPLEMENTATION",
        "reference_status": status,
        "question_ids": ["Q-001"],
        "decision_ids": ["D-001"],
        "discovery": {
            "route": "ALTERNATIVE_MECHANISM",
            "source": "bounded capability-oriented discovery record",
            "tool_id": "approved-read-tool",
            "query": "capability plus declared constraints",
            "retrieved_at": "2026-10-22T00:00:00Z",
        },
        "repository": {
            "full_name": repo,
            "url": base,
            "commit_sha": COMMIT,
            "commit_permalink": f"{base}/commit/{COMMIT}",
        },
        "implementation": {
            "mechanism": "a bounded implemented mechanism observed in exact bytes",
            "inputs": ["declared input class"],
            "conditions": ["declared operating condition"],
            "assumptions": ["declared implementation assumption"],
            "material_refs": [{
                "path": "src/mechanism.py",
                "functions": ["mechanism.apply"],
                "permalink": f"{base}/blob/{COMMIT}/src/mechanism.py",
                "read_scope": "full named source file",
                "content_sha256": MATERIAL_HASH,
                "retrieved_at": "2026-10-22T00:00:00Z",
            }],
        },
        "tests_inspected": [{
            "path": "tests/test_mechanism.py",
            "named_checks": ["test_exact_mechanism"],
            "permalink": f"{base}/blob/{COMMIT}/tests/test_mechanism.py",
            "content_sha256": TEST_HASH,
        }],
        "check_receipts": [{
            "execution_scope": "LOCAL_REPRODUCTION",
            "named_check": "test_exact_mechanism",
            "commit_sha": COMMIT,
            "result": "PASS",
            "receipt_ref": "evidence/raw/local-run-receipt.txt",
            "executed_at": "2026-10-22T00:00:00Z",
        }],
        "version_fix_history": [{
            "kind": "PR",
            "reference": "https://github.com/example/reference/pull/7",
            "state": "FIX_VERIFIED",
            "relevance": "records a bounded historical fix without asserting a current bug",
        }],
        "current_limits": ["observed only under the declared input condition"],
        "maturity": {"status": "UNKNOWN", "basis": "UNKNOWN"},
        "license": {
            "status": "UNKNOWN",
            "identifier": "UNKNOWN",
            "source_path": "UNKNOWN",
            "content_sha256": "UNKNOWN",
            "use_limits": [],
        },
        "origin": {
            "origin_family": "example-reference-primary-family",
            "primary_repository": "UNKNOWN",
            "relationship": "UNKNOWN",
            "evidence_refs": [],
        },
        "interpretation": "the inspected bytes establish only a bounded candidate mechanism",
        "disposition": "DEFER",
        "disposition_basis": "target applicability remains a separately addressable question",
        "transfer": {
            "target_conditions": ["target input and resources require an independent comparison"],
            "applicability_evidence_ids": [],
            "limits": ["no target transfer claim is made"],
            "new_risks": ["target integration risk remains open"],
        },
        "unknown_fields": [
            "maturity.status",
            "maturity.basis",
            "license.status",
            "license.identifier",
            "license.source_path",
            "license.content_sha256",
            "license.use_limits",
            "origin.primary_repository",
            "origin.relationship",
        ],
    }


class GithubEvidenceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.model = CompleteModel(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _replace_hash(value: Any, old: str, new: str) -> Any:
        if isinstance(value, str):
            return new if value == old else value
        if isinstance(value, list):
            return [GithubEvidenceModelTests._replace_hash(item, old, new) for item in value]
        if isinstance(value, dict):
            return {key: GithubEvidenceModelTests._replace_hash(item, old, new) for key, item in value.items()}
        return value

    def replace_evidence(self, fields: dict[str, Any], result_id: str = "E-001") -> None:
        doc = self.model.docs[result_id]
        entry = self.model.results[result_id]
        old_hash = entry["content_hash"]
        doc.update(copy.deepcopy(fields))
        doc["related_ids"] = ["Q-001", "D-001"]
        new_hash = write(self.model.run / entry["path"], doc)
        entry["content_hash"] = new_hash
        for other_id, other_doc in self.model.docs.items():
            if other_id != result_id:
                self.model.docs[other_id] = self._replace_hash(other_doc, old_hash, new_hash)
        for path in sorted((self.model.run / "gates").glob("*.yaml")):
            gate = yaml.safe_load(path.read_text(encoding="utf-8"))
            write(path, self._replace_hash(gate, old_hash, new_hash))

    def external_errors(self) -> list[str]:
        return [
            error for error in self.model.validate("final")
            if error.startswith("external_evidence:")
        ]

    def test_generic_legacy_evidence_remains_compatible(self) -> None:
        self.assertEqual(self.model.validate("final"), [])

    def test_tested_external_evidence_with_truthful_unknowns_is_structurally_valid(self) -> None:
        self.replace_evidence(external_fields())
        self.assertEqual(self.model.validate("final"), [])

    def test_corrected_canonical_template_binds_to_complete_model(self) -> None:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "external-implementation.yaml"
        fields = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        common = self.model.docs["E-001"]
        for field in (
            "schema_version", "id", "run_id", "input_snapshot", "author_role", "agent_id",
            "knowledge_status", "workflow_status", "source_refs", "related_ids", "revision",
            "created_at", "updated_at", "artifact_type", "assignment_id", "attempt_id",
        ):
            fields[field] = common[field]
        material = fields["implementation"]["material_refs"][0]
        raw = b"template example cache"
        digest = sha256(raw).hexdigest()
        material["content_sha256"] = material["cached_artifact"]["sha256"] = digest
        cache = self.model.run / material["cached_artifact"]["path"]
        cache.parent.mkdir()
        cache.write_bytes(raw)
        self.replace_evidence(fields)
        self.assertEqual(self.model.validate("final"), [])

    def test_declared_local_cache_binds_actual_raw_bytes(self) -> None:
        fields = external_fields()
        material = fields["implementation"]["material_refs"][0]
        raw = b"exact source"
        digest = sha256(raw).hexdigest()
        relative = "evidence/raw/E-001-mechanism.bin"
        material.update({"content_sha256": digest, "cached_artifact": {"path": relative, "sha256": digest}})
        self.replace_evidence(fields)
        path = self.model.run / relative
        path.parent.mkdir()
        path.write_bytes(raw)
        self.assertEqual(self.external_errors(), [])
        path.write_bytes(b"drift source")
        prefix = "external_evidence:E-001:material:0:cached_artifact"
        self.assertEqual(self.external_errors(), [prefix + ":source_drift"])
        path.unlink()
        self.assertEqual(self.external_errors(), [prefix + ":source_unavailable"])
        outside = Path(self.temp.name) / "outside.bin"
        outside.write_bytes(raw)
        path.symlink_to(outside)
        self.assertEqual(self.external_errors(), [prefix + ":source_unavailable"])
        material["cached_artifact"]["path"] = "../outside.bin"
        self.replace_evidence(fields)
        self.assertEqual(self.external_errors(), [prefix])

    def test_unknown_scalar_and_collection_closure(self) -> None:
        evidence = external_fields()
        evidence["unknown_fields"].remove("maturity.status")
        evidence["current_limits"] = ["UNKNOWN"]
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn(
            "external_evidence:E-001:unknown_scalar_missing_marker:maturity.status", errors,
        )
        self.assertIn(
            "external_evidence:E-001:unknown_collection_member:current_limits", errors,
        )

    def test_empty_marked_collection_is_unknown_and_unmarked_empty_is_known_none(self) -> None:
        evidence = external_fields()
        evidence["current_limits"] = []
        evidence["unknown_fields"].append("current_limits")
        self.replace_evidence(evidence)
        self.assertEqual(self.external_errors(), [])
        evidence = external_fields()
        evidence["current_limits"] = []
        self.replace_evidence(evidence)
        self.assertEqual(self.external_errors(), [])

    def test_unknown_marker_rejects_broad_typo_and_known_value(self) -> None:
        evidence = external_fields()
        evidence["unknown_fields"].extend([
            "license", "license.identifer", "current_limits",
        ])
        evidence["maturity"]["basis"] = "specific bounded maturity evidence"
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:unknown_field_invalid:license", errors)
        self.assertIn("external_evidence:E-001:unknown_field_invalid:license.identifer", errors)
        self.assertIn(
            "external_evidence:E-001:unknown_scalar_marker_not_unknown:maturity.basis", errors,
        )
        self.assertIn(
            "external_evidence:E-001:unknown_collection_marker_not_empty:current_limits", errors,
        )

    def test_unknown_collection_members_remain_invalid_when_marked(self) -> None:
        evidence = external_fields()
        evidence["current_limits"] = ["UNKNOWN"]
        evidence["unknown_fields"].append("current_limits")
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn(
            "external_evidence:E-001:unknown_collection_member:current_limits", errors,
        )
        self.assertIn(
            "external_evidence:E-001:unknown_collection_marker_not_empty:current_limits", errors,
        )

    def test_common_knowledge_status_unknown_remains_compatible(self) -> None:
        self.model.docs["E-001"]["knowledge_status"] = "UNKNOWN"
        self.replace_evidence(external_fields())
        self.assertEqual(self.model.validate("final"), [])

    def test_abbreviated_sha_and_branch_permalink_are_rejected(self) -> None:
        evidence = external_fields()
        evidence["repository"]["commit_sha"] = "main"
        evidence["repository"]["commit_permalink"] = "https://github.com/example/reference/commit/main"
        evidence["implementation"]["material_refs"][0]["permalink"] = (
            "https://github.com/example/reference/blob/main/src/mechanism.py"
        )
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:repository_commit_sha", errors)
        self.assertIn("external_evidence:E-001:material:0:permalink", errors)

    def test_blob_context_is_not_a_commit_permalink(self) -> None:
        evidence = external_fields()
        evidence["repository"]["commit_permalink"] = (
            f"https://github.com/example/reference/blob/{COMMIT}/src/mechanism.py"
        )
        self.replace_evidence(evidence)
        self.assertIn(
            "external_evidence:E-001:repository_commit_permalink",
            self.external_errors(),
        )

    def test_q_d_union_and_inspected_status_preconditions_are_enforced(self) -> None:
        evidence = external_fields(status="INSPECTED")
        evidence["question_ids"] = ["Q-001", "Q-001"]
        evidence["implementation"]["material_refs"] = []
        self.replace_evidence(evidence)
        self.model.docs["E-001"]["related_ids"] = ["Q-001"]
        self.model.results["E-001"]["content_hash"] = write(
            self.model.run / self.model.results["E-001"]["path"], self.model.docs["E-001"]
        )
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:question_ids", errors)
        self.assertIn("external_evidence:E-001:related_id_missing:D-001", errors)
        self.assertIn("external_evidence:E-001:status_requires_material", errors)

    def test_rejected_status_requires_reject_disposition(self) -> None:
        evidence = external_fields(status="REJECTED")
        evidence["disposition"] = "DEFER"
        self.replace_evidence(evidence)
        self.assertIn(
            "external_evidence:E-001:rejected_requires_reject_disposition",
            self.external_errors(),
        )

    def test_wrong_code_and_test_pins_are_rejected(self) -> None:
        evidence = external_fields()
        evidence["implementation"]["material_refs"][0]["path"] = "../outside.py"
        evidence["tests_inspected"][0]["permalink"] = (
            f"https://github.com/example/reference/blob/{COMMIT}/tests/other.py"
        )
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:material:0:path", errors)
        self.assertIn("external_evidence:E-001:test:0:permalink", errors)

    def test_tested_status_requires_named_receipt(self) -> None:
        evidence = external_fields()
        evidence["check_receipts"] = []
        self.replace_evidence(evidence)
        self.assertIn(
            "external_evidence:E-001:status_requires_check_receipt",
            self.external_errors(),
        )

    def test_transfer_requires_distinct_accepted_applicability_evidence(self) -> None:
        evidence = external_fields(status="TRANSFER_VALIDATED")
        evidence["transfer"]["applicability_evidence_ids"] = ["E-001", "E-MISSING"]
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:transfer_self_reference", errors)
        self.assertIn(
            "external_evidence:E-001:transfer_target_not_accepted_evidence:E-MISSING",
            errors,
        )

    def test_transfer_accepts_distinct_addressable_evidence_target(self) -> None:
        self.model.add_result(
            "E-TARGET-001", "EVIDENCE", "OP-VECTOR", "/root/OP-VECTOR",
            "evidence/E-TARGET-001.yaml", {"observation": "target-side applicability observation"},
        )
        evidence = external_fields(status="TRANSFER_VALIDATED")
        evidence["transfer"]["applicability_evidence_ids"] = ["E-TARGET-001"]
        self.replace_evidence(evidence)
        self.assertEqual(self.external_errors(), [])

    def test_missing_unknown_marker_and_unsupported_independence_assertion_fail(self) -> None:
        evidence = external_fields()
        evidence["unknown_fields"].remove("license.identifier")
        evidence["origin"]["independent_origin"] = True
        self.replace_evidence(evidence)
        errors = self.external_errors()
        self.assertIn("external_evidence:E-001:license_identifier", errors)
        self.assertIn(
            "external_evidence:E-001:unsupported_independence_assertion", errors,
        )

    def test_explicit_transfer_cycle_is_rejected(self) -> None:
        self.model.add_result(
            "E-002", "EVIDENCE", "OP-VECTOR", "/root/OP-VECTOR", "evidence/E-002.yaml"
        )
        first = external_fields("E-001", "TRANSFER_VALIDATED")
        first["transfer"]["applicability_evidence_ids"] = ["E-002"]
        second = external_fields("E-002", "TRANSFER_VALIDATED")
        second["transfer"]["applicability_evidence_ids"] = ["E-001"]
        self.replace_evidence(first, "E-001")
        self.replace_evidence(second, "E-002")
        self.assertIn("external_evidence:transfer_cycle", self.external_errors())


if __name__ == "__main__":
    unittest.main()
