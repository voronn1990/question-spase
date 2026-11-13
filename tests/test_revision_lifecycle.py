"""Public CLI receipts for immutable source/audit revision selection."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
QS = HERE / "scripts" / "qs.py"
spec = importlib.util.spec_from_file_location("qs_revision_test", QS)
assert spec and spec.loader
qs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = qs
spec.loader.exec_module(qs)


def put(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(qs.canon_yaml(value))


class RevisionLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "workspace"
        self.bank_path = self.base / "bank.yaml"
        self.original = self.base / "original.md"
        put(self.bank_path, {"requirements": ["R-1"], "questions": [{"id": "Q-001", "text": "question", "source_offset": "1"}]})
        self.original.write_text("# original\n", encoding="utf-8")
        self.init = self.call("init", "--root", str(self.workspace), "--run-id", "RUN-REV", "--bank", str(self.bank_path), "--original-bank", str(self.original))
        self.run = self.workspace / "runs" / "RUN-REV"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, *args: str, ok: bool = True) -> dict:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        completed = subprocess.run([sys.executable, "-B", str(QS), *args], text=True, capture_output=True, env=env)
        data = json.loads(completed.stdout or completed.stderr)
        if ok:
            self.assertEqual(completed.returncode, 0, data)
        else:
            self.assertNotEqual(completed.returncode, 0, data)
        return data

    def common(self, result_id: str, role: str, agent: str, assignment_id: str, attempt_id: str, artifact_type: str) -> dict:
        return {
            "schema_version": "question-space-v4", "id": result_id, "run_id": "RUN-REV",
            "input_snapshot": self.init["input_snapshot"], "author_role": role, "agent_id": agent,
            "knowledge_status": "DERIVED", "workflow_status": "ACCEPTED", "source_refs": [],
            "related_ids": [], "revision": "1", "created_at": "2026-10-23T00:00:00Z",
            "updated_at": "2026-10-23T00:00:00Z", "assignment_id": assignment_id,
            "attempt_id": attempt_id, "artifact_type": artifact_type,
        }

    def accept(self, result: dict, role: str, artifact_type: str, output: str, *, supersedes_attempt_id: str | None = None, launch_state: str | None = None) -> dict:
        assignment = {
            "assignment_id": result["assignment_id"], "attempt_id": result["attempt_id"],
            "role_id": role, "agent_id": result["agent_id"], "input_snapshot": self.init["input_snapshot"],
            "artifact_type": artifact_type, "output_path": output, "dependency_hashes": [],
            "result_id": result["id"], "selection_authority": "ROOT", "group_id": "1",
            "assigned_ids": {"questions": ["Q-001"] if artifact_type in {"PRIMARY", "ANSWER"} else [], "requirements": [], "decisions": [], "evidence": []},
        }
        if supersedes_attempt_id is not None:
            assignment["supersedes_attempt_id"] = supersedes_attempt_id
        if launch_state is not None:
            assignment["launch_state"] = launch_state
        ap = self.base / (result["id"] + ".assignment.yaml")
        rp = self.base / (result["id"] + ".result.yaml")
        put(ap, assignment); put(rp, result)
        self.call("assign", "--root", str(self.workspace), "--run-id", "RUN-REV", "--assignment", str(ap))
        return self.call("accept", "--root", str(self.workspace), "--run-id", "RUN-REV", "--assignment", str(ap), "--result", str(rp))

    def test_accepted_unknown_launch_allows_explicit_repair_but_incomplete_unknown_blocks(self) -> None:
        decision1 = self.common("D-REPAIR-1", "OP-SYNTHESIS", "/root/synthesis", "A-DECISION-REPAIR", "ATT-1", "DECISION")
        decision1.update({
            "criticality": "CRITICAL", "mechanism": "rejected bounded mechanism",
            "grounds": ["E-OLD"], "alternatives": ["A", "B", "C"],
            "scope": "structural fixture", "risk_of_error": "known subject failure",
            "review_requirements": ["SUBJECT", "SYSTEM"],
        })
        self.accept(decision1, "OP-SYNTHESIS", "DECISION", "decisions/D-REPAIR-1.yaml", launch_state="DISPATCH_UNKNOWN")
        state=qs.load_yaml(self.run/"state/assignments.yaml")
        self.assertEqual(state["registered"]["A-DECISION-REPAIR\0ATT-1"]["launch_state"], "DISPATCH_UNKNOWN")

        decision2 = self.common("D-REPAIR-2", "OP-SYNTHESIS", "/root/synthesis", "A-DECISION-REPAIR", "ATT-2", "DECISION")
        decision2.update({
            "criticality": "CRITICAL", "mechanism": "corrected bounded mechanism",
            "grounds": ["E-NEW"], "alternatives": ["A", "B", "C"],
            "scope": "structural fixture", "risk_of_error": "reviewed current mechanism",
            "review_requirements": ["SUBJECT", "SYSTEM"], "revision": "2",
            "supersedes": "D-REPAIR-1", "related_ids": ["D-REPAIR-1"],
        })
        assignment2 = {
            "assignment_id": "A-DECISION-REPAIR", "attempt_id": "ATT-2", "role_id": "OP-SYNTHESIS",
            "agent_id": "/root/synthesis", "input_snapshot": self.init["input_snapshot"], "artifact_type": "DECISION",
            "output_path": "decisions/D-REPAIR-2.yaml", "dependency_hashes": [], "result_id": "D-REPAIR-2",
            "selection_authority": "ROOT", "group_id": "1", "assigned_ids": {"questions": [], "requirements": [], "decisions": [], "evidence": []},
            "supersedes_attempt_id": "ATT-1",
        }
        assignment2_path=self.base/"D-REPAIR-2.assignment.yaml"; result2_path=self.base/"D-REPAIR-2.result.yaml"
        put(assignment2_path,assignment2); put(result2_path,decision2)
        transaction=self.run/"transactions/D-REPAIR-1.yaml"; transaction_bytes=transaction.read_bytes(); transaction.unlink()
        blocked=self.call("assign","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(assignment2_path),ok=False)
        self.assertEqual(blocked["error"],"retry_predecessor_not_terminal")
        transaction.write_bytes(transaction_bytes)
        self.call("assign","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(assignment2_path))
        self.call("accept","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(assignment2_path),"--result",str(result2_path))
        state=qs.load_yaml(self.run/"state/assignments.yaml")
        self.assertEqual(state["registered"]["A-DECISION-REPAIR\0ATT-1"]["launch_state"], "DISPATCH_UNKNOWN")
        self.assertEqual(state["current_attempts"]["A-DECISION-REPAIR"], "ATT-2")

        state,current,bank=qs.accepted(self.run)
        docs={result_id:qs.load_yaml(self.run/entry["path"]) for result_id,entry in state["results"].items()}
        completed_unknown,current_claims=qs._orchestration_proofs(self.run,state,current)
        self.assertEqual(completed_unknown,{"A-DECISION-REPAIR\0ATT-1":"D-REPAIR-1"})
        approved={
            "input_snapshot":self.init["input_snapshot"], "allowed_roles":sorted(qs.ROLES),
            "role_competence":{role:[] for role in qs.ROLES},
            "question_routes":{"Q-001":{"primary_assignment_id":"A-MISSING","required_tags":[]}},
            "critical_reviews":{}, "evidence_applicability":{},
        }
        checked=qs.validate_orchestration(
            bank,approved,{"registered":state["registered"],"current_attempts":state["current_attempts"]},
            state["results"],docs,[],completed_unknown,current_claims,
        )
        unknown_error="assignment:A-DECISION-REPAIR:attempt:ATT-1:dispatch_unknown_unresolved"
        self.assertNotIn(unknown_error,checked["errors"])
        completed_tx=self.run/"transactions/D-REPAIR-1.yaml"; completed_bytes=completed_tx.read_bytes(); completed_tx.unlink()
        incomplete_unknown,_=qs._orchestration_proofs(self.run,state,current)
        rejected=qs.validate_orchestration(
            bank,approved,{"registered":state["registered"],"current_attempts":state["current_attempts"]},
            state["results"],docs,[],incomplete_unknown,current_claims,
        )
        self.assertIn(unknown_error,rejected["errors"])
        completed_tx.write_bytes(completed_bytes)

        pending1={**assignment2,"assignment_id":"A-PENDING-UNKNOWN","attempt_id":"ATT-1","result_id":"D-PENDING-1","output_path":"decisions/D-PENDING-1.yaml","launch_state":"DISPATCH_UNKNOWN"}
        pending1.pop("supersedes_attempt_id")
        pending2={**pending1,"attempt_id":"ATT-2","result_id":"D-PENDING-2","output_path":"decisions/D-PENDING-2.yaml","supersedes_attempt_id":"ATT-1","launch_state":"NOT_LAUNCHED"}
        pending1_path=self.base/"D-PENDING-1.assignment.yaml"; pending2_path=self.base/"D-PENDING-2.assignment.yaml"
        put(pending1_path,pending1); put(pending2_path,pending2)
        self.call("assign","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(pending1_path))
        blocked=self.call("assign","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(pending2_path),ok=False)
        self.assertEqual(blocked["error"],"retry_predecessor_not_terminal")

    def test_source_outcomes_open_answered_recheck_replacement_and_replay(self) -> None:
        evidence = self.common("E-001", "P-02", "/root/evidence", "A-EVIDENCE", "ATT-1", "EVIDENCE")
        evidence["changed"] = "UNCHANGED"
        self.accept(evidence, "P-02", "EVIDENCE", "evidence/E-001.yaml")
        answer = self.common("ANSWER-001", "P-01", "/root/answer", "A-ANSWER", "ATT-1", "ANSWER")
        answer["addressed_q_ids"] = ["Q-001"]
        self.accept(answer, "P-01", "ANSWER", "answers/ANSWER-001.yaml")

        def outcome(result_id: str, assignment_id: str, attempt: str, status: str, supersedes: str | None = None) -> dict:
            value = self.common(result_id, "P-01", "/root/source", assignment_id, attempt, "SOURCE_OUTCOMES")
            value["records"] = [{"knowledge_state": "UNKNOWN" if status == "OPEN" else "DERIVED", 
                "record_kind": "SOURCE_OUTCOME", "original_q_ids": ["Q-001"],
                "source_disposition": "KEPT", "answer_status": status,
                "author_assignment_id": "A-ANSWER", "answer_result_id": "ANSWER-001", "evidence_ids": ["E-001"],
            }]
            value["dependency_edges"] = [
                {"from_id": "E-001", "to_id": "ANSWER-001"},
                {"from_id": "ANSWER-001", "to_id": result_id},
            ]
            if supersedes is not None:
                value.update({"supersedes": supersedes, "related_ids": [supersedes], "revision": str(int(attempt.removeprefix("ATT-")))})
            return value

        out1 = outcome("OUT-001", "A-SOURCE", "ATT-1", "OPEN")
        first = self.accept(out1, "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT-001.yaml")
        self.assertEqual(first["current_selection"]["result_id"], "OUT-001")
        out2 = outcome("OUT-002", "A-SOURCE", "ATT-2", "ANSWERED", "OUT-001")
        second = self.accept(out2, "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT-002.yaml", supersedes_attempt_id="ATT-1")
        self.assertEqual(second["current_selection"]["result_id"], "OUT-002")
        self.assertTrue((self.run / "answers/source-outcomes/OUT-001.yaml").is_file())
        self.assertTrue((self.run / "answers/source-outcomes/OUT-002.yaml").is_file())

        resumed = self.call("resume", "--root", str(self.workspace), "--run-id", "RUN-REV", "--changed", "E-001")
        self.assertEqual(resumed["selection_state"], {"OUT-002": "NEEDS_RECHECK"})
        self.assertEqual(resumed["stale_result_ids"], ["ANSWER-001", "E-001", "OUT-001", "OUT-002"])

        bad = outcome("OUT-BRANCH", "A-SOURCE-BAD", "ATT-1", "ANSWERED", "OUT-001")
        bad_assignment = {
            "assignment_id": "A-SOURCE-BAD", "attempt_id": "ATT-1", "role_id": "P-01", "agent_id": "/root/source",
            "input_snapshot": self.init["input_snapshot"], "artifact_type": "SOURCE_OUTCOMES",
            "output_path": "answers/source-outcomes/OUT-BRANCH.yaml", "dependency_hashes": [], "result_id": "OUT-BRANCH",
            "selection_authority": "ROOT", "group_id": "1", "assigned_ids": {"questions": [], "requirements": [], "decisions": [], "evidence": []},
        }
        bad_ap=self.base/"OUT-BRANCH.assignment.yaml"; bad_rp=self.base/"OUT-BRANCH.result.yaml"
        put(bad_ap,bad_assignment); put(bad_rp,bad)
        self.call("assign","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(bad_ap))
        failed=self.call("accept","--root",str(self.workspace),"--run-id","RUN-REV","--assignment",str(bad_ap),"--result",str(bad_rp),ok=False)
        self.assertEqual(failed["error"], "current_source_outcomes_supersedes_current_required")
        self.assertFalse((self.run/"transactions/OUT-BRANCH.yaml").exists())
        self.assertFalse((self.run/"answers/source-outcomes/OUT-BRANCH.yaml").exists())

        evidence2 = self.common("E-002", "P-02", "/root/evidence", "A-EVIDENCE", "ATT-2", "EVIDENCE")
        evidence2.update({"changed":"UNCHANGED", "supersedes":"E-001", "related_ids":["E-001"], "revision":"2"})
        self.accept(evidence2, "P-02", "EVIDENCE", "evidence/E-002.yaml", supersedes_attempt_id="ATT-1")
        answer2 = self.common("ANSWER-002", "P-01", "/root/answer", "A-ANSWER", "ATT-2", "ANSWER")
        answer2.update({"addressed_q_ids":["Q-001"], "supersedes":"ANSWER-001", "related_ids":["ANSWER-001"], "revision":"2",
                        "dependency_edges":[{"from_id":"E-002","to_id":"ANSWER-002"}]})
        self.accept(answer2, "P-01", "ANSWER", "answers/ANSWER-002.yaml", supersedes_attempt_id="ATT-1")
        out3 = outcome("OUT-003", "A-SOURCE-RECHECK", "ATT-1", "ANSWERED", "OUT-002")
        out3["records"][0].update({"answer_result_id":"ANSWER-002", "evidence_ids":["E-002"]})
        out3["dependency_edges"]=[{"from_id":"E-002","to_id":"ANSWER-002"},{"from_id":"ANSWER-002","to_id":"OUT-003"}]
        third = self.accept(out3, "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT-003.yaml")
        self.assertEqual(third["current_selection"]["result_id"], "OUT-003")
        resumed = self.call("resume", "--root", str(self.workspace), "--run-id", "RUN-REV")
        self.assertEqual(resumed["selection_state"], {"OUT-003": "CURRENT"})
        validation = self.call("validate", "--root", str(self.workspace), "--run-id", "RUN-REV", "--phase", "core", ok=False)
        self.assertFalse(any(error.startswith("stale_result_ids:") for error in validation["errors"]), validation["errors"])

        transition = qs.load_yaml(self.run / "transactions/current/TX-CURRENT-00000005.yaml")
        predecessor = qs.load_yaml(self.run / "transactions/current/TX-CURRENT-00000004.yaml")
        self.assertEqual(transition["mutation_type"], "SOURCE_OUTCOMES_SELECT")
        self.run.joinpath("state/current.yaml").write_bytes(base64.b64decode(predecessor["after_current_bytes_b64"]))
        recovered = self.call("recover", "--root", str(self.workspace), "--run-id", "RUN-REV")
        self.assertEqual(recovered["current_source_outcomes"]["result_id"], "OUT-003")

        corrupted = dict(transition); corrupted["after_current_sha256"] = "0" * 64
        put(self.run / "transactions/current/TX-CURRENT-00000005.yaml", corrupted)
        failed_recovery = self.call("recover", "--root", str(self.workspace), "--run-id", "RUN-REV", ok=False)
        self.assertTrue(any("current_replay_payload" in error for error in failed_recovery["errors"]))

    def test_final_audit_revisions_have_exact_selected_identity(self) -> None:
        repair = self.common("AUDIT-REPAIR", "FINAL-AUDITOR", "/root/auditor", "A-AUDIT-1", "ATT-1", "FINAL_AUDIT")
        repair.update({"verdict": "REPAIR_REQUIRED", "core_manifest_sha256": "a" * 64})
        first = self.accept(repair, "FINAL-AUDITOR", "FINAL_AUDIT", "audit/final/AUDIT-REPAIR.yaml")
        self.assertEqual(first["current_selection"]["result_id"], "AUDIT-REPAIR")
        ready = self.common("AUDIT-READY", "FINAL-AUDITOR", "/root/auditor-2", "A-AUDIT-2", "ATT-1", "FINAL_AUDIT")
        ready.update({"verdict": "READY", "core_manifest_sha256": "b" * 64, "supersedes": "AUDIT-REPAIR", "related_ids": ["AUDIT-REPAIR"], "revision": "2"})
        second = self.accept(ready, "FINAL-AUDITOR", "FINAL_AUDIT", "audit/final/AUDIT-READY.yaml")
        self.assertEqual(second["current_selection"]["result_id"], "AUDIT-READY")
        recovered = self.call("recover", "--root", str(self.workspace), "--run-id", "RUN-REV")
        self.assertEqual(recovered["current_final_audit"]["path"], "audit/final/AUDIT-READY.yaml")
        self.assertEqual(set(recovered["accepted_result_ids"]), {"AUDIT-READY", "AUDIT-REPAIR"})

    def test_legacy_singleton_is_read_without_fabricating_persisted_selection(self) -> None:
        answer = self.common("ANSWER-LEGACY", "P-01", "/root/answer", "A-ANSWER-LEGACY", "ATT-1", "ANSWER")
        answer["addressed_q_ids"] = ["Q-001"]
        self.accept(answer, "P-01", "ANSWER", "answers/ANSWER-LEGACY.yaml")
        first = self.common("OUT-LEGACY", "P-01", "/root/source", "A-SOURCE-LEGACY", "ATT-1", "SOURCE_OUTCOMES")
        first["records"] = [{"knowledge_state": "UNKNOWN", "record_kind":"SOURCE_OUTCOME","original_q_ids":["Q-001"],"source_disposition":"KEPT","answer_status":"OPEN","author_assignment_id":"A-ANSWER-LEGACY","answer_result_id":"ANSWER-LEGACY","evidence_ids":[]}]
        self.accept(first, "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes.yaml")
        transition = self.run / "transactions/current/TX-CURRENT-00000002.yaml"
        transition.unlink()
        put(self.run / "state/current.yaml", {"schema_version":"question-space-v4","run_id":"RUN-REV","input_snapshot":self.init["input_snapshot"],"revision":"1","status":"INITIALIZED"})
        recovered = self.call("recover", "--root", str(self.workspace), "--run-id", "RUN-REV")
        self.assertEqual(recovered["current_source_outcomes"]["result_id"], "OUT-LEGACY")
        self.assertNotIn("current_source_outcomes", qs.load_yaml(self.run / "state/current.yaml"))
        second = self.common("OUT-MIGRATED", "P-01", "/root/source-2", "A-SOURCE-MIGRATED", "ATT-1", "SOURCE_OUTCOMES")
        second.update({"supersedes":"OUT-LEGACY","related_ids":["OUT-LEGACY"],"revision":"2","records":[{"knowledge_state": "DERIVED", "record_kind":"SOURCE_OUTCOME","original_q_ids":["Q-001"],"source_disposition":"KEPT","answer_status":"ANSWERED","author_assignment_id":"A-ANSWER-LEGACY","answer_result_id":"ANSWER-LEGACY","evidence_ids":[]}]})
        migrated = self.accept(second, "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT-MIGRATED.yaml")
        self.assertEqual(migrated["current_selection"]["result_id"], "OUT-MIGRATED")
        self.assertEqual(qs.load_yaml(self.run / "state/current.yaml")["current_source_outcomes"]["result_id"], "OUT-MIGRATED")

    def test_external_bank_entry_preserves_original_and_rejects_tamper(self) -> None:
        workspace=self.base/"external-work"; goal=self.base/"goal.md"; external=self.base/"external.txt"; statement=self.base/"statement.md"
        goal.write_text("bounded goal\n",encoding="utf-8"); external.write_text("original external bank bytes\n",encoding="utf-8"); statement.write_text("fixture owner statement\n",encoding="utf-8")
        initialized=self.call("init","--root",str(workspace),"--run-id","EXT-1","--mode","BANK_TO_PLAN","--goal",str(goal),"--material",str(statement),"--bank",str(self.bank_path),"--external-bank",str(external),"--test-fixture")
        run=workspace/"runs/EXT-1"; snapshot=qs.load_yaml(run/"input/snapshot.yaml"); binding=snapshot["external_binding"]
        self.assertEqual(snapshot["bank_entry_kind"],"EXTERNAL_READY_BANK")
        self.assertEqual((run/binding["original_path"]).read_bytes(),external.read_bytes())
        self.assertEqual((run/binding["normalized_path"]).read_bytes(),self.bank_path.read_bytes())
        self.assertNotIn("import_binding",snapshot)
        recovered=self.call("recover","--root",str(workspace),"--run-id","EXT-1")
        self.assertEqual(recovered["status"],"RECOVERED")
        vector_assignment={"assignment_id":"A-EXT-VECTOR","attempt_id":"ATT-1","role_id":"OP-VECTOR","agent_id":"/root/vector","input_snapshot":initialized["input_snapshot"],"artifact_type":"OPERATOR","output_path":"operators/EXT-VECTOR.yaml","dependency_hashes":[],"result_id":"EXT-VECTOR","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR"}
        vector={"schema_version":"question-space-v4","id":"EXT-VECTOR","run_id":"EXT-1","input_snapshot":initialized["input_snapshot"],"author_role":"OP-VECTOR","agent_id":"/root/vector","knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-23T00:00:00Z","updated_at":"2026-10-23T00:00:00Z","assignment_id":"A-EXT-VECTOR","attempt_id":"ATT-1","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR","G":"bounded goal","NOT_G":["unbounded result"],"value_recipient":"owner","scope":"fixture","success_criteria":["criterion"],"requirements":["R-1"],"requirement_definitions":[{"id":"R-1","definition":"bound"}],"hard_constraints":["local only"],"changeable_assumptions":["choice"],"current_state":"research","resources":"local","authority":"fixture owner","owner_decisions":[{"id":"OD-1","decision_needed":"approve vector","why_owner_only":"goal authority","known_context":"fixture","answer_effect":"continue"}],"open_research_questions":["unknown"]}
        vap=self.base/"external-vector.assignment.yaml"; vrp=self.base/"external-vector.result.yaml"; put(vap,vector_assignment); put(vrp,vector)
        self.call("assign","--root",str(workspace),"--run-id","EXT-1","--assignment",str(vap))
        vector_accept=self.call("accept","--root",str(workspace),"--run-id","EXT-1","--assignment",str(vap),"--result",str(vrp))
        approval={"schema_version":"question-space-v4","approval_id":"APP-EXT-FIXTURE","run_id":"EXT-1","input_snapshot":initialized["input_snapshot"],"vector_result_id":"EXT-VECTOR","vector_sha256":vector_accept["content_hash"],"vector_revision":"1","decision":"APPROVE","approval_mode":"SIMULATED_TEST","approver_id":"ROOT-AS-SIMULATED_TEST_OWNER","authority_basis":"bounded fixture authority","observation_provenance":"tests/test_revision_lifecycle.py:external-1","statement_path":"input/materials/001-statement.md","statement_sha256":qs.sha(statement.read_bytes()),"created_at":"2026-10-23T00:00:00Z","fixture_only":"YES"}
        approval_path=self.base/"external-approval.yaml"; put(approval_path,approval)
        approved=self.call("approve-vector","--root",str(workspace),"--run-id","EXT-1","--approval",str(approval_path))
        self.assertEqual(approved["status"],"VECTOR_APPROVED")
        self.assertEqual(self.call("resume","--root",str(workspace),"--run-id","EXT-1")["status"],"RESUMED")
        original=(run/binding["original_path"]).read_bytes(); (run/binding["original_path"]).write_text("tampered\n",encoding="utf-8")
        failed=self.call("recover","--root",str(workspace),"--run-id","EXT-1",ok=False)
        self.assertIn("immutable_external_bank_bytes_drift",failed["errors"][0])
        (run/binding["original_path"]).write_bytes(original)
        self.assertEqual(self.call("recover","--root",str(workspace),"--run-id","EXT-1")["status"],"RECOVERED")
        self.assertEqual(initialized["questions"],1)


if __name__ == "__main__":
    unittest.main()
