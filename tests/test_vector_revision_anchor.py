"""Structural regression for retained accepted local Vector revisions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_architecture_integration import CompleteArchitectureModel
from test_model_contracts import CompleteModel, digest, load_yaml, model_validation, write


ROOT_VECTOR = "VECTOR-001"
HEAD_VECTOR = "VECTOR-003"
LOCAL_ANCHOR = "vector_anchor:LOCAL_ACCEPTED_VECTOR"
LATER_GATES = [f"gate:GATE-{number}:missing_or_invalid" for number in range(2, 6)]


class VectorRevisionAnchorTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.model = CompleteModel(Path(directory.name))

    def add_vector(self, result_id: str, revision: str, **lineage: str) -> None:
        self.model.add_result(
            result_id, "OPERATOR", "OP-VECTOR", "/root/OP-VECTOR",
            f"operators/{result_id}.yaml",
            {"goal": "bounded goal", "requirements": ["R-001"], "revision": revision, **lineage},
        )

    def update_result(self, result_id: str, **fields: str) -> None:
        entry = self.model.results[result_id]
        doc = self.model.docs[result_id]
        doc.update(fields)
        entry["content_hash"] = write(self.model.run / entry["path"], doc)

    def approve(self, result_id: str, **overrides: object) -> None:
        doc = self.model.docs[result_id]
        write(self.model.run / "input/vector-approval.yaml", {
            "approval_id": "APPROVAL-1",
            "decision": "APPROVE",
            "vector_result_id": result_id,
            "vector_revision": doc["revision"],
            "vector_sha256": self.model.results[result_id]["content_hash"],
            **overrides,
        })

    def bind_local_gate(
        self, *, with_history: bool = True, select_original: bool = False,
        mode: str = "GOAL_TO_PLAN",
    ) -> None:
        snapshot = {"input_mode": mode}
        if mode == "BANK_TO_PLAN":
            snapshot["bank_entry_kind"] = "EXTERNAL_READY_BANK"
        write(self.model.run / "input/snapshot.yaml", snapshot)
        selected = ROOT_VECTOR
        if with_history:
            self.add_vector(HEAD_VECTOR, "3", supersedes=ROOT_VECTOR)
            if not select_original:
                selected = HEAD_VECTOR
        self.approve(selected)
        for number in range(1, 11):
            self.update_result(f"FRAME-P-{number:02d}", vector_result_id=selected)
        gate_review_id = "REVIEW-GATE-1"
        for result_id, entry in self.model.results.items():
            if entry["artifact_type"] == "REVIEW":
                framing_hash = self.model.results["FRAME-" + entry["role_id"]]["content_hash"]
                self.update_result(
                    result_id, blind_position_hash=framing_hash, blind_input_hash=framing_hash,
                )
        selected_hash = self.model.results[selected]["content_hash"]
        self.update_result(gate_review_id, target_result_id=selected, target_hash=selected_hash)
        gate = self.model.gate(1, gate_review_id, "E-001")
        old_hash = self.model.results[ROOT_VECTOR]["content_hash"]
        gate["required_objects"] = [
            selected if item == ROOT_VECTOR else item for item in gate["required_objects"]
        ]
        gate["content_review"]["target_hashes"] = [
            selected_hash if value == old_hash else value
            for value in gate["content_review"]["target_hashes"]
        ]
        write(self.model.run / "gates/GATE-1.yaml", gate)
        for number in range(2, 6):
            (self.model.run / f"gates/GATE-{number}.yaml").unlink()

    def goal_context(self) -> dict:
        return model_validation.architecture_context(
            self.model.run, self.model.bank, self.model.results, self.model.docs,
            load_yaml=load_yaml, sha=digest,
        )

    def assert_gate_one_and_anchor(self, expected: str) -> None:
        errors = self.model.validate("core")
        self.assertEqual([error for error in errors if error.startswith("gate:")], LATER_GATES)
        context = self.goal_context()
        self.assertEqual(context["context_errors"], [])
        self.assertEqual(context["goal_ids"], {expected})

    def assert_anchor_rejected(self, reason: str) -> None:
        errors = self.model.validate("core")
        self.assertIn(f"{LOCAL_ANCHOR}:{reason}", errors)
        self.assertIn("gate:GATE-1:vector_binding", errors)
        self.assertEqual(self.goal_context()["goal_ids"], set())

    def test_approved_revision_three_anchors_gate_with_revision_one_retained(self) -> None:
        self.bind_local_gate()
        self.assert_gate_one_and_anchor(HEAD_VECTOR)
        self.assertIn(ROOT_VECTOR, self.model.results)
        self.assertTrue((self.model.run / "operators/VECTOR-001.yaml").is_file())

    def test_external_ready_bank_uses_same_local_history_contract(self) -> None:
        self.bind_local_gate(mode="BANK_TO_PLAN")
        self.assert_gate_one_and_anchor(HEAD_VECTOR)

    def test_local_singleton_remains_supported(self) -> None:
        self.bind_local_gate(with_history=False)
        self.assert_gate_one_and_anchor(ROOT_VECTOR)

    def test_legacy_singleton_remains_supported(self) -> None:
        self.assertEqual(self.model.validate("core"), [])
        self.assertEqual(self.goal_context()["goal_ids"], {ROOT_VECTOR})

    def test_unselected_sibling_and_unrelated_vector_remain_provenance(self) -> None:
        self.bind_local_gate()
        self.add_vector("VECTOR-002", "2", supersedes=ROOT_VECTOR)
        self.add_vector("VECTOR-OTHER", "1")
        self.assert_gate_one_and_anchor(HEAD_VECTOR)

    def test_applicability_is_not_a_normative_vector(self) -> None:
        self.bind_local_gate(mode="BANK_TO_PLAN")
        self.add_vector("APPLY-BANK", "1", operator_contract="BANK_APPLICABILITY")
        self.assert_gate_one_and_anchor(HEAD_VECTOR)
        self.approve("APPLY-BANK")
        self.assert_anchor_rejected("approval_binding")

    def test_registry_document_mismatch_cannot_anchor(self) -> None:
        self.bind_local_gate()
        self.update_result(HEAD_VECTOR, attempt_id="ATT-UNREGISTERED")
        self.approve(HEAD_VECTOR)
        self.assert_anchor_rejected(f"accepted_binding:{HEAD_VECTOR}")

    def test_approval_binding_fields_are_required_and_exact(self) -> None:
        self.bind_local_gate()
        for field, value in (
            ("approval_id", None),
            ("vector_result_id", "VECTOR-ABSENT"),
            ("vector_result_id", ["VECTOR-003"]),
            ("vector_sha256", "0" * 64),
            ("vector_revision", "2"),
            ("decision", "REVISE"),
        ):
            with self.subTest(field=field, value=value):
                self.approve(HEAD_VECTOR, **{field: value})
                self.assert_anchor_rejected("approval_binding")

    def test_initial_approval_can_select_older_accepted_result(self) -> None:
        self.update_result(ROOT_VECTOR, revision="initial-owner-review")
        self.bind_local_gate(select_original=True)
        self.assert_gate_one_and_anchor(ROOT_VECTOR)


    def test_synthesis_intake_rejects_approval_drift_after_admission(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.model = CompleteArchitectureModel(Path(directory.name))
        other_vector = "VECTOR-000"
        self.add_vector(other_vector, "draft-before-approval")
        approval_path = self.model.run / "input/vector-approval.yaml"
        saved_approval = approval_path.read_bytes()
        approval_id = load_yaml(approval_path)["approval_id"]
        admission_path = self.model.run / "input/prep-admission.yaml"
        write(admission_path, {
            "vector_binding": {
                "result_id": ROOT_VECTOR,
                "content_hash": self.model.results[ROOT_VECTOR]["content_hash"],
            },
            "approval_binding": {"content_hash": digest(approval_path.read_bytes())},
        })
        saved_admission = admission_path.read_bytes()
        candidate = self.model.docs["SYNTHESIS-001"]

        def intake_errors() -> list[str]:
            return model_validation.validate_intake_candidate(
                self.model.run, self.model.bank, self.model.results,
                candidate, "SYNTHESIS", load_yaml,
            )

        self.assertEqual(intake_errors(), [])
        for vector_id, selected_approval_id in (
            (ROOT_VECTOR, "REPLACED-APPROVAL"),
            (other_vector, approval_id),
        ):
            with self.subTest(vector_id=vector_id):
                self.approve(vector_id, approval_id=selected_approval_id)
                self.assertIn(f"{LOCAL_ANCHOR}:admission_binding", intake_errors())
                self.assertEqual(self.goal_context()["goal_ids"], set())
                self.assertEqual(admission_path.read_bytes(), saved_admission)

        approval_path.write_bytes(saved_approval)
        admission_path.unlink()
        self.assertEqual(intake_errors(), [])
        outside = Path(directory.name) / "outside-admission.yaml"
        outside.write_bytes(saved_admission)
        for target in (outside, outside.with_name("missing-admission.yaml")):
            with self.subTest(admission_target=target.name):
                admission_path.symlink_to(target)
                try:
                    self.assertIn(f"{LOCAL_ANCHOR}:admission_binding", intake_errors())
                    self.assertEqual(self.goal_context()["goal_ids"], set())
                finally:
                    admission_path.unlink()


if __name__ == "__main__":
    unittest.main()
