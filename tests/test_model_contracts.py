"""Deterministic tests for the addressable structural-model validator."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "model_validation.py"
SPEC = importlib.util.spec_from_file_location("model_validation_under_test", MODULE)
assert SPEC and SPEC.loader
model_validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = model_validation
SPEC.loader.exec_module(model_validation)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return digest(path.read_bytes())


class CompleteModel:
    def __init__(self, base: Path) -> None:
        self.run = base / "runs" / "RUN-1"
        self.snapshot = "1" * 64
        self.bank = {
            "requirements": ["R-001"],
            "questions": [{"id": "Q-001", "text": "one addressable question", "source_offset": "1"}],
        }
        self.results: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, dict[str, Any]] = {}
        self.review_index = 0
        self.bank_hash = write(self.run / "input" / "bank.yaml", self.bank)
        self._build()

    def common(self, result_id: str, artifact_type: str, role: str, agent: str) -> dict[str, Any]:
        return {
            "schema_version": "question-space-v4",
            "id": result_id,
            "run_id": "RUN-1",
            "input_snapshot": self.snapshot,
            "author_role": role,
            "agent_id": agent,
            "knowledge_status": "DERIVED",
            "workflow_status": "ACCEPTED",
            "source_refs": [],
            "related_ids": [],
            "revision": "1",
            "created_at": "2026-10-21T09:00:00Z",
            "updated_at": "2026-10-21T09:00:00Z",
            "artifact_type": artifact_type,
            "assignment_id": "ASG-" + result_id,
            "attempt_id": "ATT-1",
        }

    def add_result(self, result_id: str, artifact_type: str, role: str, agent: str, relative: str, extra: dict[str, Any] | None = None) -> None:
        doc = self.common(result_id, artifact_type, role, agent)
        if extra:
            doc.update(extra)
        content_hash = write(self.run / relative, doc)
        self.docs[result_id] = doc
        self.results[result_id] = {
            "result_id": result_id,
            "artifact_type": artifact_type,
            "role_id": role,
            "agent_id": agent,
            "assignment_id": doc["assignment_id"],
            "attempt_id": doc["attempt_id"],
            "input_snapshot": self.snapshot,
            "path": relative,
            "content_hash": content_hash,
        }

    def add_review(self, result_id: str, reviewer_number: int, target_id: str, kind: str, suffix: str) -> None:
        role = f"P-{reviewer_number:02d}"
        agent = "/root/" + role
        target = self.results[target_id]
        framing_id = "FRAME-" + role
        self.review_index += 1
        extra = {
            "target_result_id": target_id,
            "target_hash": target["content_hash"],
            "reviewer_competence": "competence for exact check " + suffix,
            "nonauthor_declaration": "reviewer did not author exact target " + suffix,
            "blind_position_hash": self.results[framing_id]["content_hash"],
            "exposures": [],
            "review_kind": kind,
            "check_id": "CHECK-" + suffix,
            "check_provenance": "independent provenance " + suffix,
            "risk_coverage": "specific covered risk " + suffix,
            "blind_input_result_id": framing_id,
            "blind_input_hash": self.results[framing_id]["content_hash"],
            "procedure": "bounded reproducible procedure " + suffix,
            "risk_tested": "distinct tested failure " + suffix,
            "claim_scope": "exact accepted target scope " + suffix,
            "counterexample_or_observation": "targeted observation recorded " + suffix,
            "outcome": "PASS",
            "required_repair": [],
            "regression": [],
            "goal_impact": "declared impact on goal " + suffix,
            "independence_limits": [],
        }
        self.add_result(result_id, "REVIEW", role, agent, "reviews/" + result_id + ".yaml", extra)

    def stage(self, stage_id: str, task_id: str, check_id: str, predecessors: list[str], next_stages: list[str]) -> dict[str, Any]:
        entry = "ENTRY-" + stage_id
        exit_id = "EXIT-" + stage_id
        first = task_id == "T-000"
        input_contract = (
            {
                "id": "INPUT-" + task_id,
                "input_kind": "EXISTING",
                "contract": "meaning schema units freshness authority",
                "consumer_ids": [task_id],
                "source_id": "E-001",
                "version": "accepted revision 1",
                "identity": "exact accepted evidence result E-001",
                "accepted_hash": self.results["E-001"]["content_hash"],
            }
            if first else
            {
                "id": "INPUT-" + task_id,
                "input_kind": "PLANNED",
                "contract": "meaning schema units freshness authority",
                "consumer_ids": [task_id],
                "producer_task_id": "T-000",
                "output_id": "OUTPUT-T-000",
                "acceptance_check_id": "C-000",
            }
        )
        output_path = "artifact/" + task_id + ".yaml"
        return {
            "plan_version": "1",
            "owner": "named plan owner " + stage_id,
            "result": "observable stage result " + stage_id,
            "sections": [str(index) for index in range(1, 9)],
            "identity_result": {
                "stage_id": stage_id,
                "plan_version": "1",
                "owner": "named plan owner " + stage_id,
                "requirements": ["R-001"],
                "decisions": ["D-001"],
                "accepted_result": "observable accepted stage result " + stage_id,
            },
            "local_execution_graph": {
                "predecessors": predecessors,
                "entry": entry,
                "initial_state": "declared initial state " + stage_id,
                "first_task": task_id,
                "edges": [
                    {"from": entry, "to": task_id},
                    {"from": task_id, "to": exit_id},
                ],
                "bounded_iterations": [],
                "parallel_waves": [],
                "write_conflict_policy": "serialize every shared write by dependency",
                "exit": exit_id,
                "next_stages": next_stages,
            },
            "inputs_contracts": {
                "inputs": [input_contract],
                "outputs": [{
                    "id": "OUTPUT-" + task_id,
                    "path": output_path,
                    "producer_task_id": task_id,
                    "acceptance_check_id": check_id,
                    "consumer_ids": ["T-001"] if first else [],
                    "contract": "meaning schema units and consumer",
                    "availability": "UNCONDITIONAL",
                    "branch_guard": "declared guard evaluates true",
                    "acceptance_status": "PLANNED",
                }],
            },
            "tasks": [{
                "id": task_id,
                "owner": "named task owner " + task_id,
                "execution_status": "NOT_STARTED",
                "result": "one observable task result " + task_id,
                "procedure": "finite exact procedure " + task_id,
                "algorithms": ["bounded deterministic algorithm"],
                "inputs": ["INPUT-" + task_id],
                "dependencies": [] if task_id == "T-000" else ["T-000"],
                "outputs": [output_path],
                "errors_recovery": "bounded retry and compensation",
                "resources": "finite time compute data and money",
                "acceptance_criteria": [check_id],
                "checks": [check_id],
                "r_refs": ["R-001"],
                "d_refs": ["D-001"],
                "e_refs": ["E-001"],
                "branch_guard": "declared guard evaluates true",
                "writes": ["artifact/shared.yaml"],
                "serialized_with": [] if task_id == "T-000" else ["T-000"],
            }],
            "outputs_handoff": {
                "artifacts": [output_path],
                "consumers": [check_id],
                "allowed_next_stages": next_stages,
                "forbidden_transitions": [],
            },
            "acceptance_exit": [{
                "id": check_id,
                "property": "required observable property " + check_id,
                "task_id": task_id,
                "inputs": [output_path],
                "preconditions": ["task completed"],
                "environment": "declared deterministic environment",
                "procedure": "exact finite acceptance procedure",
                "expected_observation": "specified observation means pass",
                "negative_check": "negative mutation must be rejected",
                "boundary_check": "boundary input remains inside contract",
                "sensitivity_check": "single perturbation changes expected result",
                "checker": "competent nonauthor checker",
            }],
            "resources_stop": {
                "budget": "finite declared budget",
                "estimate_basis": "measured fixture size",
                "monitoring": "observable counters and deadline",
                "stop_action": "stop and preserve checkpoint",
            },
            "errors_recovery_evidence": {
                "retry": "one bounded idempotent retry",
                "idempotency": "stable assignment and attempt key",
                "compensation": "restore prior accepted pointer",
                "checkpoint": "after accepted task atom",
                "existing": ["E-001"],
                "future_not_run": [],
                "drift_recheck": [],
            },
        }

    def gate(self, number: int, review_id: str, evidence_id: str) -> dict[str, Any]:
        gate_id = "GATE-" + str(number)
        review_entry = self.results[review_id]
        review_doc = self.docs[review_id]
        evidence = self.results[evidence_id]
        normative_reviews = sorted(
            result_id for result_id, entry in self.results.items()
            if entry["artifact_type"] == "REVIEW"
            and self.docs[result_id].get("target_result_id", "").startswith(("PRIMARY-", "D-"))
        )
        required_by_gate = {
            1: ["input/bank.yaml", "VECTOR-001", "S-000"]
                + [f"FRAME-P-{index:02d}" for index in range(1, 11)],
            2: ["SOURCE-OUTCOMES-001", "ANSWER-001", "S-000"],
            3: normative_reviews + ["S-000"],
            4: ["SYNTHESIS-001", "S-000"],
            5: ["S-000", "S-001"],
            6: ["AUDIT-001"],
        }
        required_objects = required_by_gate[number]
        required_hashes = []
        for result_id in required_objects:
            content_hash = (
                self.bank_hash if result_id == "input/bank.yaml"
                else self.results[result_id]["content_hash"]
            )
            if content_hash not in required_hashes:
                required_hashes.append(content_hash)
        return {
            "schema_version": "question-space-v4",
            "id": gate_id,
            "run_id": "RUN-1",
            "input_snapshot": self.snapshot,
            "author_role": "COORDINATOR",
            "agent_id": "/root/coordinator",
            "knowledge_status": "OBSERVED",
            "workflow_status": "ACCEPTED",
            "source_refs": ["input/snapshot.yaml"],
            "related_ids": ["R-001"],
            "revision": "1",
            "created_at": "2026-10-21T09:00:00Z",
            "updated_at": "2026-10-21T09:00:00Z",
            "gate_id": gate_id,
            "required_objects": required_objects,
            "completed_checks": [{
                "check_id": "CHECK-" + gate_id,
                "property": "exact accepted evidence and target remain bound",
                "outcome": "PASS",
                "evidence_result_id": evidence_id,
                "evidence_sha256": evidence["content_hash"],
            }],
            "content_review": {
                "status": "ACCEPTED",
                "result_id": review_id,
                "sha256": review_entry["content_hash"],
                "reviewer_role": review_entry["role_id"],
                "reviewer_agent_id": review_entry["agent_id"],
                "target_hashes": required_hashes,
                "outcome": "PASS",
            },
            "unresolved_critical": [],
            "decision": "PASS",
            "allowed_next": [] if number == 6 else ["GATE-" + str(number + 1)],
            "blocked_dependencies": [],
            "evidence_refs": [{"result_id": evidence_id, "sha256": evidence["content_hash"]}],
        }

    def _build(self) -> None:
        self.add_result(
            "E-001", "EVIDENCE", "OP-VECTOR", "/root/OP-VECTOR", "evidence/E-001.yaml",
            {"changed": "UNCHANGED", "observation": "deterministic fixture observation"},
        )
        self.add_result(
            "VECTOR-001", "OPERATOR", "OP-VECTOR", "/root/OP-VECTOR",
            "operators/VECTOR-001.yaml",
            {"goal": "bounded goal", "requirements": ["R-001"]},
        )
        for number in range(1, 11):
            role = f"P-{number:02d}"
            agent = "/root/" + role
            self.add_result(
                "FRAME-" + role, "FRAMING", role, agent, "positions/FRAME-" + role + ".yaml",
                {
                    "protocol_blindness": "VERIFIED",
                    "disclosure_read_log": [],
                    "vector_result_id": "VECTOR-001",
                },
            )
            self.add_result(
                "PRIMARY-" + role, "PRIMARY", role, agent, "positions/PRIMARY-" + role + ".yaml",
                {"self_critique": {
                    "strongest_competing_cause": "a named competing cause could explain the observation",
                    "false_success_example": "formal completion could miss the useful result",
                    "falsification_condition": "a bounded negative observation would refute this position",
                    "goal_consequence": "failure would prevent the declared goal path",
                }},
            )
        self.add_result(
            "ANSWER-001", "ANSWER", "P-01", "/root/P-01", "answers/ANSWER-001.yaml",
            {"addressed_q_ids": ["Q-001"], "answer": "bounded current answer"},
        )
        self.add_result(
            "SOURCE-OUTCOMES-001", "SOURCE_OUTCOMES", "OP-VECTOR", "/root/OP-VECTOR",
            "answers/source-outcomes/SOURCE-OUTCOMES-001.yaml",
            {"records": [{
                "id": "SOURCE-Q-001",
                "record_kind": "SOURCE_OUTCOME",
                "original_q_ids": ["Q-001"],
                "source_disposition": "KEPT",
                "disposition_target_ids": [],
                "answer_status": "ANSWERED",
                "knowledge_state": "DERIVED",
                "author_assignment_id": self.results["ANSWER-001"]["assignment_id"],
                "answer_result_id": "ANSWER-001",
                "evidence_ids": ["E-001"],
            }]},
        )
        self.add_result(
            "D-001", "DECISION", "OP-SYNTHESIS", "/root/OP-SYNTHESIS", "decisions/D-001.yaml",
            {"criticality": "CRITICAL", "mechanism": "bounded selected mechanism"},
        )

        # Each P reviews two other P PRIMARY results; the ring also gives every P two incoming reviews.
        for reviewer in range(1, 11):
            for offset in (1, 2):
                target = ((reviewer - 1 + offset) % 10) + 1
                self.add_review(
                    f"REVIEW-PEER-{reviewer:02d}-{target:02d}",
                    reviewer,
                    f"PRIMARY-P-{target:02d}",
                    "PEER",
                    f"PEER-{reviewer:02d}-{target:02d}",
                )
        self.add_review("REVIEW-D-SUBJECT", 1, "D-001", "SUBJECT", "D-SUBJECT")
        self.add_review("REVIEW-D-SYSTEM", 2, "D-001", "SYSTEM", "D-SYSTEM")

        self.add_result(
            "S-000", "PLAN", "PLAN-AUTHOR", "/root/PLAN-AUTHOR", "plan/S-000.yaml",
            self.stage("S-000", "T-000", "C-000", [], ["S-001"]),
        )
        self.add_result(
            "S-001", "PLAN", "PLAN-AUTHOR", "/root/PLAN-AUTHOR", "plan/S-001.yaml",
            self.stage("S-001", "T-001", "C-001", ["S-000"], []),
        )

        for number in range(1, 6):
            review_id = "REVIEW-GATE-" + str(number)
            self.add_review(review_id, number + 2, "S-000", "PEER", "GATE-" + str(number))

        stage_hash = self.results["S-000"]["content_hash"]
        area_ids = {
            "1": ["R-001"],
            "2": ["Q-001"],
            "3": ["E-001"],
            "4": ["REVIEW-D-SUBJECT"],
            "5": ["D-001"],
            "6": ["S-000", "T-000"],
            "7": ["C-000"],
            "8": ["AUDIT-001"],
        }
        details = {}
        for area_id in map(str, range(1, 9)):
            details[area_id] = {
                "ids": area_ids[area_id],
                "files": ["plan/S-000.yaml"],
                "hashes": [stage_hash],
                "procedure": "bounded destructive structural procedure for area " + area_id,
                "findings": ["addressable structures and exact bytes were checked for area " + area_id],
                "counterexample_result": "a targeted malformed counterexample was rejected for area " + area_id,
                "alternative_result": "a stronger bounded structural alternative was compared for area " + area_id,
                "limitations": ["structural evidence does not establish substantive truth"],
                "defect_ids": [],
                "open_condition_ids": [],
                "readiness_impact": "no adverse structural readiness impact was found",
            }
        self.add_result(
            "AUDIT-001", "FINAL_AUDIT", "FINAL-AUDITOR", "/root/FINAL-AUDITOR", "audit/final.yaml",
            {
                "core_manifest_sha256": "a" * 64,
                "independence_mode": "INDEPENDENT",
                "auditor_independence": "auditor did not author plan, last fix, or checked procedure",
                "auditor_exposure": [],
                "areas": {str(index): "PASS" for index in range(1, 9)},
                "area_details": details,
                "defects": [],
                "counterexample_results": ["all registered destructive counterexamples were structurally rejected"],
                "alternative_results": ["a bounded stronger alternative was examined for every audit area"],
                "verdict": "READY",
                "verdict_reasons": ["all eight addressable structural areas passed with exact bindings"],
                "last_fix_status": "CHECKED",
                "readiness": {
                    "package": "READY",
                    "runtime": "UNVERIFIED",
                    "quality": "UNVERIFIED",
                    "plan": "READY",
                    "implementation": "UNVERIFIED",
                    "effect": "UNVERIFIED",
                },
                "plan_readiness": "READY",
                "implementation_status": "NOT_RUN",
                "effect_status": "NOT_RUN",
                "open_conditions": [],
                "allowed_next_action": "independent substantive evaluation may proceed",
                "claim_limits": ["structural validation does not establish truth or effect"],
            },
        )
        self.add_review("REVIEW-GATE-6", 8, "AUDIT-001", "PEER", "GATE-6")

        review_ids = sorted(
            result_id for result_id, entry in self.results.items()
            if entry["artifact_type"] == "REVIEW"
            and result_id != "REVIEW-GATE-6"
        )
        graph_edges: list[dict[str, str]] = [
            {"from_type": "E", "from_id": "E-001", "to_type": "Q", "to_id": "Q-001"},
            {"from_type": "Q", "from_id": "Q-001", "to_type": "D", "to_id": "D-001"},
        ]
        for review_id in review_ids:
            graph_edges.append({"from_type": "D", "from_id": "D-001", "to_type": "REVIEW", "to_id": review_id})
            graph_edges.append({"from_type": "REVIEW", "from_id": review_id, "to_type": "T", "to_id": "T-000"})
        graph_edges.extend([
            {"from_type": "REVIEW", "from_id": "REVIEW-D-SYSTEM", "to_type": "T", "to_id": "T-001"},
            {"from_type": "T", "from_id": "T-000", "to_type": "C", "to_id": "C-000"},
            {"from_type": "T", "from_id": "T-001", "to_type": "C", "to_id": "C-001"},
        ])
        self.add_result(
            "SYNTHESIS-001", "SYNTHESIS", "OP-SYNTHESIS", "/root/OP-SYNTHESIS", "synthesis/SYNTHESIS-001.yaml",
            {
                "comparison_protocol": "frozen comparison against goal requirements evidence cost risk and feasibility",
                "chosen_mechanism": "bounded selected mechanism with explicit contracts",
                "alternatives": [
                    {"id": "A", "direction": "improve current", "mechanism": "strengthen current chain", "comparison": "lower change cost with bounded limits", "outcome": "REJECTED"},
                    {"id": "B", "direction": "replace central mechanism", "mechanism": "replace the central chain", "comparison": "higher change cost without stronger evidence", "outcome": "REJECTED"},
                    {"id": "C", "direction": "construct from goal", "mechanism": "derive a new goal-first chain", "comparison": "useful control but weaker feasibility", "outcome": "REJECTED"},
                ],
                "semantic_edges": [dict(edge, relation="supports") for edge in graph_edges],
                "evidence_provenance_edges": [],
                "execution_edges": graph_edges,
                "node_status": {},
            },
        )

        for number in range(1, 6):
            write(self.run / "gates" / f"GATE-{number}.yaml", self.gate(number, "REVIEW-GATE-" + str(number), "E-001"))
        write(self.run / "gates" / "GATE-6.yaml", self.gate(6, "REVIEW-GATE-6", "AUDIT-001"))

    def validate(self, phase: str = "final") -> list[str]:
        return model_validation.validate_model(
            self.run,
            self.bank,
            self.results,
            self.docs,
            phase,
            load_yaml,
            digest,
        )


class ModelContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.model = CompleteModel(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_complete_addressable_model_passes_structurally(self) -> None:
        self.assertEqual(self.model.validate(), [])

    def test_explicit_projection_accepts_immutable_plan_revision_history(self) -> None:
        import copy
        results=copy.deepcopy(self.model.results); docs=copy.deepcopy(self.model.docs)
        projection={}
        for old_id,new_id,predecessors,next_stages in (("S-000","S-000-R2",[],["S-001-R2"]),("S-001","S-001-R2",["S-000-R2"],[])):
            document=copy.deepcopy(docs[old_id]); document.update({"id":new_id,"assignment_id":"ASG-"+old_id,"attempt_id":"ATT-2","revision":"2","related_ids":[old_id],"supersedes":old_id,"plan_version":"2"})
            document["identity_result"]=copy.deepcopy(document["identity_result"]); document["identity_result"]["plan_version"]="2"
            document["local_execution_graph"]=copy.deepcopy(document["local_execution_graph"]); document["local_execution_graph"]["predecessors"]=predecessors; document["local_execution_graph"]["next_stages"]=next_stages
            docs[new_id]=document; entry=copy.deepcopy(results[old_id]); entry.update({"result_id":new_id,"assignment_id":"ASG-"+old_id,"attempt_id":"ATT-2","content_hash":"b"*64}); results[new_id]=entry; projection[new_id]=old_id
        gate5_path = self.model.run / "gates" / "GATE-5.yaml"
        gate5 = load_yaml(gate5_path)
        gate5["required_objects"].extend(sorted(projection))
        gate5["content_review"]["target_hashes"].append("b" * 64)
        write(gate5_path, gate5)
        errors=model_validation.validate_model(self.model.run,self.model.bank,results,docs,"final",load_yaml,digest,projection)
        self.assertEqual(errors,[])
        errors=model_validation.validate_model(self.model.run,self.model.bank,results,docs,"final",load_yaml,digest,{"S-000-R2":"S-000"})
        self.assertIn("stage:S-000:unknown_next:S-001-R2",errors)

    def test_core_excludes_final_audit_and_gate6(self) -> None:
        self.model.docs["AUDIT-001"].pop("area_details")
        (self.model.run / "gates" / "GATE-6.yaml").unlink()
        self.assertEqual(self.model.validate("core"), [])

    def test_core_allows_future_administrative_gate6_review_outside_graph(self) -> None:
        for result_id in ("AUDIT-001", "REVIEW-GATE-6"):
            entry = self.model.results.pop(result_id)
            self.model.docs.pop(result_id)
            (self.model.run / entry["path"]).unlink()
        (self.model.run / "gates" / "GATE-6.yaml").unlink()
        self.assertEqual(self.model.validate("core"), [])

    def test_final_requires_accepted_administrative_gate6_review(self) -> None:
        entry = self.model.results.pop("REVIEW-GATE-6")
        self.model.docs.pop("REVIEW-GATE-6")
        (self.model.run / entry["path"]).unlink()
        errors = self.model.validate("final")
        self.assertIn("gate:GATE-6:content_review_binding", errors)

    def test_selected_versioned_gate6_wins_and_legacy_requires_singleton(self) -> None:
        canonical = self.model.run / "gates" / "GATE-6.yaml"
        selected = self.model.run / "gates" / "GATE-6" / "AUDIT-001.yaml"
        write(selected, load_yaml(canonical))
        stale = load_yaml(canonical)
        stale["content_review"]["status"] = "STALE"
        write(canonical, stale)
        errors = model_validation.validate_model(
            self.model.run, self.model.bank, self.model.results, self.model.docs,
            "final", load_yaml, digest,
            current_source_outcomes_id="SOURCE-OUTCOMES-001",
            current_final_audit_id="AUDIT-001",
        )
        self.assertEqual(errors, [])

        selected.unlink()
        old = copy.deepcopy(self.model.docs["AUDIT-001"])
        old.update({
            "id": "AUDIT-000",
            "assignment_id": "ASG-AUDIT-000",
            "attempt_id": "ATT-0",
            "revision": "0",
        })
        common_fields = {
            "schema_version", "id", "run_id", "input_snapshot", "author_role",
            "agent_id", "knowledge_status", "workflow_status", "source_refs",
            "related_ids", "revision", "created_at", "updated_at", "artifact_type",
            "assignment_id", "attempt_id",
        }
        self.model.add_result(
            "AUDIT-000", "FINAL_AUDIT", "FINAL-AUDITOR", "/root/FINAL-AUDITOR",
            "audit/final/AUDIT-000.yaml",
            {key: value for key, value in old.items() if key not in common_fields},
        )
        errors = model_validation.validate_model(
            self.model.run, self.model.bank, self.model.results, self.model.docs,
            "final", load_yaml, digest,
            current_source_outcomes_id="SOURCE-OUTCOMES-001",
            current_final_audit_id="AUDIT-001",
        )
        self.assertIn("gate:GATE-6:missing_or_invalid", errors)

    def test_core_rejects_arbitrary_ghost_review_endpoint(self) -> None:
        edges = self.model.docs["SYNTHESIS-001"]["execution_edges"]
        edges.extend([
            {
                "from_type": "D", "from_id": "D-001",
                "to_type": "REVIEW", "to_id": "REVIEW-GHOST",
            },
            {
                "from_type": "REVIEW", "from_id": "REVIEW-GHOST",
                "to_type": "T", "to_id": "T-000",
            },
        ])
        errors = self.model.validate("core")
        self.assertIn(
            "execution_edge:D:D-001->REVIEW:REVIEW-GHOST:missing_to",
            errors,
        )
        self.assertIn(
            "execution_edge:REVIEW:REVIEW-GHOST->T:T-000:missing_from",
            errors,
        )

    def test_missing_named_stage_section_is_addressed(self) -> None:
        self.model.docs["S-001"].pop("resources_stop")
        errors = self.model.validate()
        self.assertIn("stage:S-001:missing_named_section:7:resources_stop", errors)

    def test_global_stage_cycle_is_rejected(self) -> None:
        self.model.docs["S-000"]["local_execution_graph"]["predecessors"] = ["S-001"]
        self.model.docs["S-001"]["local_execution_graph"]["next_stages"] = ["S-000"]
        errors = self.model.validate()
        self.assertIn("stage_graph:cycle", errors)

    def test_false_R_D_E_C_references_are_rejected(self) -> None:
        task = self.model.docs["S-001"]["tasks"][0]
        task["r_refs"] = ["R-404"]
        task["d_refs"] = ["D-404"]
        task["e_refs"] = ["E-404"]
        task["checks"] = ["C-404"]
        task["acceptance_criteria"] = ["C-404"]
        errors = self.model.validate()
        self.assertIn("task:T-001:unknown_r_ref:R-404", errors)
        self.assertIn("task:T-001:unknown_d_ref:D-404", errors)
        self.assertIn("task:T-001:unknown_e_ref:E-404", errors)
        self.assertIn("task:T-001:unknown_c_ref:C-404", errors)

    def test_reused_critical_review_proof_is_rejected(self) -> None:
        subject = self.model.docs["REVIEW-D-SUBJECT"]
        system = self.model.docs["REVIEW-D-SYSTEM"]
        for field in ("check_id", "check_provenance", "procedure", "risk_coverage", "risk_tested"):
            system[field] = subject[field]
        errors = self.model.validate()
        self.assertIn("decision:D-001:reused_review_proof", errors)

    def test_only_peer_reviews_satisfy_perspective_quotas(self) -> None:
        for result_id, entry in self.model.results.items():
            if (
                entry["artifact_type"] == "REVIEW"
                and self.model.docs[result_id]["target_result_id"].startswith("PRIMARY-P-")
            ):
                self.model.docs[result_id]["review_kind"] = "SUBJECT"
        errors = self.model.validate()
        self.assertIn("primary:PRIMARY-P-01:peer_review_coverage", errors)
        self.assertIn("perspective:P-01:outgoing_peer_review_coverage", errors)

    def test_each_local_task_must_reach_its_stage_exit(self) -> None:
        graph = self.model.docs["S-001"]["local_execution_graph"]
        graph["edges"] = [
            {"from": "ENTRY-S-001", "to": "T-001"},
            {"from": "ENTRY-S-001", "to": "EXIT-S-001"},
        ]
        errors = self.model.validate()
        self.assertIn("stage:S-001:task_cannot_reach_exit:T-001", errors)

    def test_cross_stage_task_dependency_cannot_reverse_stage_order(self) -> None:
        self.model.docs["S-001"]["tasks"][0]["dependencies"] = []
        self.model.docs["S-000"]["tasks"][0]["dependencies"] = ["T-001"]
        errors = self.model.validate()
        self.assertIn("task:T-000:dependency_reverses_stage_order:T-001", errors)

    def test_every_stage_uses_one_plan_version(self) -> None:
        self.model.docs["S-001"]["plan_version"] = "2"
        self.model.docs["S-001"]["identity_result"]["plan_version"] = "2"
        errors = self.model.validate()
        self.assertIn("stage_graph:plan_version_mismatch", errors)

    def test_status_only_gate_placeholder_is_rejected(self) -> None:
        path = self.model.run / "gates" / "GATE-2.yaml"
        gate = load_yaml(path)
        gate["content_review"] = {"status": "ACCEPTED"}
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-2:content_review_placeholder", errors)

    def test_gate_required_objects_resolve_to_accepted_results(self) -> None:
        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        gate["required_objects"] = ["MISSING-OBJECT"]
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:required_object:MISSING-OBJECT", errors)

    def test_gate_can_bind_frozen_requirement_and_exact_input_evidence(self) -> None:
        input_hash = write(self.model.run / "input" / "bank.yaml", self.model.bank)
        review_id = "REVIEW-GATE-1"
        review_doc = self.model.docs[review_id]
        review_doc["target_result_id"] = "R-001"
        review_doc["target_hash"] = input_hash
        review_hash = write(
            self.model.run / self.model.results[review_id]["path"],
            review_doc,
        )
        self.model.results[review_id]["content_hash"] = review_hash

        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        gate["required_objects"].append("R-001")
        gate["completed_checks"][0]["evidence_result_id"] = "input/bank.yaml"
        gate["completed_checks"][0]["evidence_sha256"] = input_hash
        gate["content_review"]["sha256"] = review_hash
        gate["content_review"]["target_hashes"] = list(dict.fromkeys(
            gate["content_review"]["target_hashes"] + [input_hash]
        ))
        gate["evidence_refs"] = [{
            "result_id": "input/bank.yaml",
            "sha256": input_hash,
        }]
        write(path, gate)
        self.assertEqual(self.model.validate(), [])

    def test_gate_content_review_targets_exact_required_object(self) -> None:
        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        gate["required_objects"] = ["S-001"]
        gate["content_review"]["target_hashes"] = [
            self.model.results["S-001"]["content_hash"]
        ]
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:content_review_target_mismatch", errors)

    def test_gate_check_and_evidence_ref_reject_primary_as_evidence(self) -> None:
        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        primary_hash = self.model.results["PRIMARY-P-01"]["content_hash"]
        gate["completed_checks"][0]["evidence_result_id"] = "PRIMARY-P-01"
        gate["completed_checks"][0]["evidence_sha256"] = primary_hash
        gate["evidence_refs"] = [{
            "result_id": "PRIMARY-P-01",
            "sha256": primary_hash,
        }]
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:check:0:evidence_type:PRIMARY", errors)
        self.assertIn("gate:GATE-1:evidence_ref:0:type:PRIMARY", errors)

    def test_gate_evidence_refs_cover_each_completed_check(self) -> None:
        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        gate["evidence_refs"] = []
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:check:0:evidence_ref_missing", errors)

    def test_gate_rejects_stale_content_review_target_hash(self) -> None:
        path = self.model.run / "gates" / "GATE-1.yaml"
        gate = load_yaml(path)
        gate["content_review"]["target_hashes"] = ["f" * 64]
        write(path, gate)
        errors = self.model.validate()
        self.assertIn("gate:GATE-1:content_review_target_hashes", errors)

    def test_incomplete_audit_area_details_are_rejected(self) -> None:
        self.model.docs["AUDIT-001"]["area_details"]["4"].pop("findings")
        errors = self.model.validate()
        self.assertIn("audit:AUDIT-001:area:4:contract", errors)

    def test_source_outcome_status_and_knowledge_fail_before_commit(self) -> None:
        original = self.model.docs["SOURCE-OUTCOMES-001"]
        for field in ("answer_status", "knowledge_state"):
            for invalid in (None, "", "BANANA", [], {}):
                with self.subTest(field=field, invalid=invalid):
                    candidate = copy.deepcopy(original)
                    if invalid is None:
                        candidate["records"][0].pop(field)
                    else:
                        candidate["records"][0][field] = invalid
                    errors = model_validation.validate_intake_candidate(
                        self.model.run, self.model.bank, self.model.results,
                        candidate, "SOURCE_OUTCOMES", load_yaml,
                    )
                    self.assertIn(
                        "intake:SOURCE_OUTCOMES:SOURCE-OUTCOMES-001:record:0:" + field,
                        errors,
                    )

        for status in ("ANSWERED", "OPEN", "NOT_APPLICABLE"):
            for knowledge in ("OBSERVED", "DERIVED", "HYPOTHESIS", "REFUTED", "UNKNOWN"):
                with self.subTest(status=status, knowledge=knowledge):
                    candidate = copy.deepcopy(original)
                    candidate["records"][0].update(answer_status=status, knowledge_state=knowledge)
                    self.assertEqual(model_validation.validate_intake_candidate(
                        self.model.run, self.model.bank, self.model.results,
                        candidate, "SOURCE_OUTCOMES", load_yaml,
                    ), [])

    def test_missing_status_cannot_evade_ready_guard(self) -> None:
        self.model.docs["SOURCE-OUTCOMES-001"]["records"][0].pop("answer_status")
        self.assertIn(
            "intake:SOURCE_OUTCOMES:SOURCE-OUTCOMES-001:record:0:answer_status",
            self.model.validate(),
        )

    def test_gate2_can_pass_with_addressed_open_outcome_without_final_ready(self) -> None:
        self.model.docs["SOURCE-OUTCOMES-001"]["records"][0].update(
            answer_status="OPEN", knowledge_state="UNKNOWN",
        )
        self.assertEqual(self.model.validate("core"), [])
        self.assertIn("audit:AUDIT-001:ready_with_open_source_question", self.model.validate())

    def test_extension_cannot_bypass_required_audit_area_fields(self) -> None:
        original = copy.deepcopy(self.model.docs["AUDIT-001"]["area_details"]["4"])
        for field in ("defect_ids", "open_condition_ids"):
            with self.subTest(field=field):
                detail = copy.deepcopy(original)
                detail.pop(field)
                detail["extension"] = "additional provenance is permitted"
                self.model.docs["AUDIT-001"]["area_details"]["4"] = detail
                self.assertIn("audit:AUDIT-001:area:4:contract", self.model.validate())

    def test_plan_extension_cannot_bypass_missing_required_lists(self) -> None:
        stage = self.model.docs["S-001"]
        stage["tasks"][0].pop("algorithms")
        stage["tasks"][0]["extension"] = "additional task provenance"
        stage["acceptance_exit"][0].pop("preconditions")
        stage["acceptance_exit"][0]["extension"] = "additional check provenance"
        errors = self.model.validate()
        self.assertIn("task:T-001:contract_fields", errors)
        self.assertIn("check:C-001:contract_fields", errors)

    def test_missing_task_id_with_extension_returns_error_instead_of_crashing(self) -> None:
        stage = self.model.docs["S-001"]
        malformed = copy.deepcopy(stage["tasks"][0])
        malformed.pop("id")
        malformed["extension"] = "additional task provenance"
        stage["tasks"].append(malformed)
        self.assertIn("task:index-1:contract_fields", self.model.validate())

    def test_additional_plan_metadata_does_not_invalidate_complete_contracts(self) -> None:
        stage = self.model.docs["S-001"]
        for value in (
            stage["tasks"][0], stage["acceptance_exit"][0],
            stage["inputs_contracts"]["inputs"][0],
            stage["inputs_contracts"]["outputs"][0],
            self.model.docs["PRIMARY-P-01"]["self_critique"],
        ):
            value["extension"] = "additional bounded provenance"
        self.assertEqual(self.model.validate(), [])

    def test_audit_condition_and_defect_ids_are_typed_unique_lists(self) -> None:
        detail = self.model.docs["AUDIT-001"]["area_details"]["4"]
        for field in ("defect_ids", "open_condition_ids"):
            for invalid in ("OPEN-1", [None], ["OPEN-1", "OPEN-1"]):
                with self.subTest(field=field, invalid=invalid):
                    detail[field] = invalid
                    self.assertIn("audit:AUDIT-001:area:4:" + field + "_contract", self.model.validate())
                    detail[field] = []

    def test_audit_alternative_rejects_bare_na_but_accepts_scoped_rationale(self) -> None:
        detail = self.model.docs["AUDIT-001"]["area_details"]["4"]
        detail["alternative_result"] = "not applicable"
        errors = self.model.validate()
        self.assertIn("audit:AUDIT-001:area:4:alternative_result", errors)

        detail["alternative_result"] = (
            "No stronger alternative applies because area 4 has one declared "
            "review-binding procedure and no competing procedure within this audit scope."
        )
        self.assertEqual(self.model.validate(), [])

    def test_audit_template_has_no_bare_alternative_placeholder(self) -> None:
        template = load_yaml(ROOT / "templates" / "audit.yaml")
        details = template.get("area_details", {})
        self.assertEqual(set(details), {str(index) for index in range(1, 9)})
        for area_id, detail in sorted(details.items()):
            with self.subTest(area_id=area_id):
                self.assertTrue(
                    model_validation._text(detail.get("alternative_result")),
                    "audit template must require a concrete result or scoped rationale",
                )

    def test_serialized_reachable_shared_write_is_allowed_but_undeclared_conflict_fails(self) -> None:
        self.assertFalse(any(error.startswith("write_conflict:") for error in self.model.validate()))
        self.model.docs["S-001"]["tasks"][0]["serialized_with"] = []
        errors = self.model.validate()
        self.assertIn("write_conflict:artifact/shared.yaml:T-000:T-001", errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
