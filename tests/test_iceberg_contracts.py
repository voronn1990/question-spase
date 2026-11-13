"""Positive/negative contracts for the marked Double Iceberg extension."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.test_model_contracts import CompleteModel, digest, load_yaml, model_validation, write


def _replace_exact(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_exact(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_exact(item, old, new) for item in value]
    return new if value == old else value


def _accepted_binding(model: "CompleteIcebergModel", result_id: str) -> dict[str, str]:
    entry = model.results[result_id]
    return {
        "source_kind": "ACCEPTED_RESULT",
        "source_id": result_id,
        "source_sha256": entry["content_hash"],
        "source_path": entry["path"],
    }


def _refresh_hash_bindings(
    model: "CompleteIcebergModel", removed: set[str] | None = None
) -> None:
    """Re-hash a coherent temporary variant and repair its exact consumers."""
    removed = removed or set()
    for result_id in sorted(model.results):
        model.sync(result_id)

    for result_id in ("VIEW-CURRENT-RESULT", "VIEW-DESIRED-RESULT"):
        view = model.docs[result_id]["iceberg_view"]
        updated: list[dict[str, Any]] = []
        for binding in view["source_bindings"]:
            source_id = binding.get("source_id") if isinstance(binding, dict) else None
            if binding.get("source_kind") == "ACCEPTED_RESULT" and source_id in model.results:
                updated.append(_accepted_binding(model, str(source_id)))
            elif source_id not in removed:
                updated.append(binding)
        view["source_bindings"] = updated
        for member in view["members"]:
            source_id = member.get("source_binding", {}).get("source_id")
            if source_id in model.results:
                member["source_binding"] = _accepted_binding(model, str(source_id))
        model.sync(result_id)

    model.projection = {
        kind: {
            **binding,
            "result_sha256": model.results[binding["result_id"]]["content_hash"],
        }
        for kind, binding in model.projection.items()
    }
    view_bindings = list(model.projection.values())
    model.docs["SYNTHESIS-001"]["iceberg_view_bindings"] = copy.deepcopy(view_bindings)
    for result_id in ("S-000", "S-001"):
        model.docs[result_id]["iceberg_trace"]["view_bindings"] = copy.deepcopy(view_bindings)
    model.docs["AUDIT-001"]["iceberg_view_bindings"] = copy.deepcopy(view_bindings)
    for result_id in ("SYNTHESIS-001", "S-000", "S-001", "AUDIT-001"):
        model.sync(result_id)

    for detail in model.docs["AUDIT-001"]["area_details"].values():
        detail["hashes"] = [model.results["S-000"]["content_hash"]]
    model.sync("AUDIT-001")

    for result_id, entry in sorted(model.results.items()):
        if entry["artifact_type"] != "REVIEW":
            continue
        target_id = model.docs[result_id].get("target_result_id")
        if target_id in model.results:
            model.docs[result_id]["target_hash"] = model.results[target_id]["content_hash"]
            model.sync(result_id)

    for number in range(1, 7):
        path = model.run / "gates" / f"GATE-{number}.yaml"
        gate = load_yaml(path)
        gate["required_objects"] = [
            item for item in gate["required_objects"] if item not in removed
        ]
        target_hashes: list[str] = []
        for item in gate["required_objects"]:
            value = (
                model.bank_hash if item == "input/bank.yaml"
                else model.results[item]["content_hash"]
            )
            if value not in target_hashes:
                target_hashes.append(value)
        review_id = gate["content_review"]["result_id"]
        gate["content_review"]["sha256"] = model.results[review_id]["content_hash"]
        gate["content_review"]["target_hashes"] = target_hashes
        for check in gate["completed_checks"]:
            evidence_id = check["evidence_result_id"]
            if evidence_id in model.results:
                check["evidence_sha256"] = model.results[evidence_id]["content_hash"]
        for reference in gate["evidence_refs"]:
            evidence_id = reference["result_id"]
            if evidence_id in model.results:
                reference["sha256"] = model.results[evidence_id]["content_hash"]
        write(path, gate)


def _use_only_original_question(model: "CompleteIcebergModel") -> None:
    source = model.docs["SOURCE-OUTCOMES-001"]
    source["records"] = [
        record for record in source["records"]
        if record.get("record_kind") != "DERIVED_Q"
    ]
    for result_id in list(model.docs):
        model.docs[result_id] = _replace_exact(
            model.docs[result_id], "Q-ICE-001", "Q-001"
        )
    for result_id in ("VIEW-CURRENT-RESULT", "VIEW-DESIRED-RESULT"):
        model.docs[result_id]["iceberg_view"]["open_q_ids"] = []
    _refresh_hash_bindings(model)


def _remove_hypothesis_obligation(model: "CompleteIcebergModel") -> None:
    removed = {"H-001", "REVIEW-H-001"}
    for result_id in removed:
        model.results.pop(result_id)
        model.docs.pop(result_id)

    current = model.docs["VIEW-CURRENT-RESULT"]["iceberg_view"]
    e_source = _accepted_binding(model, "E-001")
    current["source_bindings"] = [e_source]
    current["members"] = [{
        "element_kind": "OBSERVATION",
        "ref_type": "E",
        "ref_id": "E-001",
        "canonical_claim_path": "/observation",
        "source_binding": e_source,
        "origin_position_refs": [model.origin(1), model.origin(2)],
        "materiality": "supports the bounded selected mechanism",
        "scope": "bounded fixture scope",
    }]
    current["semantic_edge_ids"] = ["EDGE-OBSERVED"]
    model.docs["VIEW-DESIRED-RESULT"]["iceberg_view"]["semantic_edge_ids"] = [
        "EDGE-NECESSARY", "EDGE-BYPASS",
    ]

    synthesis = model.docs["SYNTHESIS-001"]
    synthesis["semantic_edges"] = [
        model.edge("EDGE-OBSERVED", "CORRELATES_WITH", "E", "E-001", "D", "D-001"),
        model.edge("EDGE-NECESSARY", "NECESSARY_FOR", "R", "R-001", "G", "VECTOR-001"),
        model.edge("EDGE-BYPASS", "BYPASSES", "R", "R-001", "D", "D-001"),
    ]
    synthesis["execution_edges"] = [
        edge for edge in synthesis["execution_edges"]
        if edge.get("from_id") not in removed and edge.get("to_id") not in removed
    ]
    synthesis["iceberg_mechanism_trace"]["chosen"]["member_refs"] = [
        "ICE-CURRENT-001#E-001", "ICE-DESIRED-001#R-001",
    ]
    synthesis["iceberg_mechanism_trace"]["chosen"]["edge_ids"] = [
        "EDGE-OBSERVED", "EDGE-NECESSARY",
    ]
    for row in synthesis["iceberg_mechanism_trace"]["alternatives"]:
        row["member_refs"] = ["ICE-CURRENT-001#E-001"]
        row["edge_ids"] = ["EDGE-BYPASS"]

    for result_id in ("S-000", "S-001"):
        model.docs[result_id]["iceberg_trace"]["capability_or_unknown_refs"] = ["E-001"]
    replacements = {
        "missing_or_shared_assumption": "E-001",
        "false_necessity_or_causality": "EDGE-OBSERVED",
        "impossible_capability": "ICE-DESIRED-001",
        "missed_failure_combination": "EDGE-NECESSARY",
        "stronger_bypass": "EDGE-BYPASS",
    }
    for key, affected in replacements.items():
        model.docs["AUDIT-001"]["iceberg_probes"][key]["affected_ids"] = [affected]
    _refresh_hash_bindings(model, removed)


class CompleteIcebergModel(CompleteModel):
    def __init__(self, base: Path) -> None:
        super().__init__(base)
        self._add_iceberg_contract()

    def sync(self, result_id: str) -> None:
        entry = self.results[result_id]
        entry["content_hash"] = write(self.run / entry["path"], self.docs[result_id])

    def origin(self, role_number: int) -> dict[str, str]:
        result_id = f"FRAME-P-{role_number:02d}"
        return {
            "framing_result_id": result_id,
            "framing_result_sha256": self.results[result_id]["content_hash"],
            "position_id": f"ICEPOS-P-{role_number:02d}",
            "seed_id": f"SEED-P-{role_number:02d}",
            "origin_role": f"P-{role_number:02d}",
        }

    def edge(
        self,
        edge_id: str,
        relation: str,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        combination_kind: str = "SINGLE",
        **extra: Any,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": edge_id,
            "relation": relation,
            "from_type": from_type,
            "from_id": from_id,
            "to_type": to_type,
            "to_id": to_id,
            "scope": "bounded fixture scope",
            "combination_kind": combination_kind,
            "knowledge_status": "DERIVED",
            "evidence_ids": ["E-001"],
            "distinguishing_check_id": "Q-ICE-001",
        }
        value.update(extra)
        return value

    def _add_iceberg_contract(self) -> None:
        write(self.run / "input" / "snapshot.yaml", {
            "schema_version": "question-space-v4",
            "run_id": "RUN-1",
            "input_mode": "GOAL_TO_PLAN",
            "methodology_features": ["DOUBLE_ICEBERG_V1"],
        })
        vector_hash = self.results["VECTOR-001"]["content_hash"]
        approval_hash = write(self.run / "input" / "vector-approval.yaml", {
            "schema_version": "question-space-v4",
            "approval_id": "APPROVAL-001",
            "run_id": "RUN-1",
            "input_snapshot": self.snapshot,
            "vector_result_id": "VECTOR-001",
            "vector_sha256": vector_hash,
            "vector_revision": "1",
            "decision": "APPROVE",
        })
        for number in range(1, 11):
            result_id = f"FRAME-P-{number:02d}"
            doc = self.docs[result_id]
            doc["vector_sha256"] = vector_hash
            doc["iceberg_position"] = {
                "position_id": f"ICEPOS-P-{number:02d}",
                "origin_role": f"P-{number:02d}",
                "origin_assignment_id": doc["assignment_id"],
                "input_snapshot": self.snapshot,
                "elements": [{
                    "seed_id": f"SEED-P-{number:02d}",
                    "candidate_view": "CURRENT_REALITY" if number % 2 else "DESIRED_REALITY",
                    "element_kind": "ASSUMPTION" if number % 2 else "CAPABILITY",
                    "statement": f"bounded blind seed {number}",
                    "scope": "bounded fixture scope",
                    "g_refs": ["VECTOR-001"],
                    "r_refs": ["R-001"],
                    "materiality": "changes Q-ICE-001 or D-001",
                    "knowledge_status": "HYPOTHESIS",
                    "source_refs": [],
                    "finite_check": {
                        "check_kind": "OBSERVATION",
                        "procedure": "perform one bounded distinguishing observation",
                        "discriminating_outcomes": ["supports", "refutes", "remains unknown"],
                    },
                }],
                "relations": [],
            }
            self.sync(result_id)

        # Existing reviews bind exact blind position bytes.
        for result_id, entry in self.results.items():
            if entry["artifact_type"] != "REVIEW":
                continue
            review = self.docs[result_id]
            framing_id = "FRAME-" + entry["role_id"]
            if framing_id in self.results:
                review["blind_position_hash"] = self.results[framing_id]["content_hash"]
                review["blind_input_hash"] = self.results[framing_id]["content_hash"]
                self.sync(result_id)

        origins = [self.origin(1), self.origin(2)]
        self.add_result(
            "H-001", "HYPOTHESIS", "OP-DIVERGENCE", "/root/OP-DIVERGENCE",
            "hypotheses/H-001.yaml",
            {
                "knowledge_status": "HYPOTHESIS",
                "source_refs": ["FRAME-P-01", "FRAME-P-02"],
                "claim": "two blind roles share a bounded testable premise",
                "scope": "bounded fixture scope",
                "materiality": "changes Q-ICE-001 and D-001",
                "origin_position_refs": origins,
                "finite_check": {
                    "check_kind": "EXPERIMENT",
                    "procedure": "run one finite discriminating experiment",
                    "discriminating_outcomes": ["supports", "refutes", "remains unknown"],
                },
                "dependent_ids": ["Q-ICE-001", "D-001"],
            },
        )
        self.add_review("REVIEW-H-001", 3, "H-001", "SUBJECT", "H-001")

        source = self.docs["SOURCE-OUTCOMES-001"]
        source["records"].append({
            "id": "DERIVED-Q-ICE-001",
            "record_kind": "DERIVED_Q",
            "q_id": "Q-ICE-001",
            "parent_source_ids": ["Q-001"],
            "materiality": "answer changes D-001",
            "origin_position_refs": origins,
            "finite_check": {
                "check_kind": "EXPERIMENT",
                "procedure": "run one finite distinguishing experiment",
                "discriminating_outcomes": ["supports", "refutes", "remains unknown"],
            },
            "equivalent_q_ids": [],
            "resource_cap_impact": "one bounded question within the declared cap",
            "evidence_ids": ["E-001"],
        })
        self.sync("SOURCE-OUTCOMES-001")

        edges = [
            self.edge("EDGE-AND-1", "NECESSARY_FOR", "H", "H-001", "G", "VECTOR-001", "AND", combination_id="COMBO-AND"),
            self.edge("EDGE-AND-2", "NECESSARY_FOR", "R", "R-001", "G", "VECTOR-001", "AND", combination_id="COMBO-AND"),
            self.edge("EDGE-OR-1", "ALTERNATIVE_TO", "H", "H-001", "D", "D-001", "OR", combination_id="COMBO-OR"),
            self.edge("EDGE-OR-2", "BYPASSES", "R", "R-001", "D", "D-001", "OR", combination_id="COMBO-OR"),
            self.edge("EDGE-SEQ-1", "ENABLES", "E", "E-001", "H", "H-001", "SEQUENCE", combination_id="COMBO-SEQ", sequence_index="1"),
            self.edge("EDGE-SEQ-2", "ENABLES", "H", "H-001", "D", "D-001", "SEQUENCE", combination_id="COMBO-SEQ", sequence_index="2"),
            self.edge(
                "EDGE-CAUSE", "CAUSAL_INFLUENCE", "E", "E-001", "H", "H-001",
                causal_confirmation={
                    "intervention_evidence_ids": ["E-001"],
                    "temporal_order_evidence_ids": ["E-001"],
                    "mechanism_evidence_ids": ["E-001"],
                    "competing_explanation_evidence_ids": ["E-001"],
                },
            ),
            self.edge(
                "EDGE-HYP", "CAUSES_HYPOTHESIS", "H", "H-001", "D", "D-001",
                knowledge_status="HYPOTHESIS", evidence_ids=[],
            ),
            self.edge("EDGE-CYCLE-1", "FEEDBACK_WITH", "H", "H-001", "D", "D-001"),
            self.edge("EDGE-CYCLE-2", "FEEDBACK_WITH", "D", "D-001", "H", "H-001"),
        ]
        self.docs["SYNTHESIS-001"]["semantic_edges"] = edges
        execution = self.docs["SYNTHESIS-001"]["execution_edges"]
        execution.extend([
            {"from_type": "E", "from_id": "E-001", "to_type": "H", "to_id": "H-001"},
            {"from_type": "H", "from_id": "H-001", "to_type": "Q", "to_id": "Q-ICE-001"},
            {"from_type": "Q", "from_id": "Q-ICE-001", "to_type": "D", "to_id": "D-001"},
            {"from_type": "D", "from_id": "D-001", "to_type": "REVIEW", "to_id": "REVIEW-H-001"},
            {"from_type": "REVIEW", "from_id": "REVIEW-H-001", "to_type": "T", "to_id": "T-000"},
        ])

        anchor = {
            "anchor_kind": "LOCAL_ACCEPTED_VECTOR",
            "vector_id": "VECTOR-001",
            "vector_sha256": vector_hash,
            "revision": "1",
            "approval_binding_id": "APPROVAL-001",
            "approval_sha256": approval_hash,
            "applicability_result_id": "VECTOR-001",
            "goal_claim_path": "/goal",
        }
        h_source = {
            "source_kind": "ACCEPTED_RESULT",
            "source_id": "H-001",
            "source_sha256": self.results["H-001"]["content_hash"],
            "source_path": self.results["H-001"]["path"],
        }
        vector_source = {
            "source_kind": "ACCEPTED_RESULT",
            "source_id": "VECTOR-001",
            "source_sha256": vector_hash,
            "source_path": self.results["VECTOR-001"]["path"],
        }
        self.add_result(
            "VIEW-CURRENT-RESULT", "OPERATOR", "OP-PROJECTION", "/root/OP-PROJECTION",
            "operators/VIEW-CURRENT-RESULT.yaml",
            {"iceberg_view": {
                "view_id": "ICE-CURRENT-001",
                "view_kind": "CURRENT_REALITY",
                "revision": "1",
                "input_snapshot": self.snapshot,
                "normative_vector_binding": anchor,
                "source_bindings": [h_source],
                "members": [{
                    "element_kind": "ASSUMPTION",
                    "ref_type": "H",
                    "ref_id": "H-001",
                    "canonical_claim_path": "/claim",
                    "source_binding": h_source,
                    "origin_position_refs": origins,
                    "materiality": "changes Q-ICE-001 and D-001",
                    "scope": "bounded fixture scope",
                }],
                "semantic_edge_ids": ["EDGE-AND-1", "EDGE-HYP", "EDGE-CYCLE-1"],
                "open_q_ids": ["Q-ICE-001"],
                "coverage_limit": "known material conditions and declared unknowns",
                "knowledge_status": "DERIVED",
                "workflow_status": "ACCEPTED",
            }},
        )
        self.add_result(
            "VIEW-DESIRED-RESULT", "OPERATOR", "OP-REVERSE", "/root/OP-REVERSE",
            "operators/VIEW-DESIRED-RESULT.yaml",
            {"iceberg_view": {
                "view_id": "ICE-DESIRED-001",
                "view_kind": "DESIRED_REALITY",
                "revision": "1",
                "input_snapshot": self.snapshot,
                "normative_vector_binding": anchor,
                "source_bindings": [vector_source],
                "members": [{
                    "element_kind": "CAPABILITY",
                    "ref_type": "R",
                    "ref_id": "R-001",
                    "canonical_claim_path": "/requirements/0",
                    "source_binding": vector_source,
                    "origin_position_refs": origins,
                    "materiality": "changes the chosen mechanism",
                    "scope": "bounded fixture scope",
                }],
                "semantic_edge_ids": ["EDGE-AND-2", "EDGE-SEQ-1", "EDGE-SEQ-2"],
                "open_q_ids": ["Q-ICE-001"],
                "coverage_limit": "known capabilities and declared alternatives",
                "knowledge_status": "DERIVED",
                "workflow_status": "ACCEPTED",
            }},
        )
        self.projection = {
            "CURRENT_REALITY": {
                "view_id": "ICE-CURRENT-001", "view_kind": "CURRENT_REALITY",
                "result_id": "VIEW-CURRENT-RESULT",
                "result_sha256": self.results["VIEW-CURRENT-RESULT"]["content_hash"],
                "revision": "1",
            },
            "DESIRED_REALITY": {
                "view_id": "ICE-DESIRED-001", "view_kind": "DESIRED_REALITY",
                "result_id": "VIEW-DESIRED-RESULT",
                "result_sha256": self.results["VIEW-DESIRED-RESULT"]["content_hash"],
                "revision": "1",
            },
        }
        bindings = list(self.projection.values())
        synthesis = self.docs["SYNTHESIS-001"]
        synthesis["iceberg_view_bindings"] = copy.deepcopy(bindings)
        synthesis["iceberg_mechanism_trace"] = {
            "chosen": {
                "member_refs": ["ICE-CURRENT-001#H-001", "ICE-DESIRED-001#R-001"],
                "edge_ids": ["EDGE-AND-1", "EDGE-SEQ-2"],
                "e_refs": ["E-001"],
                "d_refs": ["D-001"],
            },
            "alternatives": [{
                "alternative_id": alternative["id"],
                "disposition": "REJECTED_WITH_BASIS",
                "member_refs": ["ICE-CURRENT-001#H-001"],
                "edge_ids": ["EDGE-OR-1"],
                "e_refs": ["E-001"],
                "d_refs": ["D-001"],
            } for alternative in synthesis["alternatives"]],
        }
        self.sync("SYNTHESIS-001")

        for result_id in ("S-000", "S-001"):
            stage = self.docs[result_id]
            task = stage["tasks"][0]["id"]
            check = stage["acceptance_exit"][0]["id"]
            stage["iceberg_trace"] = {
                "view_bindings": copy.deepcopy(bindings),
                "g_refs": ["VECTOR-001"],
                "r_refs": ["R-001"],
                "capability_or_unknown_refs": ["H-001"],
                "q_refs": ["Q-ICE-001"],
                "e_refs": ["E-001"],
                "d_refs": ["D-001"],
                "stage_refs": [result_id],
                "task_refs": [task],
                "check_refs": [check],
                "useful_result": "one observable result tied to G and R",
            }
            self.sync(result_id)

        audit = self.docs["AUDIT-001"]
        for detail in audit["area_details"].values():
            detail["hashes"] = [self.results["S-000"]["content_hash"]]
        audit["iceberg_view_bindings"] = copy.deepcopy(bindings)
        audit["iceberg_probes"] = {
            "missing_or_shared_assumption": self.probe("H-001"),
            "false_necessity_or_causality": self.probe("EDGE-CAUSE"),
            "impossible_capability": self.probe("ICE-DESIRED-001"),
            "missed_failure_combination": self.probe("COMBO-AND"),
            "stronger_bypass": self.probe("D-001"),
        }
        self.sync("AUDIT-001")

        # Rebind reviews whose target bytes changed.
        for result_id, entry in self.results.items():
            if entry["artifact_type"] != "REVIEW":
                continue
            review = self.docs[result_id]
            target_id = review.get("target_result_id")
            if target_id in self.results:
                review["target_hash"] = self.results[target_id]["content_hash"]
                self.sync(result_id)

        for number in range(1, 7):
            review_id = f"REVIEW-GATE-{number}"
            evidence_id = "AUDIT-001" if number == 6 else "E-001"
            gate = self.gate(number, review_id, evidence_id)
            additions: list[str] = []
            if number == 2:
                additions.append("E-001")
            if number == 3:
                additions.append("REVIEW-H-001")
            if number >= 4:
                additions.extend(["VIEW-CURRENT-RESULT", "VIEW-DESIRED-RESULT"])
            for result_id in additions:
                if result_id not in gate["required_objects"]:
                    gate["required_objects"].append(result_id)
                value = self.results[result_id]["content_hash"]
                if value not in gate["content_review"]["target_hashes"]:
                    gate["content_review"]["target_hashes"].append(value)
            write(self.run / "gates" / f"GATE-{number}.yaml", gate)

    def probe(self, affected_id: str) -> dict[str, Any]:
        return {
            "procedure": "run one independent bounded counterexample probe",
            "result": "no structural counterexample in the fixture scope",
            "evidence_refs": ["E-001"],
            "affected_ids": [affected_id],
        }


class DoubleIcebergContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.model = CompleteIcebergModel(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_positive(self) -> None:
        self.assertEqual(self.model.validate(), [])

    def test_marked_complete_model_and_public_projection_pass(self) -> None:
        self.assert_positive()
        projection, historical, errors = model_validation.current_iceberg_view_projection(
            self.model.results, self.model.docs, input_snapshot=self.model.snapshot
        )
        self.assertEqual(projection, self.model.projection)
        self.assertEqual(historical, set())
        self.assertEqual(errors, [])

    def test_unmarked_legacy_model_remains_compatible(self) -> None:
        legacy = CompleteModel(Path(self.temp.name) / "legacy")
        self.assertEqual(legacy.validate(), [])
        self.assertFalse(model_validation.double_iceberg_enabled(None))

    def test_zero_new_q_or_h_obligations_pass_core_and_final(self) -> None:
        no_derived = CompleteIcebergModel(Path(self.temp.name) / "no-derived")
        _use_only_original_question(no_derived)
        self.assertEqual(no_derived.validate("core"), [])
        self.assertEqual(no_derived.validate("final"), [])

        no_hypothesis = CompleteIcebergModel(Path(self.temp.name) / "no-hypothesis")
        _remove_hypothesis_obligation(no_hypothesis)
        self.assertEqual(no_hypothesis.validate("core"), [])
        self.assertEqual(no_hypothesis.validate("final"), [])

        no_new_obligation = CompleteIcebergModel(Path(self.temp.name) / "no-new-obligation")
        _remove_hypothesis_obligation(no_new_obligation)
        _use_only_original_question(no_new_obligation)
        self.assertEqual(no_new_obligation.validate("core"), [])
        self.assertEqual(no_new_obligation.validate("final"), [])

    def test_present_q_h_and_h_review_obligations_remain_strict(self) -> None:
        malformed_q = CompleteIcebergModel(Path(self.temp.name) / "malformed-q")
        derived_q = next(
            record for record in malformed_q.docs["SOURCE-OUTCOMES-001"]["records"]
            if record.get("record_kind") == "DERIVED_Q"
        )
        derived_q.pop("materiality")
        self.assertIn(
            "iceberg_derived_q:Q-ICE-001:contract",
            malformed_q.validate("core"),
        )

        malformed_h = CompleteIcebergModel(Path(self.temp.name) / "malformed-h")
        malformed_h.docs["H-001"]["materiality"] = ""
        self.assertIn(
            "iceberg_hypothesis:H-001:substance",
            malformed_h.validate("core"),
        )

        missing_h_review = CompleteIcebergModel(Path(self.temp.name) / "missing-h-review")
        review = missing_h_review.docs["REVIEW-H-001"]
        review["target_result_id"] = "D-001"
        _refresh_hash_bindings(missing_h_review)
        self.assertIn(
            "double_iceberg:hypothesis_review_missing",
            missing_h_review.validate("core"),
        )

    def test_marked_missing_position_and_views_cannot_be_ready(self) -> None:
        self.assert_positive()
        self.model.docs["FRAME-P-01"].pop("iceberg_position")
        self.model.docs["VIEW-CURRENT-RESULT"].pop("iceberg_view")
        errors = self.model.validate()
        self.assertIn("iceberg_position:FRAME-P-01:contract", errors)
        self.assertIn("iceberg_view:CURRENT_REALITY:missing", errors)
        self.assertIn("double_iceberg:view_head_coverage:1", errors)

    def test_common_origin_and_unavailable_check_block_false_ready(self) -> None:
        self.assert_positive()
        self.model.docs["H-001"]["origin_position_refs"] = [self.model.origin(1)]
        seed = self.model.docs["FRAME-P-01"]["iceberg_position"]["elements"][0]
        seed["finite_check"] = {
            "check_kind": "NOT_CURRENTLY_AVAILABLE",
            "procedure": "the bounded observation cannot currently be run",
            "discriminating_outcomes": ["later support", "later refutation"],
            "unavailable_basis": "required source is unavailable in the declared window",
        }
        errors = self.model.validate()
        self.assertIn("iceberg_hypothesis:H-001:shared_origin_roles", errors)
        self.assertIn("iceberg_audit:AUDIT-001:ready_with_unavailable_dependent_check", errors)

    def test_combinations_and_causal_categories_fail_closed(self) -> None:
        self.assert_positive()
        synthesis = self.model.docs["SYNTHESIS-001"]
        by_id = {edge["id"]: edge for edge in synthesis["semantic_edges"]}
        by_id["EDGE-SEQ-2"]["sequence_index"] = "1"
        by_id["EDGE-HYP"]["knowledge_status"] = "DERIVED"
        by_id["EDGE-CAUSE"]["causal_confirmation"]["intervention_evidence_ids"] = []
        errors = self.model.validate()
        self.assertIn("iceberg_combination:COMBO-SEQ:sequence", errors)
        self.assertIn("iceberg_edge:EDGE-HYP:causal_hypothesis_status", errors)
        self.assertIn("iceberg_edge:EDGE-CAUSE:causal_confirmation:intervention_evidence_ids", errors)

    def test_semantic_cycle_allowed_but_execution_cycle_rejected(self) -> None:
        self.assert_positive()
        self.model.docs["SYNTHESIS-001"]["execution_edges"].append(
            {"from_type": "D", "from_id": "D-001", "to_type": "H", "to_id": "H-001"}
        )
        self.assertIn("execution_graph:cycle", self.model.validate())

    def test_knowledge_status_does_not_replace_workflow_acceptance(self) -> None:
        self.assert_positive()
        view = self.model.docs["VIEW-CURRENT-RESULT"]["iceberg_view"]
        view["knowledge_status"] = "HYPOTHESIS"
        self.assertEqual(self.model.validate(), [])
        view["workflow_status"] = "NEEDS_RECHECK"
        errors = self.model.validate()
        self.assertIn("iceberg_view:VIEW-CURRENT-RESULT:stale_head", errors)
        self.assertIn("iceberg_view:VIEW-CURRENT-RESULT:status_or_coverage", errors)

    def test_observed_seed_requires_source_and_consumers_bind_exact_heads(self) -> None:
        self.assert_positive()
        seed = self.model.docs["FRAME-P-01"]["iceberg_position"]["elements"][0]
        seed["knowledge_status"] = "OBSERVED"
        synthesis = self.model.docs["SYNTHESIS-001"]
        synthesis["iceberg_view_bindings"][0]["result_sha256"] = "0" * 64
        errors = self.model.validate()
        self.assertIn("iceberg_position:FRAME-P-01:element:0:observed_without_source", errors)
        self.assertIn("iceberg_synthesis:SYNTHESIS-001:view_bindings", errors)

    def test_gate_four_requires_both_exact_view_results(self) -> None:
        self.assert_positive()
        path = self.model.run / "gates" / "GATE-4.yaml"
        gate = load_yaml(path)
        gate["required_objects"].remove("VIEW-CURRENT-RESULT")
        gate["content_review"]["target_hashes"].remove(
            self.model.results["VIEW-CURRENT-RESULT"]["content_hash"]
        )
        write(path, gate)
        self.assertIn(
            "gate:GATE-4:missing_required_object:VIEW-CURRENT-RESULT",
            self.model.validate(),
        )

    def test_view_hash_version_gap_and_branch_fail_closed(self) -> None:
        self.assert_positive()
        old = copy.deepcopy(self.model.docs["VIEW-CURRENT-RESULT"])
        old["id"] = "VIEW-CURRENT-OLD"
        old["assignment_id"] = "ASG-VIEW-CURRENT-OLD"
        old["attempt_id"] = "ATT-OLD"
        old["iceberg_view"]["view_id"] = "ICE-CURRENT-000"
        old["iceberg_view"]["revision"] = "1"
        self.model.add_result(
            "VIEW-CURRENT-OLD", "OPERATOR", "OP-PROJECTION", "/root/OP-PROJECTION",
            "operators/VIEW-CURRENT-OLD.yaml",
            {key: value for key, value in old.items() if key not in {
                "schema_version", "id", "run_id", "input_snapshot", "author_role",
                "agent_id", "knowledge_status", "workflow_status", "source_refs",
                "related_ids", "revision", "created_at", "updated_at", "artifact_type",
                "assignment_id", "attempt_id",
            }},
        )
        current = self.model.docs["VIEW-CURRENT-RESULT"]
        current["supersedes"] = "VIEW-CURRENT-OLD"
        current["iceberg_view"]["supersedes_view_id"] = "ICE-CURRENT-000"
        prior_binding = {
            "source_kind": "ACCEPTED_RESULT",
            "source_id": "VIEW-CURRENT-OLD",
            "source_sha256": self.model.results["VIEW-CURRENT-OLD"]["content_hash"],
            "source_path": self.model.results["VIEW-CURRENT-OLD"]["path"],
        }
        current["iceberg_view"]["source_bindings"].append(prior_binding)
        current["iceberg_view"]["revision"] = "3"
        _projection, history, errors = model_validation.current_iceberg_view_projection(
            self.model.results, self.model.docs, input_snapshot=self.model.snapshot
        )
        self.assertEqual(history, {"VIEW-CURRENT-OLD"})
        self.assertIn("iceberg_view:ICE-CURRENT-001:revision_gap", errors)

        prior_binding["source_sha256"] = "0" * 64
        _projection, _history, hash_errors = model_validation.current_iceberg_view_projection(
            self.model.results, self.model.docs, input_snapshot=self.model.snapshot
        )
        self.assertIn("iceberg_view:ICE-CURRENT-001:lineage_source_binding", hash_errors)
        prior_binding["source_sha256"] = self.model.results["VIEW-CURRENT-OLD"]["content_hash"]

        current["iceberg_view"]["revision"] = "2"
        clean_projection, clean_history, clean_errors = model_validation.current_iceberg_view_projection(
            self.model.results, self.model.docs, input_snapshot=self.model.snapshot
        )
        self.assertEqual(clean_errors, [])
        self.assertEqual(clean_history, {"VIEW-CURRENT-OLD"})
        self.assertEqual(clean_projection["CURRENT_REALITY"]["result_id"], "VIEW-CURRENT-RESULT")
        sibling = copy.deepcopy(current)
        sibling["id"] = "VIEW-CURRENT-BRANCH"
        sibling["assignment_id"] = "ASG-VIEW-CURRENT-BRANCH"
        sibling["attempt_id"] = "ATT-BRANCH"
        sibling["iceberg_view"]["view_id"] = "ICE-CURRENT-BRANCH"
        self.model.add_result(
            "VIEW-CURRENT-BRANCH", "OPERATOR", "OP-PROJECTION", "/root/OP-PROJECTION",
            "operators/VIEW-CURRENT-BRANCH.yaml",
            {key: value for key, value in sibling.items() if key not in {
                "schema_version", "id", "run_id", "input_snapshot", "author_role",
                "agent_id", "knowledge_status", "workflow_status", "source_refs",
                "related_ids", "revision", "created_at", "updated_at", "artifact_type",
                "assignment_id", "attempt_id",
            }},
        )
        _projection, _history, errors = model_validation.current_iceberg_view_projection(
            self.model.results, self.model.docs, input_snapshot=self.model.snapshot
        )
        self.assertIn("iceberg_view:ICE-CURRENT-000:branch", errors)
        self.assertIn("iceberg_view:CURRENT_REALITY:head_cardinality:2", errors)

    def test_exact_source_hash_change_invalidates_only_bound_view(self) -> None:
        self.assert_positive()
        self.model.docs["E-001"]["changed"] = "CHANGED"
        current = self.model.docs["VIEW-CURRENT-RESULT"]["iceberg_view"]
        e_source = {
            "source_kind": "ACCEPTED_RESULT",
            "source_id": "E-001",
            "source_sha256": self.model.results["E-001"]["content_hash"],
            "source_path": self.model.results["E-001"]["path"],
        }
        current["source_bindings"].append(e_source)
        errors = self.model.validate()
        self.assertIn("iceberg_view:VIEW-CURRENT-RESULT:changed_source_requires_successor", errors)
        self.assertNotIn("iceberg_view:VIEW-DESIRED-RESULT:changed_source_requires_successor", errors)


class ImportedVectorAnchorTest(unittest.TestCase):
    def test_imported_vector_uses_immutable_anchor_and_applicability_gate_object(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            model = CompleteModel(Path(temp.name))
            vector = copy.deepcopy(model.docs.pop("VECTOR-001"))
            model.results.pop("VECTOR-001")
            vector.update({
                "id": "FOREIGN-VECTOR", "run_id": "SOURCE-RUN", "input_snapshot": "a" * 64,
                "revision": "7", "G": "bounded goal", "NOT_G": ["outside goal"],
            })
            vector.pop("goal", None)
            vector_path = "input/imports/IMP-1/vector.yaml"
            vector_hash = write(model.run / vector_path, vector)
            statement_hash = write(model.run / "input/imports/IMP-1/statement.yaml", {"statement": "approved"})
            approval = {
                "approval_id": "FOREIGN-APP", "run_id": "SOURCE-RUN", "input_snapshot": "a" * 64,
                "vector_result_id": "FOREIGN-VECTOR", "vector_sha256": vector_hash,
                "vector_revision": "7", "decision": "APPROVE", "statement_sha256": statement_hash,
            }
            approval_path = "input/imports/IMP-1/approval.yaml"
            approval_hash = write(model.run / approval_path, approval)
            binding = {
                "source_vector_result_id": "FOREIGN-VECTOR", "source_vector_sha256": vector_hash,
                "source_vector_revision": "7", "vector_path": vector_path,
                "vector_sha256": vector_hash, "source_approval_id": "FOREIGN-APP",
                "source_approval_decision": "APPROVE", "approval_path": approval_path,
                "approval_sha256": approval_hash, "import_payload_sha256": "b" * 64,
            }
            write(model.run / "input/snapshot.yaml", {
                "run_id": "RUN-1", "input_mode": "BANK_TO_PLAN",
                "bank_entry_kind": "APPROVED_RUN_IMPORT", "import_binding": binding,
            })
            write(model.run / "input/prep-admission.yaml", {
                "vector_binding": {"result_id": "FOREIGN-VECTOR", "sha256": vector_hash},
                "approval_binding": {"id": "FOREIGN-APP", "sha256": approval_hash},
            })
            model.add_result(
                "APPLY-BANK", "OPERATOR", "OP-VECTOR", "/root/OP-VECTOR",
                "operators/APPLY-BANK.yaml",
                {
                    "operator_contract": "BANK_APPLICABILITY", "current_run_id": "RUN-1",
                    "current_input_snapshot": model.snapshot,
                    "import_payload_sha256": binding["import_payload_sha256"], "overall": "UNCHANGED",
                },
            )
            for number in range(1, 11):
                result_id = f"FRAME-P-{number:02d}"
                model.docs[result_id]["vector_result_id"] = "FOREIGN-VECTOR"
                model.results[result_id]["content_hash"] = write(
                    model.run / model.results[result_id]["path"], model.docs[result_id]
                )
            # Review hashes follow changed framings and Gate 1 registers applicability, never a fake Vector.
            for result_id, entry in model.results.items():
                if entry["artifact_type"] == "REVIEW":
                    framing_id = "FRAME-" + entry["role_id"]
                    if framing_id in model.results:
                        model.docs[result_id]["blind_position_hash"] = model.results[framing_id]["content_hash"]
                        model.docs[result_id]["blind_input_hash"] = model.results[framing_id]["content_hash"]
                        entry["content_hash"] = write(model.run / entry["path"], model.docs[result_id])
            gate = load_yaml(model.run / "gates" / "GATE-1.yaml")
            gate["content_review"]["sha256"] = model.results["REVIEW-GATE-1"]["content_hash"]
            gate["required_objects"] = [
                "input/bank.yaml", "APPLY-BANK", "S-000",
                *[f"FRAME-P-{number:02d}" for number in range(1, 11)],
            ]
            gate["content_review"]["target_hashes"] = [
                model.bank_hash, model.results["APPLY-BANK"]["content_hash"],
                model.results["S-000"]["content_hash"],
                *[model.results[f"FRAME-P-{number:02d}"]["content_hash"] for number in range(1, 11)],
            ]
            write(model.run / "gates" / "GATE-1.yaml", gate)
            errors = model.validate()
            self.assertNotIn("gate:GATE-1:vector_binding", errors)
            self.assertFalse(any(error.startswith("vector_anchor:IMPORTED_APPROVED_VECTOR") for error in errors))
            self.assertNotIn("gate:GATE-1:missing_required_object:FOREIGN-VECTOR", errors)
            self.assertNotIn("gate:GATE-1:missing_required_object:VECTOR-001", errors)

            (model.run / vector_path).write_text("tampered immutable Vector\n", encoding="utf-8")
            tampered = model.validate()
            self.assertIn("vector_anchor:IMPORTED_APPROVED_VECTOR:vector_binding", tampered)
            self.assertIn("gate:GATE-1:vector_binding", tampered)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
