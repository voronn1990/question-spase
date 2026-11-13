"""Regression counterexamples for revision 4.1 FIX-03 through FIX-06."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests.test_model_contracts import CompleteModel, digest, load_yaml, model_validation, write


class RevisionContractCounterexampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.model = CompleteModel(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_positive_control(self) -> None:
        self.assertEqual(self.model.validate(), [])

    def test_active_critical_decision_projection_keeps_exact_current_review_guards(self) -> None:
        self.assert_positive_control()
        self.model.docs["REVIEW-D-SUBJECT"]["outcome"] = "FAIL"
        self.assertIn("decision:D-001:critical_review_coverage", self.model.validate())
        self.model.add_result(
            "D-002", "DECISION", "OP-SYNTHESIS", "/root/OP-SYNTHESIS", "decisions/D-002.yaml",
            {"criticality":"CRITICAL", "mechanism":"corrected bounded mechanism", "supersedes":"D-001", "related_ids":["D-001"]},
        )

        def validate_active(active: set[str]) -> list[str]:
            return model_validation.validate_model(
                self.model.run, self.model.bank, self.model.results, self.model.docs,
                "final", load_yaml, digest, active_critical_decision_ids=active,
            )

        missing = validate_active({"D-002"})
        self.assertIn("decision:D-002:critical_review_coverage", missing)
        self.assertNotIn("decision:D-001:critical_review_coverage", missing)
        self.model.add_review("REVIEW-D2-SUBJECT", 1, "D-002", "SUBJECT", "D2-SUBJECT")
        self.model.add_review("REVIEW-D2-SYSTEM", 2, "D-002", "SYSTEM", "D2-SYSTEM")
        covered = validate_active({"D-002"})
        self.assertNotIn("decision:D-001:critical_review_coverage", covered)
        self.assertNotIn("decision:D-002:critical_review_coverage", covered)
        self.model.docs["REVIEW-D2-SYSTEM"]["target_hash"] = self.model.results["D-001"]["content_hash"]
        stale = validate_active({"D-002"})
        self.assertIn("review:REVIEW-D2-SYSTEM:target_binding", stale)
        self.assertIn("decision:D-002:critical_review_coverage", stale)
        invalid = validate_active({"D-GHOST"})
        self.assertIn("model:active_critical_decisions_contract", invalid)
        self.assertIn("decision:D-001:critical_review_coverage", invalid)

    def test_01_nonexistent_task_input_is_rejected(self) -> None:
        self.assert_positive_control()
        self.model.docs["S-001"]["tasks"][0]["inputs"] = ["INPUT-DOES-NOT-EXIST"]
        self.assertIn(
            "task:T-001:unknown_input:INPUT-DOES-NOT-EXIST",
            self.model.validate(),
        )

    def test_02_planned_input_has_real_producer_chain_and_no_future_hash(self) -> None:
        self.assert_positive_control()
        planned = self.model.docs["S-001"]["inputs_contracts"]["inputs"][0]
        planned["accepted_hash"] = "f" * 64
        self.assertIn(
            "input_contract:INPUT-T-001:planned_fields_or_future_hash",
            self.model.validate(),
        )

    def test_03_missing_planned_producer_is_rejected(self) -> None:
        self.assert_positive_control()
        planned = self.model.docs["S-001"]["inputs_contracts"]["inputs"][0]
        planned["producer_task_id"] = "T-MISSING"
        self.assertIn(
            "input_contract:INPUT-T-001:planned_chain:T-MISSING:OUTPUT-T-000:C-000",
            self.model.validate(),
        )

    def test_04_unselected_branch_cannot_create_consumable_output(self) -> None:
        self.assert_positive_control()
        output = self.model.docs["S-000"]["inputs_contracts"]["outputs"][0]
        output["availability"] = "UNSELECTED_BRANCH"
        errors = self.model.validate()
        self.assertIn("output_contract:OUTPUT-T-000:unselected_has_consumers", errors)

    def test_selected_branch_requires_selection_and_accepted_output_before_use(self) -> None:
        output = self.model.docs["S-000"]["inputs_contracts"]["outputs"][0]
        output.update({
            "availability": "SELECTED_BRANCH",
            "branch_selection_status": "SELECTED",
            "branch_selection_evidence_id": "E-001",
            "acceptance_status": "ACCEPTED",
            "acceptance_evidence_id": "E-001",
        })
        self.model.docs["S-000"]["tasks"][0]["execution_status"] = "ACCEPTED"
        self.model.docs["S-001"]["tasks"][0]["execution_status"] = "ACCEPTED"
        self.assertEqual(self.model.validate(), [])

        output["branch_selection_status"] = "NOT_SELECTED"
        output["acceptance_status"] = "PLANNED"
        output.pop("acceptance_evidence_id")
        self.model.docs["S-000"]["tasks"][0]["execution_status"] = "NOT_STARTED"
        errors = self.model.validate()
        self.assertIn("output_contract:OUTPUT-T-000:selected_branch_status", errors)
        self.assertIn(
            "output_contract:OUTPUT-T-000:active_consumer_without_accepted_producer:T-001",
            errors,
        )
        self.assertIn(
            "output_contract:OUTPUT-T-000:active_consumer_without_accepted_output:T-001",
            errors,
        )

    def test_external_availability_evidence_controls_every_consumer(self) -> None:
        external = self.model.docs["S-001"]["inputs_contracts"]["inputs"][0]
        external.clear()
        external.update({
            "id": "INPUT-T-001",
            "input_kind": "EXTERNAL",
            "contract": "meaning schema units freshness authority",
            "consumer_ids": ["T-001"],
            "source": "named external provider",
            "owner": "named availability owner",
            "availability_condition": "provider record is accepted and hash bound",
            "availability_status": "AVAILABLE",
            "availability_evidence_id": "E-001",
            "blocked_consumer_ids": [],
        })
        upstream_output = self.model.docs["S-000"]["inputs_contracts"]["outputs"][0]
        upstream_output.update({
            "acceptance_status": "ACCEPTED",
            "acceptance_evidence_id": "E-001",
        })
        self.model.docs["S-000"]["tasks"][0]["execution_status"] = "ACCEPTED"
        self.model.docs["S-001"]["tasks"][0]["execution_status"] = "ACCEPTED"
        self.assertEqual(self.model.validate(), [])

        external["availability_status"] = "UNAVAILABLE"
        external.pop("availability_evidence_id")
        errors = self.model.validate()
        self.assertIn(
            "input_contract:INPUT-T-001:unavailable_consumers_not_blocked", errors
        )
        self.assertIn(
            "input_contract:INPUT-T-001:unavailable_consumer_executed:T-001", errors
        )

    def test_05_agent_selected_gate_objects_cannot_replace_phase_minima(self) -> None:
        self.assert_positive_control()
        for number in range(1, 6):
            path = self.model.run / "gates" / f"GATE-{number}.yaml"
            gate = load_yaml(path)
            gate["required_objects"] = ["S-000"]
            gate["content_review"]["target_hashes"] = [
                self.model.results["S-000"]["content_hash"]
            ]
            write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:missing_required_object:input/bank.yaml", errors)
        self.assertIn("gate:GATE-2:missing_required_object:SOURCE-OUTCOMES-001", errors)
        self.assertIn("gate:GATE-4:missing_required_object:SYNTHESIS-001", errors)
        self.assertIn("gate:GATE-5:missing_required_object:S-001", errors)

    def test_06_synthesis_requires_protocol_mechanism_and_alternatives(self) -> None:
        self.assert_positive_control()
        synthesis = self.model.docs["SYNTHESIS-001"]
        synthesis.pop("comparison_protocol")
        synthesis["chosen_mechanism"] = ""
        synthesis["alternatives"] = []
        errors = self.model.validate()
        self.assertIn("synthesis:SYNTHESIS-001:comparison_protocol", errors)
        self.assertIn("synthesis:SYNTHESIS-001:chosen_mechanism", errors)
        self.assertIn("synthesis:SYNTHESIS-001:alternatives", errors)

    def test_07_noncritical_residual_can_be_ready_but_central_unknown_cannot(self) -> None:
        self.assert_positive_control()
        audit = self.model.docs["AUDIT-001"]
        residual = {
            "id": "OPEN-RESIDUAL",
            "area_ids": ["6"],
            "criticality": "NONCRITICAL",
            "condition": "a bounded future observation remains after plan acceptance",
            "next_path": "run C-000 before the gated dependent action",
            "condition_kind": "FUTURE_CHECK",
            "goal_requirement_impact": "R-001 remains satisfied before the gated action",
            "admissibility_basis": "the check precedes and gates its only dependent action",
            "early_check_id": "C-000",
            "gated_dependency_ids": ["T-001"],
            "affected_requirement_ids": [],
            "affected_decision_ids": [],
        }
        audit["open_conditions"] = [residual]
        audit["area_details"]["6"]["open_condition_ids"] = ["OPEN-RESIDUAL"]
        self.assertEqual(self.model.validate(), [])

        residual["gated_dependency_ids"] = ["D-001"]
        residual["affected_decision_ids"] = ["D-001"]
        residual["affected_requirement_ids"] = ["R-001"]
        errors = self.model.validate()
        self.assertIn(
            "audit:AUDIT-001:noncritical_affects_goal_requirement:OPEN-RESIDUAL",
            errors,
        )
        self.assertIn(
            "audit:AUDIT-001:noncritical_affects_critical_decision:OPEN-RESIDUAL:D-001",
            errors,
        )
        self.assertIn(
            "audit:AUDIT-001:noncritical_dependency_not_task:OPEN-RESIDUAL:D-001",
            errors,
        )
        self.assertIn("audit:AUDIT-001:verdict_coherence:RESEARCH_REQUIRED", errors)

        residual["gated_dependency_ids"] = ["T-001"]
        residual["affected_decision_ids"] = []
        residual["affected_requirement_ids"] = []
        residual["early_check_id"] = "C-001"
        errors = self.model.validate()
        self.assertIn(
            "audit:AUDIT-001:noncritical_check_not_before_dependency:OPEN-RESIDUAL:T-001",
            errors,
        )
        self.assertIn("audit:AUDIT-001:verdict_coherence:RESEARCH_REQUIRED", errors)

        residual["early_check_id"] = "C-000"
        residual["condition_kind"] = "CENTRAL_UNCERTAINTY"
        errors = self.model.validate()
        self.assertIn("audit:AUDIT-001:noncritical_kind:OPEN-RESIDUAL", errors)
        self.assertIn("audit:AUDIT-001:verdict_coherence:RESEARCH_REQUIRED", errors)

    def test_08_evidence_provenance_allows_e_to_e_but_rejects_circular_support(self) -> None:
        self.assert_positive_control()
        self.model.add_result(
            "E-002", "EVIDENCE", "OP-PROJECTION", "/root/OP-PROJECTION",
            "evidence/E-002.yaml",
            {
                "changed": "UNCHANGED",
                "observation": "derived exact observation",
                "source_refs": ["E-001"],
            },
        )
        synthesis = self.model.docs["SYNTHESIS-001"]
        synthesis["evidence_provenance_edges"] = [
            {"from_id": "E-001", "to_id": "E-002"}
        ]
        synthesis["semantic_edges"].append({
            "from_type": "E", "from_id": "E-001",
            "to_type": "E", "to_id": "E-002", "relation": "derived-from",
        })
        synthesis["semantic_edges"].append({
            "from_type": "Q", "from_id": "Q-001",
            "to_type": "E", "to_id": "E-001", "relation": "feedback",
        })
        synthesis["execution_edges"][0] = {
            "from_type": "E", "from_id": "E-002",
            "to_type": "Q", "to_id": "Q-001",
        }
        self.assertEqual(self.model.validate(), [])

        self.model.docs["E-001"]["source_refs"] = ["E-002"]
        synthesis["evidence_provenance_edges"] = []
        errors = self.model.validate()
        self.assertIn(
            "evidence_source_ref:E:E-001->E:E-002:missing_provenance_edge", errors
        )
        self.assertIn(
            "evidence_source_ref:E:E-002->E:E-001:missing_provenance_edge", errors
        )
        self.assertIn("evidence_provenance_graph:cycle", errors)

    def test_current_source_selector_accepts_history_and_rejects_ambiguity(self) -> None:
        self.assert_positive_control()
        old_doc = copy.deepcopy(self.model.docs["SOURCE-OUTCOMES-001"])
        old_doc.update({
            "id": "SOURCE-OUTCOMES-000",
            "revision": "0",
            "assignment_id": "ASG-SOURCE-OUTCOMES-000",
            "attempt_id": "ATT-0",
        })
        self.model.add_result(
            "SOURCE-OUTCOMES-000", "SOURCE_OUTCOMES", "OP-VECTOR", "/root/OP-VECTOR",
            "answers/source-outcomes/SOURCE-OUTCOMES-000.yaml",
            {key: value for key, value in old_doc.items() if key not in {
                "schema_version", "id", "run_id", "input_snapshot", "author_role",
                "agent_id", "knowledge_status", "workflow_status", "source_refs",
                "related_ids", "revision", "created_at", "updated_at", "artifact_type",
                "assignment_id", "attempt_id",
            }},
        )
        ambiguous = self.model.validate()
        self.assertTrue(any(item.startswith("source_outcomes_selection:ambiguous_current:") for item in ambiguous))
        selected = model_validation.validate_model(
            self.model.run, self.model.bank, self.model.results, self.model.docs,
            "final", load_yaml, digest,
            current_source_outcomes_id="SOURCE-OUTCOMES-001",
            current_final_audit_id="AUDIT-001",
        )
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
