from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from orchestration import ROLES, validate_orchestration  # noqa: E402


SNAPSHOT = "s" * 64


class CompleteOrchestration:
    def __init__(self) -> None:
        self.bank = {
            "questions": [
                {"id": "Q-001", "text": "first", "source_offset": "1"},
                {"id": "Q-002", "text": "second", "source_offset": "2"},
            ],
            "requirements": ["R-001"],
        }
        self.evidence_key = {
            "evidence_id": "E-001",
            "content_hash": "hash-evidence",
            "input_snapshot": SNAPSHOT,
            "tool_id": "TOOL-1",
            "tool_version": "1.0",
            "policy_hash": "policy-1",
            "exposure_hash": "exposure-1",
            "freshness": "CURRENT-AS-DECLARED",
        }
        self.approved = {
            "input_snapshot": SNAPSHOT,
            "allowed_roles": sorted(ROLES),
            "role_competence": {
                **{role: [] for role in ROLES},
                "P-01": ["analysis", "domain"],
                "P-02": ["analysis", "domain"],
                "P-03": ["architecture"],
            },
            "question_routes": {
                "Q-001": {"primary_assignment_id": "ASG-Q1", "required_tags": ["analysis"]},
                "Q-002": {"primary_assignment_id": "ASG-Q2", "required_tags": ["analysis"]},
            },
            "critical_reviews": {
                "D-001": {
                    "author_role": "P-01",
                    "subject": {
                        "assignment_id": "ASG-SUBJECT",
                        "role_id": "P-02",
                        "check_id": "CHECK-SUBJECT",
                        "required_tags": ["domain"],
                    },
                    "system": {
                        "assignment_id": "ASG-SYSTEM",
                        "role_id": "P-03",
                        "check_id": "CHECK-SYSTEM",
                        "required_tags": ["architecture"],
                    },
                }
            },
            "evidence_applicability": {"E-001": ["Q-001", "Q-002"]},
        }
        self.results = {
            "RES-E": self.result("hash-evidence", "EVIDENCE", "ASG-E"),
            "RES-Q1": self.result("hash-q1", "PRIMARY", "ASG-Q1"),
            "RES-D": self.result("hash-d", "DECISION", "ASG-D"),
            "RES-BRANCH": self.result("hash-branch", "OPERATOR", "ASG-BRANCH"),
        }
        self.docs = {
            "RES-E": {
                "id": "E-001",
                "artifact_type": "EVIDENCE",
                "input_snapshot": SNAPSHOT,
                "tool_id": "TOOL-1",
                "tool_version": "1.0",
                "policy_hash": "policy-1",
                "exposure_hash": "exposure-1",
                "freshness": "CURRENT-AS-DECLARED",
            },
            "RES-BRANCH": {
                "id": "OP-BRANCH",
                "artifact_type": "OPERATOR",
                "dependency_edges": [
                    {"from_id": "E-001", "to_id": "Q-001"},
                    {"from_id": "E-001", "to_id": "Q-002"},
                    {"from_id": "Q-001", "to_id": "D-001"},
                    {"from_id": "D-001", "to_id": "REVIEW-SUBJECT"},
                    {"from_id": "D-001", "to_id": "REVIEW-SYSTEM"},
                ],
            },
        }
        self.assignments = {
            "E\0A1": self.registered(
                self.assignment("ASG-E", "RES-E", "P-04", "EVIDENCE"), "ACCEPTED"
            ),
            "Q1\0A1": self.registered(
                self.assignment(
                    "ASG-Q1",
                    "RES-Q1",
                    "P-01",
                    "PRIMARY",
                    dependencies=["RES-E"],
                    hashes=["hash-evidence"],
                    assigned={"questions": ["Q-001"], "requirements": ["R-001"], "decisions": [], "evidence": ["E-001"]},
                    evidence=[self.applicability("Q-001")],
                ),
                "ACCEPTED",
            ),
            "D\0A1": self.registered(
                self.assignment(
                    "ASG-D",
                    "RES-D",
                    "P-01",
                    "DECISION",
                    dependencies=["RES-Q1"],
                    hashes=["hash-q1"],
                    assigned={"questions": [], "requirements": ["R-001"], "decisions": ["D-001"], "evidence": []},
                ),
                "ACCEPTED",
            ),
            "BRANCH\0A1": self.registered(
                self.assignment("ASG-BRANCH", "RES-BRANCH", "OP-VECTOR", "OPERATOR"),
                "ACCEPTED",
            ),
            "Q2\0A1": self.registered(
                self.assignment(
                    "ASG-Q2",
                    "RES-Q2",
                    "P-02",
                    "PRIMARY",
                    dependencies=["RES-E"],
                    hashes=["hash-evidence"],
                    assigned={"questions": ["Q-002"], "requirements": ["R-001"], "decisions": [], "evidence": ["E-001"]},
                    evidence=[self.applicability("Q-002")],
                    group="1",
                    path="positions/q2.yaml",
                ),
                launch="READY_TO_DISPATCH",
            ),
            "SUBJECT\0A1": self.registered(
                self.assignment(
                    "ASG-SUBJECT",
                    "RES-SUBJECT",
                    "P-02",
                    "REVIEW",
                    dependencies=["RES-D"],
                    hashes=["hash-d"],
                    assigned={"questions": [], "requirements": [], "decisions": ["D-001"], "evidence": []},
                    group="2",
                    path="reviews/subject.yaml",
                    extra={"review_kind": "SUBJECT", "review_target_id": "D-001", "review_check_id": "CHECK-SUBJECT"},
                ),
                launch="READY_TO_DISPATCH",
            ),
            "SYSTEM\0A1": self.registered(
                self.assignment(
                    "ASG-SYSTEM",
                    "RES-SYSTEM",
                    "P-03",
                    "REVIEW",
                    dependencies=["RES-D"],
                    hashes=["hash-d"],
                    assigned={"questions": [], "requirements": [], "decisions": ["D-001"], "evidence": []},
                    group="3",
                    path="reviews/system.yaml",
                    extra={"review_kind": "SYSTEM", "review_target_id": "D-001", "review_check_id": "CHECK-SYSTEM"},
                ),
                launch="READY_TO_DISPATCH",
            ),
        }

    @staticmethod
    def result(content_hash: str, artifact_type: str, assignment_id: str) -> dict[str, str]:
        return {
            "content_hash": content_hash,
            "artifact_type": artifact_type,
            "assignment_id": assignment_id,
            "input_snapshot": SNAPSHOT,
        }

    def applicability(self, consumer_id: str) -> dict[str, Any]:
        return {**self.evidence_key, "consumer_ids": [consumer_id]}

    @staticmethod
    def assignment(
        assignment_id: str,
        result_id: str,
        role_id: str,
        artifact_type: str,
        *,
        dependencies: list[str] | None = None,
        hashes: list[str] | None = None,
        assigned: dict[str, list[str]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        group: str = "1",
        path: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "assignment_id": assignment_id,
            "attempt_id": "ATT-1",
            "result_id": result_id,
            "role_id": role_id,
            "artifact_type": artifact_type,
            "input_snapshot": SNAPSHOT,
            "selection_authority": "ROOT",
            "group_id": group,
            "dependency_result_ids": dependencies or [],
            "dependency_hashes": hashes or [],
            "assigned_ids": assigned or {"questions": [], "requirements": [], "decisions": [], "evidence": []},
            "sole_writer_paths": [path or f"outputs/{assignment_id}.yaml"],
            "resource_keys": [f"resource:{assignment_id}"],
            "evidence_applicability": evidence or [],
        }
        value.update(extra or {})
        return value

    @staticmethod
    def registered(assignment: dict[str, Any], status: str = "ASSIGNED", launch: str = "NOT_LAUNCHED") -> dict[str, Any]:
        return {"assignment": assignment, "status": status, "launch_state": launch}

    def validate(
        self,
        changed: list[str] | None = None,
        completed_unknown: dict[str, str] | None = None,
        selected_question_claims: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return validate_orchestration(
            self.bank,
            self.approved,
            self.assignments,
            self.results,
            self.docs,
            changed or [],
            completed_unknown,
            selected_question_claims,
        )


class OrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = CompleteOrchestration()

    @staticmethod
    def state(result: dict[str, Any], assignment_id: str) -> dict[str, Any]:
        return next(item for item in result["task_states"] if item["assignment_id"] == assignment_id)

    def test_positive_exact_routes_dag_groups_and_reuse(self) -> None:
        result = self.model.validate()
        self.assertEqual(result["routing_status"], "VALIDATED")
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["ready_groups"],
            [
                {"group_id": "1", "assignment_ids": ["ASG-Q2"]},
                {"group_id": "2", "assignment_ids": ["ASG-SUBJECT"]},
                {"group_id": "3", "assignment_ids": ["ASG-SYSTEM"]},
            ],
        )
        self.assertEqual([item["assignment_id"] for item in result["proposals"]], ["ASG-Q2", "ASG-SUBJECT", "ASG-SYSTEM"])
        self.assertEqual(result["reuse"][0]["consumer_ids"], ["Q-001", "Q-002"])
        self.assertTrue(result["reuse"][0]["reused"])

    def test_missing_approved_map_is_deterministic_empty_compatibility(self) -> None:
        first = validate_orchestration(self.model.bank, None, {}, {}, {}, ["not-inspected"])
        second = validate_orchestration(self.model.bank, None, self.model.assignments, self.model.results, self.model.docs, [])
        self.assertEqual(first, second)
        self.assertEqual(first["routing_status"], "NOT_CONFIGURED")
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["task_states"], [])

    def make_q2_retry(self, *, old_status: str = "STALE", old_launch: str = "NOT_LAUNCHED") -> None:
        registry = self.model.assignments
        current = registry["Q2\0A1"]
        old = copy.deepcopy(current)
        old["assignment"]["attempt_id"] = "ATT-Z-OLD"
        old["assignment"]["result_id"] = "RES-Q2-OLD"
        old["assignment"].pop("supersedes_attempt_id", None)
        old["status"] = old_status
        old["launch_state"] = old_launch
        current["assignment"]["supersedes_attempt_id"] = "ATT-Z-OLD"
        registry["Q2\0ATT-Z-OLD"] = old
        self.model.results["RES-Q2-OLD"] = CompleteOrchestration.result("hash-q2-old", "PRIMARY", "ASG-Q2")
        self.model.assignments = {
            "registered": registry,
            "current_attempts": {"ASG-Q2": "ATT-1"},
        }

    def test_terminal_retry_history_selects_explicit_current_without_id_guessing(self) -> None:
        self.make_q2_retry()
        result = self.model.validate()
        self.assertEqual(result["routing_status"], "VALIDATED")
        self.assertEqual(result["errors"], [])
        current = self.state(result, "ASG-Q2")
        self.assertEqual(current["attempt_id"], "ATT-1")
        self.assertEqual(current["result_id"], "RES-Q2")
        self.assertEqual(current["state"], "READY")
        self.assertNotIn("RES-Q2-OLD", {item["result_id"] for item in result["task_states"]})

    def test_multiple_attempts_require_explicit_current_selection(self) -> None:
        self.make_q2_retry()
        self.model.assignments["current_attempts"] = {}
        result = self.model.validate()
        self.assertEqual(result["routing_status"], "INVALID")
        self.assertIn("assignment:ASG-Q2:multiple_attempts_require_explicit_current", result["errors"])

    def test_nonterminal_historical_attempt_blocks_retry(self) -> None:
        self.make_q2_retry(old_status="RUNNING", old_launch="RUNNING")
        result = self.model.validate()
        self.assertIn(
            "assignment:ASG-Q2:attempt:ATT-Z-OLD:historical_status:RUNNING:not_terminal",
            result["errors"],
        )
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")

    def test_unresolved_dispatch_unknown_history_cannot_be_reset_or_replayed(self) -> None:
        self.make_q2_retry(old_status="STALE", old_launch="DISPATCH_UNKNOWN")
        result = self.model.validate()
        self.assertIn(
            "assignment:ASG-Q2:attempt:ATT-Z-OLD:dispatch_unknown_unresolved",
            result["errors"],
        )
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")

    def test_verified_completed_unknown_history_keeps_marker_and_allows_current_attempt(self) -> None:
        self.make_q2_retry(old_status="ACCEPTED", old_launch="DISPATCH_UNKNOWN")
        result = self.model.validate(completed_unknown={"ASG-Q2\0ATT-Z-OLD": "RES-Q2-OLD"})
        self.assertEqual(result["routing_status"], "VALIDATED")
        self.assertNotIn("dispatch_unknown_unresolved", "|".join(result["errors"]))
        self.assertEqual(
            self.model.assignments["registered"]["Q2\0ATT-Z-OLD"]["launch_state"],
            "DISPATCH_UNKNOWN",
        )
        forged = self.model.validate(completed_unknown={"ASG-Q2\0ATT-Z-OLD": "RES-FORGED"})
        self.assertIn("verified_completed_unknown:binding_mismatch", forged["errors"])
        self.assertIn(
            "assignment:ASG-Q2:attempt:ATT-Z-OLD:dispatch_unknown_unresolved",
            forged["errors"],
        )

    def test_unresolved_dependency_is_addressed_and_blocks_only_consumer(self) -> None:
        assignment = self.model.assignments["Q2\0A1"]["assignment"]
        assignment["dependency_result_ids"] = ["RES-MISSING"]
        assignment["dependency_hashes"] = []
        result = self.model.validate()
        self.assertIn("dependency:ASG-Q2:RES-MISSING:unknown_endpoint", result["errors"])
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "READY")

    def test_cycle_is_rejected(self) -> None:
        q2 = self.model.assignments["Q2\0A1"]["assignment"]
        subject = self.model.assignments["SUBJECT\0A1"]["assignment"]
        q2["dependency_result_ids"], q2["dependency_hashes"] = ["RES-SUBJECT"], []
        subject["dependency_result_ids"], subject["dependency_hashes"] = ["RES-Q2"], []
        result = self.model.validate()
        self.assertTrue(any(error.startswith("dependency_graph:cycle:") for error in result["errors"]))
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "INVALID")

    def test_missing_competence_blocks_exact_routes(self) -> None:
        self.model.approved["role_competence"]["P-02"] = []
        result = self.model.validate()
        self.assertIn("question:Q-002:role:P-02:missing_competence:analysis", result["errors"])
        self.assertIn("critical_review:D-001:subject:role:P-02:missing_competence:domain", result["errors"])
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")

    def test_critical_author_cannot_be_reviewer(self) -> None:
        subject_contract = self.model.approved["critical_reviews"]["D-001"]["subject"]
        subject_contract["role_id"] = "P-01"
        self.model.assignments["SUBJECT\0A1"]["assignment"]["role_id"] = "P-01"
        result = self.model.validate()
        self.assertIn("critical_review:D-001:subject:author_is_reviewer:P-01", result["errors"])
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "INVALID")

    def test_critical_reviews_require_distinct_check_ids(self) -> None:
        self.model.approved["critical_reviews"]["D-001"]["system"]["check_id"] = "CHECK-SUBJECT"
        self.model.assignments["SYSTEM\0A1"]["assignment"]["review_check_id"] = "CHECK-SUBJECT"
        result = self.model.validate()
        self.assertIn("critical_review:D-001:check_ids_not_distinct", result["errors"])

    def test_duplicate_primary_is_rejected(self) -> None:
        duplicate = copy.deepcopy(self.model.assignments["Q1\0A1"]["assignment"])
        duplicate.update({"assignment_id": "ASG-Q1-DUP", "result_id": "RES-Q1-DUP"})
        self.model.assignments["Q1-DUP\0A1"] = CompleteOrchestration.registered(duplicate)
        result = self.model.validate()
        self.assertIn("question:Q-001:primary_assignment_count:2", result["errors"])
        self.assertEqual(self.state(result, "ASG-Q1-DUP")["state"], "INVALID")

    def test_exact_selected_current_answer_owns_question_without_erasing_history(self) -> None:
        answer = copy.deepcopy(self.model.assignments["Q1\0A1"]["assignment"])
        answer.update({"assignment_id": "ASG-Q1-ANSWER", "attempt_id": "ATT-2", "result_id": "RES-Q1-ANSWER", "artifact_type": "ANSWER"})
        self.model.assignments["Q1-ANSWER\0A2"] = CompleteOrchestration.registered(answer, "ACCEPTED")
        self.model.results["RES-Q1-ANSWER"] = {
            **CompleteOrchestration.result("hash-q1-answer", "ANSWER", "ASG-Q1-ANSWER"),
            "attempt_id": "ATT-2",
        }
        self.model.approved["question_routes"]["Q-001"]["primary_assignment_id"] = "ASG-Q1-ANSWER"
        selected = {"Q-001": {"assignment_id": "ASG-Q1-ANSWER", "attempt_id": "ATT-2", "result_id": "RES-Q1-ANSWER"}}
        result = self.model.validate(selected_question_claims=selected)
        self.assertEqual(result["routing_status"], "VALIDATED")
        self.assertNotIn("question:Q-001:primary_assignment_count:2", result["errors"])
        self.assertIn("Q1\0A1", self.model.assignments)
        without_selection = self.model.validate()
        self.assertIn("question:Q-001:primary_assignment_count:2", without_selection["errors"])
        forged = copy.deepcopy(selected); forged["Q-001"]["attempt_id"] = "ATT-FORGED"
        invalid = self.model.validate(selected_question_claims=forged)
        self.assertIn("selected_question_claim:Q-001:binding_mismatch", invalid["errors"])

    def test_dispatch_unknown_is_never_ready_or_replayed(self) -> None:
        self.model.assignments["Q2\0A1"]["launch_state"] = "DISPATCH_UNKNOWN"
        result = self.model.validate()
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "DISPATCH_UNKNOWN")
        proposed = {item["assignment_id"] for item in result["proposals"]}
        ready = {item for group in result["ready_groups"] for item in group["assignment_ids"]}
        self.assertNotIn("ASG-Q2", proposed)
        self.assertNotIn("ASG-Q2", ready)

    def test_launch_lifecycle_states_remain_distinct(self) -> None:
        self.model.assignments["Q2\0A1"]["launch_state"] = "NOT_LAUNCHED"
        self.model.assignments["SUBJECT\0A1"].update({"launch_state": "RUNNING", "status": "RUNNING"})
        self.model.assignments["SYSTEM\0A1"]["launch_state"] = "DISPATCH_UNKNOWN"
        result = self.model.validate()
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "NOT_LAUNCHED")
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "RUNNING")
        self.assertEqual(self.state(result, "ASG-SYSTEM")["state"], "DISPATCH_UNKNOWN")
        self.assertIn("ASG-Q2", {item["assignment_id"] for item in result["proposals"]})
        self.assertEqual(result["ready_groups"], [])

    def test_prefix_write_conflict_is_serialized_across_groups(self) -> None:
        self.model.assignments["SUBJECT\0A1"]["assignment"]["sole_writer_paths"] = ["reviews/shared"]
        self.model.assignments["SYSTEM\0A1"]["assignment"]["sole_writer_paths"] = ["reviews/shared/system.yaml"]
        result = self.model.validate()
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.state(result, "ASG-SYSTEM")["state"], "WAITING")
        system = next(item for item in result["proposals"] if item["assignment_id"] == "ASG-SYSTEM")
        self.assertEqual(system["serialization_after"], ["ASG-SUBJECT"])
        ready = {item for group in result["ready_groups"] for item in group["assignment_ids"]}
        self.assertNotIn("ASG-SYSTEM", ready)
        self.assertLessEqual(len(result["ready_groups"]), 3)

    def test_exact_resource_conflict_and_fourth_group_are_addressable(self) -> None:
        self.model.assignments["SUBJECT\0A1"]["assignment"]["resource_keys"] = ["GPU-1"]
        self.model.assignments["SYSTEM\0A1"]["assignment"]["resource_keys"] = ["GPU-1"]
        result = self.model.validate()
        self.assertEqual(self.state(result, "ASG-SYSTEM")["state"], "WAITING")
        self.assertIn("serialized_conflict:ASG-SUBJECT", self.state(result, "ASG-SYSTEM")["blockers"])
        self.model.assignments["Q2\0A1"]["assignment"]["group_id"] = "4"
        invalid = self.model.validate()
        self.assertIn("assignment:ASG-Q2:group_id:4:outside_1_2_3", invalid["errors"])

    def test_exact_cache_drift_invalidates_only_assignment_using_drifted_key(self) -> None:
        q2_contract = self.model.assignments["Q2\0A1"]["assignment"]["evidence_applicability"][0]
        q2_contract["tool_version"] = "2.0"
        result = self.model.validate()
        self.assertIn("assignment:ASG-Q2:evidence:E-001:exact_key_mismatch:tool_version", result["errors"])
        self.assertEqual(self.state(result, "ASG-Q2")["state"], "INVALID")
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "READY")
        self.assertEqual(self.state(result, "ASG-BRANCH")["state"], "ACCEPTED")
        self.assertEqual(result["reuse"][0]["consumer_ids"], ["Q-001"])
        self.assertFalse(result["reuse"][0]["reused"])

    def test_global_map_revision_does_not_invalidate_independent_exact_inputs(self) -> None:
        baseline = self.model.validate()
        self.model.approved.update({"version": "2", "supersedes_sha256": "previous-map-hash", "revision_note": "routing metadata only"})
        revised = self.model.validate()
        self.assertEqual(revised["errors"], [])
        self.assertEqual(self.state(revised, "ASG-Q1")["state"], "ACCEPTED")
        self.assertEqual(self.state(revised, "ASG-BRANCH")["state"], "ACCEPTED")
        self.assertEqual(revised["stale_ids"], baseline["stale_ids"])
        self.assertEqual(revised["reuse"], baseline["reuse"])

    def test_unknown_freshness_invalidates_accepted_path_not_unrelated_branch(self) -> None:
        self.model.docs["RES-E"]["freshness"] = "UNKNOWN"
        for key in ("Q1\0A1", "Q2\0A1"):
            self.model.assignments[key]["assignment"]["evidence_applicability"][0]["freshness"] = "UNKNOWN"
        result = self.model.validate()
        self.assertIn("evidence:E-001:freshness:UNKNOWN:not_current", result["errors"])
        self.assertEqual(self.state(result, "ASG-Q1")["state"], "STALE")
        self.assertEqual(self.state(result, "ASG-D")["state"], "STALE")
        self.assertEqual(self.state(result, "ASG-BRANCH")["state"], "ACCEPTED")

    def test_changed_evidence_propagates_transitively_and_retains_other_branch(self) -> None:
        result = self.model.validate(["E-001"])
        self.assertEqual(result["errors"], [])
        for result_id in ("RES-E", "RES-Q1", "RES-Q2", "RES-D", "RES-SUBJECT", "RES-SYSTEM"):
            self.assertIn(result_id, result["stale_ids"])
        self.assertEqual(self.state(result, "ASG-Q1")["state"], "STALE")
        self.assertEqual(self.state(result, "ASG-SUBJECT")["state"], "STALE")
        self.assertEqual(self.state(result, "ASG-BRANCH")["state"], "ACCEPTED")
        self.assertNotIn("RES-BRANCH", result["stale_ids"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
