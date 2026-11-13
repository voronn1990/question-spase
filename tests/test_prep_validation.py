from __future__ import annotations

import base64
import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from prep_validation import (  # noqa: E402
    ADMISSION_STATES,
    PERSPECTIVE_ROLES,
    PRESERVED_COUNTS,
    SOURCE_CHECK_IDS,
    canonical_yaml_bytes,
    resolve_admission_state,
    validate_prep,
)
import qs  # noqa: E402


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def canonical_hash(value: Any, omit: tuple[str, ...] = ()) -> str:
    if isinstance(value, dict):
        value = {key: item for key, item in value.items() if key not in set(omit)}
    return hashlib.sha256(canonical_yaml_bytes(value)).hexdigest()


def blob(path: str, document: dict[str, Any], *, policy: str | None = None) -> dict[str, Any]:
    raw = canonical_yaml_bytes(document)
    result: dict[str, Any] = {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_b64": base64.b64encode(raw).decode(),
    }
    if policy is not None:
        result["install_policy"] = policy
    return result


class CompletePrep:
    def __init__(self, mode: str = "GOAL_TO_PLAN", *, question_count: int | None = None) -> None:
        self.mode = mode
        self.run_id = "RUN-GOAL" if mode == "GOAL_TO_PLAN" else "RUN-BANK"
        goal_bytes = b"bounded goal v2\n"
        self.goal_hash = hashlib.sha256(goal_bytes).hexdigest()
        self.registry: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, dict[str, Any]] = {}
        self.inputs: dict[str, Any] = {}
        self.snapshot: dict[str, Any] = {
            "schema_version": "prep-snapshot-v1",
            "id": "SNAPSHOT-1",
            "run_id": self.run_id,
            "input_mode": mode,
            "goal_path": "input/goal.txt",
            "goal_sha256": self.goal_hash,
            "materials": [],
            "bank_binding": "DEFERRED_TO_PREP_ADMISSION" if mode == "GOAL_TO_PLAN" else "BOUND_AT_INIT",
            "path": "input/snapshot.yaml",
            "test_fixture": True,
        }
        snapshot_bytes = canonical_yaml_bytes(self.snapshot)
        self.snapshot_hash = hashlib.sha256(snapshot_bytes).hexdigest()
        evidence_bytes = b"accepted evidence\n"
        self.inputs.update(
            {
                "input/goal.txt": {
                    "sha256": self.goal_hash,
                    "bytes_b64": base64.b64encode(goal_bytes).decode(),
                },
                "input/snapshot.yaml": {
                    "sha256": self.snapshot_hash,
                    "bytes_b64": base64.b64encode(snapshot_bytes).decode(),
                },
                "input/evidence/source.txt": {
                    "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                    "bytes_b64": base64.b64encode(evidence_bytes).decode(),
                },
            }
        )
        self.perspective_map = self.make_perspective_map()
        self.perspective_map["path"] = "input/perspective-map.yaml"

        if mode == "GOAL_TO_PLAN":
            self.vector_result_id = "RES-VECTOR"
            vector_doc = self.make_vector(self.vector_result_id, self.run_id, self.snapshot_hash, revision="7")
            self.vector_hash = hashlib.sha256(canonical_yaml_bytes(vector_doc)).hexdigest()
            self.approval = self.make_approval(self.run_id, self.snapshot_hash, self.vector_result_id, self.vector_hash)
            self.approval["path"] = "input/vector-approval.yaml"
            approval_bytes = canonical_yaml_bytes(self.approval)
            self.approval_hash = hashlib.sha256(approval_bytes).hexdigest()
            statement_bytes = b"approval statement"
            self.inputs["input/vector-approval.yaml"] = {
                "sha256": self.approval_hash,
                "bytes_b64": base64.b64encode(approval_bytes).decode(),
            }
            self.inputs["input/approval-statement.txt"] = {
                "sha256": self.approval["statement_sha256"],
                "bytes_b64": base64.b64encode(statement_bytes).decode(),
            }
            self.registry[self.vector_result_id] = {
                "status": "ACCEPTED",
                "artifact_type": "OPERATOR",
                "content_hash": self.vector_hash,
            }
            self.docs[self.vector_result_id] = vector_doc
            self.initial_bindings, initial_ids = self.make_framings()
            target_count = question_count if question_count is not None else 100
            self.bank = self.make_goal_bank(initial_ids, target_count)
        else:
            self.vector_result_id = "FOREIGN-VECTOR"
            foreign_vector = self.make_vector(
                self.vector_result_id, "SOURCE-RUN", digest("source-snapshot"), revision="7"
            )
            vector_bytes = canonical_yaml_bytes(foreign_vector)
            self.vector_hash = hashlib.sha256(vector_bytes).hexdigest()
            self.approval = self.make_approval("SOURCE-RUN", digest("source-snapshot"), self.vector_result_id, self.vector_hash)
            self.approval["approval_id"] = "FOREIGN-APPROVAL"
            self.approval["statement_path"] = "source/approval-statement.txt"
            approval_bytes = canonical_yaml_bytes(self.approval)
            self.foreign_approval_hash = hashlib.sha256(approval_bytes).hexdigest()
            statement_hash = self.approval["statement_sha256"]
            vector_path = "input/imports/IMP-1/vector.yaml"
            approval_path = "input/imports/IMP-1/approval.yaml"
            statement_path = "input/imports/IMP-1/approval-statement.txt"
            self.inputs[vector_path] = {
                "sha256": self.vector_hash,
                "bytes_b64": base64.b64encode(vector_bytes).decode(),
                "document": foreign_vector,
            }
            self.inputs[approval_path] = {
                "sha256": self.foreign_approval_hash,
                "bytes_b64": base64.b64encode(approval_bytes).decode(),
                "document": copy.deepcopy(self.approval),
            }
            statement_bytes = b"approval statement"
            self.inputs[statement_path] = {
                "sha256": statement_hash,
                "bytes_b64": base64.b64encode(statement_bytes).decode(),
            }
            binding = {
                "schema_version": "import-v1",
                "import_id": "IMP-1",
                "current_run_id": self.run_id,
                "source_run_id": "SOURCE-RUN",
                "source_input_snapshot": digest("source-snapshot"),
                "source_vector_result_id": self.vector_result_id,
                "source_vector_sha256": self.vector_hash,
                "source_vector_revision": "7",
                "source_vector_author_role": "OP-VECTOR",
                "source_vector_agent_id": "AGENT-VECTOR",
                "vector_path": vector_path,
                "vector_sha256": self.vector_hash,
                "source_approval_id": self.approval["approval_id"],
                "source_approval_decision": "APPROVE",
                "source_approval_mode": self.approval["approval_mode"],
                "source_approver_id": self.approval["approver_id"],
                "source_authority_basis": self.approval["authority_basis"],
                "source_observation_provenance": self.approval["observation_provenance"],
                "approval_path": approval_path,
                "approval_sha256": self.foreign_approval_hash,
                "statement_path": statement_path,
                "statement_sha256": statement_hash,
            }
            binding["import_payload_sha256"] = canonical_hash(binding)
            self.snapshot["import_binding"] = binding
            self.approval_hash = self.foreign_approval_hash
            self.initial_bindings = []
            target_count = question_count if question_count is not None else 2
            self.bank = self.make_bank_mode_bank(target_count)

        self.perspective_map.update(
            {
                "vector_result_id": self.vector_result_id,
                "vector_sha256": self.vector_hash,
                "approval_sha256": self.approval_hash,
            }
        )
        perspective_bytes = canonical_yaml_bytes(self.perspective_map)
        self.map_hash = hashlib.sha256(perspective_bytes).hexdigest()
        self.inputs["input/perspective-map.yaml"] = {
            "sha256": self.map_hash,
            "bytes_b64": base64.b64encode(perspective_bytes).decode(),
        }

        bank_raw = canonical_yaml_bytes(self.bank)
        self.bank_hash = hashlib.sha256(bank_raw).hexdigest()
        self.inputs["input/bank.yaml"] = {
            "sha256": self.bank_hash,
            "bytes_b64": base64.b64encode(bank_raw).decode(),
        }
        self.requirements_hash = canonical_hash(self.bank["requirements"])
        self.registry["RES-EVIDENCE"] = {
            "status": "ACCEPTED",
            "artifact_type": "EVIDENCE",
            "content_hash": digest("evidence-result"),
        }
        self.docs["RES-EVIDENCE"] = {"id": "RES-EVIDENCE", "artifact_type": "EVIDENCE"}
        self.admission = self.make_admission()
        admission_blob = blob("input/prep-admission.yaml", self.admission, policy="CREATE_IF_ABSENT")
        self.admission_hash = admission_blob["sha256"]
        self.inputs["input/prep-admission.yaml"] = {
            "sha256": self.admission_hash,
            "bytes_b64": admission_blob["bytes_b64"],
        }
        expected_doc = {"schema_version": "current-v1", "run_id": self.run_id, "revision": "0"}
        self.next_current = {
            "schema_version": "current-v1",
            "run_id": self.run_id,
            "input_snapshot": self.snapshot_hash,
            "input_mode": self.mode,
            "admission_transaction_id": "TX-ADMISSION-1",
            "bank_sha256": self.bank_hash,
            "prep_admission_sha256": self.admission_hash,
            "revision": "1",
            "status": "CORE_ADMITTED",
        }
        expected_blob = blob("state/current.yaml", expected_doc)
        next_blob = blob("state/current.yaml", self.next_current)
        self.current = {**self.next_current, "sha256": next_blob["sha256"]}
        tx = {
            "schema_version": "prep-admission-transaction-v1",
            "transaction_type": "PREP_ADMISSION",
            "transaction_id": "TX-ADMISSION-1",
            "admission_id": self.admission["id"],
            "run_id": self.run_id,
            "input_snapshot": self.snapshot_hash,
            "input_mode": self.mode,
            "status": "COMMITTED",
            "bank": blob(
                "input/bank.yaml",
                self.bank,
                policy="CREATE_IF_ABSENT" if mode == "GOAL_TO_PLAN" else "VERIFY_EXISTING",
            ),
            "admission": admission_blob,
            "expected_current": expected_blob,
            "admission_next_current": next_blob,
        }
        if mode == "GOAL_TO_PLAN":
            tx.update(
                {
                    "current_vector_result_id": self.vector_result_id,
                    "current_vector_sha256": self.vector_hash,
                    "current_approval_sha256": self.approval_hash,
                }
            )
        else:
            self.make_applicability()
            tx.update(
                {
                    "import_payload_sha256": self.snapshot["import_binding"]["import_payload_sha256"],
                    "applicability_result_id": "RES-APPLICABILITY",
                    "applicability_sha256": self.registry["RES-APPLICABILITY"]["content_hash"],
                }
            )
        tx["transaction_payload_sha256"] = canonical_hash(tx)
        self.transactions = {"TX-ADMISSION-1": tx}

    @staticmethod
    def make_vector(result_id: str, run_id: str, snapshot_hash: str, *, revision: str) -> dict[str, Any]:
        return {
            "id": result_id,
            "artifact_type": "OPERATOR",
            "role_id": "OP-VECTOR",
            "author_role": "OP-VECTOR",
            "run_id": run_id,
            "input_snapshot": snapshot_hash,
            "revision": revision,
            "G": ["G-1"],
            "NOT_G": ["NOT-G-1"],
            "value_recipient": "Operator",
            "scope": "Bounded event fold",
            "success_criteria": ["Deterministic output"],
            "requirements": ["R-001"],
            "hard_constraints": ["Bounded input"],
            "changeable_assumptions": ["Storage mechanism"],
            "current_state": "Goal supplied",
            "resources": ["Python standard library"],
            "authority": "Root",
            "owner_decisions": [
                {
                    "id": "OD-1",
                    "decision_needed": "Approve goal vector",
                    "why_owner_only": "Defines intended outcome",
                    "known_context": "Exact goal bytes",
                    "answer_effect": "Allows preparation",
                }
            ],
            "open_research_questions": ["Which mechanism best satisfies bounded memory?"],
        }

    @staticmethod
    def make_approval(run_id: str, snapshot_hash: str, result_id: str, vector_hash: str) -> dict[str, Any]:
        return {
            "schema_version": "approval-v1",
            "approval_id": "APPROVAL-1",
            "run_id": run_id,
            "input_snapshot": snapshot_hash,
            "vector_result_id": result_id,
            "vector_sha256": vector_hash,
            "vector_revision": "7",
            "decision": "APPROVE",
            "approval_mode": "SIMULATED_TEST",
            "approver_id": "ROOT-AS-SIMULATED_TEST_OWNER",
            "authority_basis": "Exact bounded test fixture authority",
            "observation_provenance": "TESTS/goal05/preregistration-amendment-01.json:1",
            "statement_path": "input/approval-statement.txt",
            "statement_sha256": digest("approval statement"),
            "created_at": "2026-10-21T00:00:00Z",
            "fixture_only": "YES",
        }

    def make_perspective_map(self) -> dict[str, Any]:
        rows = []
        for number, role in enumerate(PERSPECTIVE_ROLES, 1):
            rows.append(
                {
                    "role_id": role,
                    "agent_id": f"AGENT-{number:02d}",
                    "title": f"Perspective {number}",
                    "competence": f"Competence area {number}",
                    "analysis_scope": f"Scope boundary {number}",
                    "unique_contribution": f"Distinct contribution {number}",
                    "boundary": f"Does not decide area {number + 1}",
                    "plausible_blindspots": f"Blindspot class {number}",
                }
            )
        return {
            "schema_version": "perspective-map-v1",
            "id": "PERSPECTIVE-MAP-1",
            "run_id": self.run_id,
            "input_snapshot": self.snapshot_hash,
            "vector_result_id": None,
            "vector_sha256": None,
            "approval_sha256": None,
            "selection_authority": "ROOT",
            "perspectives": rows,
        }

    def make_framings(self) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        bindings: list[dict[str, Any]] = []
        initial: list[tuple[str, str]] = []
        for row in self.perspective_map["perspectives"]:
            role = row["role_id"]
            result_id = f"RES-FRAMING-{role}"
            result_hash = digest(result_id)
            questions = []
            for number in range(1, 11):
                qid = f"IQ-{role}-{number:02d}"
                initial.append((qid, result_id))
                questions.append(
                    {
                        "id": qid,
                        "unknown": f"Unknown {qid}",
                        "why_material": f"Changes decision {qid}",
                        "changeable_decision": f"Decision {qid}",
                        "applicability": "Applies to bounded goal",
                        "evidence_needed": f"Evidence for {qid}",
                        "g_refs": ["G-1"],
                        "r_refs": ["R-001"],
                        "not_g_refs": [],
                        "origin_role": role,
                    }
                )
            self.registry[result_id] = {
                "status": "ACCEPTED",
                "artifact_type": "FRAMING",
                "content_hash": result_hash,
                "role_id": role,
            }
            self.docs[result_id] = {
                "id": result_id,
                "artifact_type": "FRAMING",
                "author_role": role,
                "agent_id": row["agent_id"],
                "input_snapshot": self.snapshot_hash,
                "perspective_profile_hash": canonical_hash(row),
                "vector_result_id": self.vector_result_id,
                "vector_sha256": self.vector_hash,
                "approval_sha256": self.approval_hash,
                "context_packet_sha256": digest(f"context:{role}"),
                "blind_input": {
                    "contains_bank": "NO",
                    "contains_foreign_p_results": "NO",
                    "contains_preferred_solution": "NO",
                    "allowed": ["approved Vector", "exact facts/materials", "own perspective row", "own instructions"],
                },
                "initial_questions": questions,
            }
            bindings.append(
                {"result_id": result_id, "sha256": result_hash, "role_id": role, "agent_id": row["agent_id"]}
            )
        return bindings, initial

    @staticmethod
    def bank_question(qid: str, source_qid: str, source_result: str, role: str) -> dict[str, Any]:
        return {
            "id": qid,
            "text": f"Investigate {qid}",
            "source_offset": qid,
            "unknown_key": f"unknown:{qid}",
            "mechanism": "Mechanism remains researchable",
            "applicability": "Bounded goal",
            "changeable_decision": f"Decision for {qid}",
            "evidence_needed": f"Evidence for {qid}",
            "lineage": {
                "operation": "INITIAL_KEPT",
                "source_question_ids": [source_qid],
                "source_result_ids": [source_result],
                "source_roles": [role],
                "reason": "Retained as a distinct unresolved question",
            },
            "g_refs": ["G-1"],
            "r_refs": ["R-001"],
            "not_g_refs": [],
        }

    def make_goal_bank(self, initial: list[tuple[str, str]], count: int) -> dict[str, Any]:
        questions = []
        for index in range(count):
            source_qid, result_id = initial[index % len(initial)]
            role = result_id.removeprefix("RES-FRAMING-")
            question = self.bank_question(f"Q-{index + 1:03d}", source_qid, result_id, role)
            if index >= len(initial):
                question["lineage"]["operation"] = "DERIVED_UNKNOWN"
            questions.append(question)
        return {"schema_version": "bank-v1", "questions": questions, "requirements": ["R-001"]}

    def make_bank_mode_bank(self, count: int) -> dict[str, Any]:
        questions = []
        for index in range(count):
            question = self.bank_question(
                f"Q-{index + 1:03d}", f"SOURCE-Q-{index + 1:03d}", self.vector_result_id, "OP-VECTOR"
            )
            question["lineage"]["operation"] = "DERIVED_UNKNOWN"
            questions.append(question)
        return {"schema_version": "bank-v1", "questions": questions, "requirements": ["R-001"]}

    def source_checks(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for check_id in SOURCE_CHECK_IDS:
            row: dict[str, Any] = {
                "check_id": check_id,
                "outcome": "PASS",
                "basis": f"Root addressed {check_id} against accepted evidence",
                "evidence_refs": ["RES-EVIDENCE"],
                "targeted_repair_assignments": [],
            }
            if check_id == "PERSPECTIVES_DISTINCT_NECESSARY":
                row.update(
                    {
                        "root_semantic_outcome": "PASS",
                        "perspective_assessments": [
                            {
                                "role_id": item["role_id"],
                                "competence": item["competence"],
                                "analysis_scope": item["analysis_scope"],
                                "unique_contribution": item["unique_contribution"],
                                "boundary": item["boundary"],
                                "necessity": f"Root found {item['role_id']} necessary for its bounded scope",
                                "basis_refs": ["RES-EVIDENCE"],
                            }
                            for item in self.perspective_map["perspectives"]
                        ],
                        "material_overlap_assessments": [],
                    }
                )
            if check_id == "COMMON_UNPROVED_ASSUMPTIONS":
                row.update(
                    {
                        "root_semantic_outcome": "PASS",
                        "detected_assumptions": [],
                        "none_with_basis": {"outcome": "NONE_WITH_BASIS", "basis_refs": ["RES-EVIDENCE"]},
                    }
                )
            rows.append(row)
        return rows

    def make_admission(self) -> dict[str, Any]:
        count = len(self.bank["questions"])
        if count < 200:
            size: dict[str, Any] = {
                "classification": "BELOW_TARGET_WITH_RATIONALE",
                "rationale": "The bounded fixture has complete lineage and is not padded to a qualification target",
            }
        elif count <= 300:
            size = {"classification": "WITHIN_TARGET"}
        else:
            size = {
                "classification": "ABOVE_TARGET_WITH_RESOURCE_PLAN",
                "resource_plan": "Partition critical obligations into three bounded review groups",
            }
        return {
            "schema_version": "prep-admission-v1",
            "id": "ADMISSION-1",
            "run_id": self.run_id,
            "input_snapshot": self.snapshot_hash,
            "input_mode": self.mode,
            "goal_sha256": self.goal_hash,
            "vector_binding": {"result_id": self.vector_result_id, "sha256": self.vector_hash},
            "approval_binding": {"id": self.approval["approval_id"], "sha256": self.approval_hash},
            "perspective_map_binding": {"id": self.perspective_map["id"], "sha256": self.map_hash},
            "initial_set_bindings": copy.deepcopy(self.initial_bindings),
            "expansion_bindings": [],
            "normalization_decisions": [{"id": "N-1", "decision": "Retain addressable lineage"}],
            "bank_path": "input/bank.yaml",
            "bank_sha256": self.bank_hash,
            "requirements_sha256": self.requirements_hash,
            "question_count": str(count),
            "size_assessment": size,
            "source_checks": self.source_checks(),
            "uncovered_areas": [],
            "generation_performed": "YES" if self.mode == "GOAL_TO_PLAN" else "NO",
            "status": "ADMITTED",
        }

    def make_applicability(self) -> None:
        result_id = "RES-APPLICABILITY"
        result_hash = digest("applicability")
        self.registry[result_id] = {
            "status": "ACCEPTED",
            "artifact_type": "OPERATOR",
            "content_hash": result_hash,
        }
        self.docs[result_id] = {
            "id": result_id,
            "artifact_type": "OPERATOR",
            "role_id": "OP-VECTOR",
            "operator_contract": "BANK_APPLICABILITY",
            "current_run_id": self.run_id,
            "current_input_snapshot": self.snapshot_hash,
            "import_payload_sha256": self.snapshot["import_binding"]["import_payload_sha256"],
            "current_goal_sha256": self.goal_hash,
            "current_material_hashes": [],
            "current_bank_sha256": self.bank_hash,
            "current_requirements_sha256": self.requirements_hash,
            "g_comparison": {"outcome": "UNCHANGED", "basis": "Exact goal comparison"},
            "requirement_comparisons": [
                {"source_requirement_id": "R-001", "current_requirement_id": "R-001", "outcome": "UNCHANGED"}
            ],
            "authority_comparison": {"outcome": "UNCHANGED", "basis": "Exact authority comparison"},
            "source_check_outcomes": {check_id: "PASS" for check_id in SOURCE_CHECK_IDS},
            "overall": "UNCHANGED",
        }

    def advance_with_plan_projection(self) -> None:
        result_id = "PLAN-STAGE-1"
        plan_hash = digest("selected-plan-stage-1")
        self.registry[result_id] = {
            "status": "ACCEPTED",
            "artifact_type": "PLAN",
            "content_hash": plan_hash,
            "assignment_id": "ASG-PLAN-1",
            "attempt_id": "ATT-PLAN-1",
            "dependency_result_ids": [],
        }
        self.docs[result_id] = {
            "id": result_id,
            "artifact_type": "PLAN",
            "plan_version": "PLAN-V1",
            "identity_result": {"stage_id": "S-001"},
        }
        projection = {
            "plan_version": "PLAN-V1",
            "stages": [
                {
                    "logical_stage_id": "S-001",
                    "result_id": result_id,
                    "sha256": plan_hash,
                    "assignment_id": "ASG-PLAN-1",
                    "attempt_id": "ATT-PLAN-1",
                }
            ],
        }
        before = {key: value for key, value in self.current.items() if key != "sha256"}
        after = {
            **before,
            "revision": "2",
            "approved_map_sha256": digest("approved-map"),
            "current_plan_projection": projection,
            "current_plan_projection_sha256": hashlib.sha256(canonical_yaml_bytes(projection)).hexdigest(),
        }
        after_bytes = canonical_yaml_bytes(after)
        after_hash = hashlib.sha256(after_bytes).hexdigest()
        record = {
            "schema_version": "current-transition-v1",
            "transaction_type": "CURRENT_TRANSITION",
            "transaction_id": "TX-CURRENT-0002",
            "current_path": "state/current.yaml",
            "run_id": self.run_id,
            "input_snapshot": self.snapshot_hash,
            "admission_transaction_id": "TX-ADMISSION-1",
            "prep_admission_sha256": self.admission_hash,
            "mutation_type": "APPROVED_MAP_SET",
            "from_revision": "1",
            "to_revision": "2",
            "before_current_sha256": self.current["sha256"],
            "after_current_sha256": after_hash,
            "after_current_bytes_b64": base64.b64encode(after_bytes).decode(),
            "backing_records": [
                {"kind": "APPROVED_MAP", "path": "state/approved-map.yaml", "sha256": digest("approved-map")},
                {"kind": "PLAN_RESULT", "path": "transactions/PLAN-STAGE-1.yaml", "sha256": plan_hash},
            ],
            "status": "COMMITTED",
        }
        record["transaction_payload_sha256"] = canonical_hash(record)
        self.transactions["TX-CURRENT-0002"] = {
            "record": record,
            "before_current": before,
            "after_current": after,
        }
        self.current = {**after, "sha256": after_hash}

    def validate(self, phase: str = "core") -> list[str]:
        return validate_prep(
            self.snapshot,
            self.current,
            self.registry,
            self.docs,
            self.inputs,
            self.bank,
            self.approval,
            self.perspective_map,
            self.admission,
            self.transactions,
            phase,
        )


class PrepValidationTest(unittest.TestCase):
    def test_preserved_method_and_lifecycle_counts_are_explicit(self) -> None:
        self.assertEqual(
            PRESERVED_COUNTS,
            {
                "roles": 18,
                "perspectives": 10,
                "operators": 6,
                "planners": 1,
                "final_auditors": 1,
                "methods": 14,
                "core_phases": 6,
                "audit_areas": 8,
                "root_stage": "S-000",
            },
        )

    def test_canonical_yaml_bytes_match_controller(self) -> None:
        value = {
            "plan_version": "PLAN-V1",
            "stages": [
                {
                    "logical_stage_id": "S-001",
                    "result_id": "PLAN-1",
                    "sha256": digest("plan"),
                    "assignment_id": "ASG-1",
                    "attempt_id": "ATT-1",
                }
            ],
        }
        self.assertEqual(canonical_yaml_bytes(value), qs.canon_yaml(value))

    def test_goal_positive(self) -> None:
        fixture = CompletePrep("GOAL_TO_PLAN")
        self.assertEqual(fixture.admission["question_count"], "100")
        self.assertEqual(fixture.validate(), [])

    def test_bank_positive(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        self.assertEqual(fixture.admission["question_count"], "2")
        self.assertEqual(fixture.validate(), [])

    def test_bank_admission_binds_explicit_prep_framing_profile(self) -> None:
        class BankWithCanonicalProfiles(CompletePrep):
            def make_perspective_map(self) -> dict[str, Any]:
                perspective_map = super().make_perspective_map()
                for row in perspective_map["perspectives"]:
                    row["profile_sha256"] = canonical_hash(row)
                return perspective_map

        fixture = BankWithCanonicalProfiles("BANK_TO_PLAN")
        row = fixture.perspective_map["perspectives"][0]
        profile = {key: value for key, value in row.items() if key != "profile_sha256"}

        result_id = "RES-PREP-FRAMING"
        fixture.registry[result_id] = {"artifact_type": "FRAMING", "content_hash": digest(result_id)}
        result = {
            "id": result_id, "artifact_type": "FRAMING", "author_role": row["role_id"],
            "perspective_profile_hash": row["profile_sha256"],
        }
        fixture.docs[result_id] = result
        profile_error = f"prep:result:{result_id}:perspective_profile:mismatch"
        self.assertEqual(fixture.validate(), [])
        result.update({"lifecycle_scope": "PREP", "prep_stage": "PREP-2_PERSPECTIVES"})
        self.assertEqual(fixture.validate(), [profile_error])
        result["perspective_profile"] = {**profile, "boundary": "different boundary"}
        self.assertEqual(fixture.validate(), [profile_error])
        result["perspective_profile"] = profile
        self.assertEqual(fixture.validate(), [])
        result["perspective_profile_hash"] = "0" * 64
        self.assertEqual(fixture.validate(), [f"prep:result:{result_id}:perspective_profile_hash:mismatch"])

    def test_canonical_question_count_roundtrips_through_strict_loader(self) -> None:
        fixture = CompletePrep()
        raw = canonical_yaml_bytes(fixture.admission)
        self.assertIn(b'"question_count": "100"', raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prep-admission.yaml"
            path.write_bytes(raw)
            loaded = qs.load_yaml(path)
        self.assertEqual(loaded, fixture.admission)
        self.assertIsInstance(loaded["question_count"], str)
        fixture.admission = loaded
        self.assertEqual(fixture.validate(), [])

    def test_all_ten_source_check_omissions_are_addressed(self) -> None:
        for check_id in SOURCE_CHECK_IDS:
            with self.subTest(check_id=check_id):
                fixture = CompletePrep()
                fixture.admission["source_checks"] = [
                    row for row in fixture.admission["source_checks"] if row["check_id"] != check_id
                ]
                errors = fixture.validate()
                self.assertIn("prep:admission:source_checks:not_exact_order", errors)

    def test_source_check_critical_gap_cannot_be_admitted(self) -> None:
        fixture = CompletePrep()
        row = fixture.admission["source_checks"][0]
        row["outcome"] = "CRITICAL_GAP"
        row["targeted_repair_assignments"] = ["ASG-REPAIR-1"]
        self.assertIn("prep:admission:status:critical_gap_cannot_admit", fixture.validate())

    def test_perspective_semantic_outcome_must_be_explicit_and_coherent(self) -> None:
        fixture = CompletePrep()
        row = next(item for item in fixture.admission["source_checks"] if item["check_id"] == "PERSPECTIVES_DISTINCT_NECESSARY")
        row["root_semantic_outcome"] = "CRITICAL_GAP"
        self.assertIn(
            "prep:source_check:PERSPECTIVES_DISTINCT_NECESSARY:root_semantic_outcome:incoherent",
            fixture.validate(),
        )

    def test_common_assumption_none_requires_accepted_basis(self) -> None:
        fixture = CompletePrep()
        row = next(item for item in fixture.admission["source_checks"] if item["check_id"] == "COMMON_UNPROVED_ASSUMPTIONS")
        row["none_with_basis"]["basis_refs"] = ["MISSING-EVIDENCE"]
        self.assertTrue(any("none_with_basis:basis_refs:unresolved" in error for error in fixture.validate()))

    def test_foreign_approval_must_approve(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        fixture.approval["decision"] = "REJECT"
        fixture.inputs["input/imports/IMP-1/approval.yaml"]["document"]["decision"] = "REJECT"
        self.assertTrue(any("decision:not_APPROVE" in error for error in fixture.validate()))

    def test_foreign_approval_crosslink_tamper_is_addressed(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        fixture.approval["vector_result_id"] = "OTHER-VECTOR"
        fixture.inputs["input/imports/IMP-1/approval.yaml"]["document"]["vector_result_id"] = "OTHER-VECTOR"
        self.assertIn(
            "prep:foreign_approval:FOREIGN-APPROVAL:vector_result_id:crosslink_mismatch",
            fixture.validate(),
        )

    def test_foreign_approval_requires_observation_provenance(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        fixture.approval["observation_provenance"] = ""
        fixture.inputs["input/imports/IMP-1/approval.yaml"]["document"]["observation_provenance"] = ""
        self.assertIn(
            "prep:foreign_approval:FOREIGN-APPROVAL:observation_provenance:missing",
            fixture.validate(),
        )

    def test_perspective_agents_must_be_distinct(self) -> None:
        fixture = CompletePrep()
        fixture.perspective_map["perspectives"][1]["agent_id"] = fixture.perspective_map["perspectives"][0]["agent_id"]
        self.assertIn("prep:perspective_map:agent_ids:not_distinct", fixture.validate())

    def test_same_identity_must_reach_framing(self) -> None:
        fixture = CompletePrep()
        fixture.docs["RES-FRAMING-P-01"]["agent_id"] = "OTHER-AGENT"
        self.assertIn("prep:initial_set:P-01:agent_id:mismatch", fixture.validate())

    def test_initial_framing_accepts_canonical_author_role_without_role_id_alias(self) -> None:
        fixture = CompletePrep()
        document = fixture.docs["RES-FRAMING-P-08"]
        self.assertEqual(document["author_role"], "P-08")
        self.assertNotIn("role_id", document)
        self.assertEqual(fixture.validate(), [])

    def test_initial_framing_rejects_role_id_alias_contradiction(self) -> None:
        fixture = CompletePrep()
        fixture.docs["RES-FRAMING-P-01"]["role_id"] = "P-02"
        self.assertIn(
            "prep:initial_set:P-01:role_id:contradicts_author_role",
            fixture.validate(),
        )

    def test_initial_framing_rejects_wrong_canonical_author_role(self) -> None:
        fixture = CompletePrep()
        fixture.docs["RES-FRAMING-P-01"]["author_role"] = "P-02"
        errors = fixture.validate()
        self.assertIn("prep:initial_set:P-01:author_role:mismatch", errors)
        self.assertIn(
            "prep:initial_set:P-01:registry_role_id:contradicts_author_role",
            errors,
        )

    def test_initial_framing_rejects_missing_canonical_author_role(self) -> None:
        fixture = CompletePrep()
        del fixture.docs["RES-FRAMING-P-01"]["author_role"]
        fixture.docs["RES-FRAMING-P-01"]["role_id"] = "P-01"
        self.assertIn("prep:initial_set:P-01:author_role:missing", fixture.validate())

    def test_same_identity_must_reach_present_core_primary(self) -> None:
        fixture = CompletePrep()
        fixture.registry["PRIMARY-P-01"] = {
            "status": "ACCEPTED",
            "artifact_type": "PRIMARY",
            "content_hash": digest("primary-p01"),
            "role_id": "P-01",
        }
        fixture.docs["PRIMARY-P-01"] = {
            "id": "PRIMARY-P-01",
            "artifact_type": "PRIMARY",
            "author_role": "P-01",
            "agent_id": "REPLACEMENT-AGENT",
        }
        self.assertIn("prep:perspective:P-01:core_identity_mismatch:PRIMARY-P-01", fixture.validate())

    def test_each_perspective_must_supply_ten_initial_questions(self) -> None:
        fixture = CompletePrep()
        fixture.docs["RES-FRAMING-P-01"]["initial_questions"].pop()
        self.assertIn("prep:initial_set:P-01:initial_questions:count_not_10", fixture.validate())

    def test_initial_question_lineage_must_be_complete(self) -> None:
        fixture = CompletePrep()
        fixture.bank["questions"][0]["lineage"]["source_question_ids"] = []
        errors = fixture.validate()
        self.assertTrue(any("prep:bank:initial_questions:uncovered" in error for error in errors))

    def test_bank_question_required_field_is_enforced(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        del fixture.bank["questions"][0]["changeable_decision"]
        self.assertIn("prep:bank_question:Q-001:changeable_decision:missing", fixture.validate())

    def test_keep_distinct_is_not_silently_added_to_lineage_contract(self) -> None:
        fixture = CompletePrep()
        fixture.bank["questions"][0]["lineage"]["operation"] = "KEEP_DISTINCT"
        self.assertIn(
            "prep:bank_question:Q-001:lineage:operation:invalid",
            fixture.validate(),
        )

    def test_every_requirement_must_be_covered(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        for question in fixture.bank["questions"]:
            question["r_refs"] = []
        self.assertIn("prep:bank:requirements:uncovered:R-001", fixture.validate())

    def test_small_complete_bank_requires_concrete_no_padding_rationale(self) -> None:
        fixture = CompletePrep()
        fixture.admission["size_assessment"]["rationale"] = "N/A"
        self.assertIn("prep:admission:size_assessment:below:rationale_missing", fixture.validate())

    def test_question_count_target_boundaries_are_consistent(self) -> None:
        cases = (
            (199, "BELOW_TARGET_WITH_RATIONALE"),
            (200, "WITHIN_TARGET"),
            (300, "WITHIN_TARGET"),
            (301, "ABOVE_TARGET_WITH_RESOURCE_PLAN"),
        )
        for count, expected in cases:
            with self.subTest(count=count):
                fixture = CompletePrep(question_count=count)
                self.assertEqual(fixture.admission["question_count"], str(count))
                self.assertEqual(fixture.admission["size_assessment"]["classification"], expected)
                self.assertEqual(fixture.validate(), [])

    def test_question_count_must_match_bank_length(self) -> None:
        fixture = CompletePrep()
        fixture.admission["question_count"] = "99"
        self.assertIn("prep:admission:question_count:mismatch", fixture.validate())

    def test_question_count_rejects_noncanonical_or_nonstring_values(self) -> None:
        invalid_values: tuple[Any, ...] = (
            100,
            True,
            100.0,
            -1,
            "-1",
            "+1",
            " 1",
            "1 ",
            "01",
            "1e2",
            "1.0",
            "one",
            "",
        )
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                fixture = CompletePrep()
                fixture.admission["question_count"] = value
                errors = fixture.validate()
                self.assertIn("prep:admission:question_count:invalid", errors)
                self.assertNotIn("prep:admission:question_count:mismatch", errors)

    def test_above_300_bank_with_resource_plan_is_valid(self) -> None:
        fixture = CompletePrep(question_count=301)
        self.assertEqual(fixture.validate(), [])

    def test_above_300_bank_without_resource_plan_is_rejected(self) -> None:
        fixture = CompletePrep(question_count=301)
        del fixture.admission["size_assessment"]["resource_plan"]
        self.assertIn("prep:admission:size_assessment:above:resource_plan_missing", fixture.validate())

    def test_missing_new_mode_is_not_silently_legacy(self) -> None:
        fixture = CompletePrep()
        del fixture.snapshot["input_mode"]
        state = resolve_admission_state(
            fixture.snapshot,
            fixture.current,
            fixture.inputs,
            fixture.bank,
            fixture.admission,
            fixture.transactions,
        )
        self.assertEqual(state["state"], "INVALID")
        self.assertIn("prep:admission_state:INVALID:not_positive_legacy_or_new_mode", state["errors"])

    def test_state_vocabulary_is_exactly_five(self) -> None:
        self.assertEqual(
            ADMISSION_STATES,
            ("LEGACY_CORE", "PREP_OPEN", "ADMISSION_REPLAYABLE", "CORE_ADMITTED", "INVALID"),
        )

    def test_positive_exact_legacy_shape_is_legacy_core(self) -> None:
        bank = {
            "questions": [{"id": "Q-001", "text": "Legacy question", "source_offset": "1"}],
            "requirements": ["R-001"],
        }
        bank_raw = canonical_yaml_bytes(bank)
        bank_hash = hashlib.sha256(bank_raw).hexdigest()
        snapshot_hash = digest("legacy-snapshot")
        original_hash = digest("legacy-original")
        snapshot = {
            "schema_version": "question-space-v4",
            "id": "SNAP-LEGACY",
            "run_id": "RUN-LEGACY",
            "bank_path": "input/bank.yaml",
            "bank_sha256": bank_hash,
            "requirements_sha256": hashlib.sha256(canonical_yaml_bytes(["R-001"])).hexdigest(),
            "question_ids": ["Q-001"],
            "original_bank": {"path": "input/original-bank.yaml", "sha256": original_hash},
            "path": "input/snapshot.yaml",
        }
        current = {"run_id": "RUN-LEGACY", "input_snapshot": snapshot_hash}
        inputs = {
            "input/snapshot.yaml": snapshot_hash,
            "input/bank.yaml": bank_hash,
            "input/original-bank.yaml": original_hash,
        }
        state = resolve_admission_state(snapshot, current, inputs, bank, None, {})
        self.assertEqual((state["state"], state["action"]), ("LEGACY_CORE", "NONE"))

    def test_plan_projection_hash_uses_canonical_yaml(self) -> None:
        fixture = CompletePrep()
        fixture.current["current_plan_projection"] = {"plan_version": "P1", "stages": []}
        fixture.current["current_plan_projection_sha256"] = digest("not-the-projection")
        self.assertTrue(
            any("current_plan_projection_sha256:mismatch" in error for error in fixture.validate())
        )

    def test_new_snapshot_without_transaction_is_prep_open(self) -> None:
        fixture = CompletePrep()
        state = resolve_admission_state(fixture.snapshot, {}, fixture.inputs, fixture.bank, None, {})
        self.assertEqual((state["state"], state["action"]), ("PREP_OPEN", "NONE"))

    def test_bank_snapshot_without_transaction_requires_verify_existing(self) -> None:
        fixture = CompletePrep("BANK_TO_PLAN")
        state = resolve_admission_state(fixture.snapshot, {}, fixture.inputs, fixture.bank, None, {})
        self.assertEqual((state["state"], state["action"]), ("PREP_OPEN", "VERIFY_EXISTING"))

    def test_committed_transaction_with_missing_outputs_is_replayable(self) -> None:
        fixture = CompletePrep()
        state = resolve_admission_state(
            fixture.snapshot, None, fixture.inputs, fixture.bank, None, fixture.transactions
        )
        self.assertEqual((state["state"], state["action"]), ("ADMISSION_REPLAYABLE", "REPLAY_ADMISSION"))

    def test_current_stable_binding_drift_is_invalid(self) -> None:
        fixture = CompletePrep()
        fixture.current["bank_sha256"] = digest("other-bank")
        state = resolve_admission_state(
            fixture.snapshot,
            fixture.current,
            fixture.inputs,
            fixture.bank,
            fixture.admission,
            fixture.transactions,
        )
        self.assertEqual(state["state"], "INVALID")
        self.assertTrue(any("bank_sha256:stable_mismatch" in error for error in state["errors"]))

    def test_valid_current_descendant_and_selected_plan_projection(self) -> None:
        fixture = CompletePrep()
        fixture.advance_with_plan_projection()
        self.assertEqual(fixture.validate(), [])

    def test_current_descendant_requires_parsed_exact_images(self) -> None:
        fixture = CompletePrep()
        fixture.advance_with_plan_projection()
        fixture.transactions["TX-CURRENT-0002"] = fixture.transactions["TX-CURRENT-0002"]["record"]
        self.assertTrue(any("parsed_current_images:missing" in error for error in fixture.validate()))

    def test_core_phase_blocks_prep_open(self) -> None:
        fixture = CompletePrep()
        errors = validate_prep(
            fixture.snapshot,
            {},
            fixture.registry,
            fixture.docs,
            fixture.inputs,
            fixture.bank,
            fixture.approval,
            fixture.perspective_map,
            None,
            {},
            "core",
        )
        self.assertIn("prep:admission_state:PREP_OPEN:phase_core_requires_core", errors)

    def test_validator_does_not_guess_semantic_truth_from_labels(self) -> None:
        fixture = CompletePrep()
        for row in fixture.perspective_map["perspectives"]:
            row["title"] = "Same lexical title"
        for result_id, document in fixture.docs.items():
            if document.get("artifact_type") == "FRAMING":
                role = document["author_role"]
                profile = next(item for item in fixture.perspective_map["perspectives"] if item["role_id"] == role)
                document["perspective_profile_hash"] = canonical_hash(profile)
        perspective_check = next(
            item for item in fixture.admission["source_checks"] if item["check_id"] == "PERSPECTIVES_DISTINCT_NECESSARY"
        )
        for assessment in perspective_check["perspective_assessments"]:
            assessment["competence"] = next(
                item["competence"] for item in fixture.perspective_map["perspectives"] if item["role_id"] == assessment["role_id"]
            )
        self.assertEqual(fixture.validate(), [])


if __name__ == "__main__":
    unittest.main()
