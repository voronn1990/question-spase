"""Deterministic structural tests for the local YAML controller only."""
from __future__ import annotations
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
QS = HERE / "scripts" / "qs.py"
spec = importlib.util.spec_from_file_location("qs_under_test", QS)
assert spec and spec.loader
qs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = qs
spec.loader.exec_module(qs)

def put(path: Path, value: object) -> None:
    path.write_bytes(qs.canon_yaml(value))

def source_swap(invocation, source, before, after):
    """Run a public entry point with an atomic post-capture fault."""
    script=r"""
from pathlib import Path
import sys

controller, source, before, after, *argv=sys.argv[1:]
sys.path.insert(0,str(Path(controller).parent))
import qs

source=Path(source)
capture=qs.captured_argument
swapped=False
def swap_after_capture(path,*args):
    global swapped
    observed=capture(path,*args)
    if Path(path)==source and not swapped:
        swapped=True
        changed=observed.raw.replace(bytes.fromhex(before),bytes.fromhex(after),1)
        assert changed!=observed.raw
        replacement=source.with_suffix(".swap")
        replacement.write_bytes(changed)
        replacement.replace(source)
    return observed
qs.captured_argument=swap_after_capture
raise SystemExit(qs.main(argv))
"""
    return subprocess.run([sys.executable,"-B","-c",script,str(QS),str(source),before.hex(),after.hex(),*invocation],
                          capture_output=True,text=True,timeout=20)


class QsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name); self.workspace = self.base / "work"
        self.bank = self.base / "bank.yaml"
        put(self.bank, {"requirements": ["R-1"], "questions": [{"id": "Q-001", "text": "first", "source_offset": "1"}, {"id": "Q-002", "text": "second", "source_offset": "2"}, {"id": "Q-003", "text": "third", "source_offset": "3"}]})
        self.raw = self.base / "questions.md"; self.raw.write_text("# source\n", encoding="utf-8")
        self.init = self.call("init", "--root", str(self.workspace), "--run-id", "R1", "--bank", str(self.bank), "--original-bank", str(self.raw)); self.run = self.workspace / "runs" / "R1"
    def tearDown(self) -> None: self.tmp.cleanup()
    def call(self, *args: str, ok: bool = True) -> dict:
        p = subprocess.run([sys.executable, "-B", str(QS), *args], text=True, capture_output=True); output = p.stdout or p.stderr; data = json.loads(output)
        if ok: self.assertEqual(p.returncode, 0, output)
        else: self.assertNotEqual(p.returncode, 0, output)
        return data
    def common(self, result_id: str, role: str, agent: str) -> dict:
        return {"schema_version": "question-space-v4", "id": result_id, "run_id": self.run.name, "input_snapshot": self.init["input_snapshot"], "author_role": role, "agent_id": agent, "knowledge_status": "DERIVED", "workflow_status": "ACCEPTED", "source_refs": [], "related_ids": [], "revision": "1", "created_at": "2026-10-21T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z"}
    def register_and_accept(self, label: str, role: str, kind: str, output: str, result: dict, agent: str, root_assignment: bool = False) -> dict:
        assignment = {"assignment_id": "A-" + label, "attempt_id": "TRY-1", "role_id": role, "agent_id": agent, "input_snapshot": self.init["input_snapshot"], "artifact_type": kind, "output_path": output, "dependency_hashes": [], "result_id": result["id"]}
        if root_assignment:
            assignment.update({"selection_authority":"ROOT", "group_id":"1", "assigned_ids":{"questions":[],"requirements":[],"decisions":[],"evidence":[]}})
        if kind == "FRAMING":
            assignment.update({"assigned_ids": {}, "role_instruction_paths": ["references/roles/perspectives.md"], "allowed_reads": ["references/phases/01-intake.md"], "blind_handoff": {"handoff_id": "HANDOFF-" + label, "assignment_id": assignment["assignment_id"], "attempt_id": assignment["attempt_id"], "role_id": role, "input_snapshot": self.init["input_snapshot"], "phase": "FRAMING", "objective": "independent framing without source access", "constraints": ["do not read source questions"]}})
        result.update({"assignment_id": assignment["assignment_id"], "attempt_id": assignment["attempt_id"], "artifact_type": kind})
        snapshot_doc=qs.load_yaml(self.run / "input" / "snapshot.yaml")
        if isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in qs.NEW_MODES:
            current=qs.load_yaml(self.run / "state" / "current.yaml")
            admission_hash=current.get("prep_admission_sha256")
            if not isinstance(admission_hash,str): raise AssertionError("new-mode test result requires admission")
            assignment.update({"lifecycle_scope":"CORE","prep_admission_sha256":admission_hash})
            result.update({"lifecycle_scope":"CORE","prep_admission_sha256":admission_hash})
        ap = self.base / (label + ".assignment.yaml"); rp = self.base / (label + ".result.yaml"); put(ap, assignment); put(rp, result)
        self.call("assign", "--root", str(self.workspace), "--run-id", self.run.name, "--assignment", str(ap))
        if isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in qs.NEW_MODES:
            self.call("context", "--root", str(self.workspace), "--run-id", self.run.name, "--assignment", str(ap), "--output", str(self.base / (label + ".context.yaml")))
        return self.call("accept", "--root", str(self.workspace), "--run-id", self.run.name, "--assignment", str(ap), "--result", str(rp))
    def test_snapshot_binds_raw_parsed_and_requirements(self) -> None:
        self.assertEqual(self.init["scope"], "STRUCTURAL_ONLY"); descriptor = self.run / "input" / "snapshot.yaml"; data = qs.load_yaml(descriptor)
        self.assertEqual(qs.sha(descriptor.read_bytes()), self.init["input_snapshot"]); self.assertEqual(data["original_bank"]["provided"], "true")
        (self.run / "input" / "original-bank.md").write_text("changed", encoding="utf-8")
        self.call("recover", "--root", str(self.workspace), "--run-id", "R1", ok=False)
        other_raw = self.base / "other.md"; other_raw.write_text("different source", encoding="utf-8")
        other = self.call("init", "--root", str(self.base / "other-work"), "--run-id", "R2", "--bank", str(self.bank), "--original-bank", str(other_raw))
        self.assertNotEqual(other["input_snapshot"], self.init["input_snapshot"])
    def test_assign_accept_recover_and_native_agent_id(self) -> None:
        result = self.common("POS-001", "P-01", "/root/v4_tools_resume")
        accepted = self.register_and_accept("p01", "P-01", "PRIMARY", "positions/POS-001.yaml", result, "/root/v4_tools_resume")
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual(self.call("recover", "--root", str(self.workspace), "--run-id", "R1")["accepted_result_ids"], ["POS-001"])
        result["revision"] = "2"; rp = self.base / "changed.yaml"; put(rp, result); ap = self.base / "p01.assignment.yaml"
        self.call("accept", "--root", str(self.workspace), "--run-id", "R1", "--assignment", str(ap), "--result", str(rp), ok=False)
    def test_assignment_with_mixed_run_snapshot_is_rejected(self) -> None:
        assignment={"assignment_id":"A-mixed","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/p01","input_snapshot":"0" * 64,"artifact_type":"PRIMARY","output_path":"positions/MIXED.yaml","dependency_hashes":[],"result_id":"MIXED"}
        path=self.base/"mixed.assignment.yaml"; put(path,assignment)
        failed=self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(path),ok=False)
        self.assertEqual(failed["error"],"assignment_snapshot_or_dependencies")
    def test_recover_reports_event_loss_and_orphan(self) -> None:
        self.register_and_accept("p02", "P-02", "PRIMARY", "positions/POS-002.yaml", self.common("POS-002", "P-02", "/root/p02"), "/root/p02")
        put(self.run / "answers" / "orphan.yaml", {"x": "orphan"})
        put(self.run / "state" / "events.yaml", {"schema_version": "question-space-v4", "run_id": "R1", "events": []})
        got = self.call("recover", "--root", str(self.workspace), "--run-id", "R1", ok=False)
        self.assertIn("answers/orphan.yaml", got["orphan_results"])
    def test_recover_replays_index_and_events_from_commit(self) -> None:
        self.register_and_accept("p03", "P-03", "PRIMARY", "positions/POS-003.yaml", self.common("POS-003", "P-03", "/root/p03"), "/root/p03")
        put(self.run / "state" / "assignments.yaml", {"schema_version":"question-space-v4","run_id":"R1","input_snapshot":self.init["input_snapshot"],"results":{},"attempts":{},"registered":{}})
        put(self.run / "state" / "events.yaml", {"schema_version":"question-space-v4","run_id":"R1","events":[]})
        got=self.call("recover", "--root", str(self.workspace), "--run-id", "R1")
        self.assertTrue(got["replayed_index"]); self.assertEqual(got["accepted_result_ids"],["POS-003"])
    def test_recover_preserves_pending_rejects_intent_tamper_and_redelivers_commit(self) -> None:
        pending = {"assignment_id":"A-pending","attempt_id":"TRY-1","role_id":"P-04","agent_id":"/root/p04","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/PENDING.yaml","dependency_hashes":[],"result_id":"PENDING"}
        pending_path=self.base/"pending.assignment.yaml"; put(pending_path,pending)
        self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(pending_path))
        result=self.common("POS-004","P-04","/root/p04")
        accepted=self.register_and_accept("p04","P-04","PRIMARY","positions/POS-004.yaml",result,"/root/p04")
        # Simulate crashes after the immutable commit and after mutable indexes/events.
        put(self.run/"state"/"assignments.yaml",{"schema_version":"question-space-v4","run_id":"R1","input_snapshot":self.init["input_snapshot"],"results":{},"attempts":{},"registered":{}})
        put(self.run/"state"/"events.yaml",{"schema_version":"question-space-v4","run_id":"R1","events":[]})
        # Exact duplicate delivery invokes durable replay rather than rerunning
        # accepted work when its mutable registry vanished.
        self.assertEqual(self.call("accept","--root",str(self.workspace),"--run-id","R1","--assignment",str(self.base/"p04.assignment.yaml"),"--result",str(self.base/"p04.result.yaml"))["status"],"IDEMPOTENT")
        recovered=self.call("recover","--root",str(self.workspace),"--run-id","R1")
        self.assertEqual(recovered["accepted_result_ids"],["POS-004"]); self.assertEqual(recovered["pending_assignments"],["A-pending"])
        intent=next((self.run/"intents").glob("*.yaml")); tampered=qs.load_yaml(intent); tampered["assignment"]["role_id"]="P-09"; put(intent,tampered)
        self.call("recover","--root",str(self.workspace),"--run-id","R1",ok=False)
    def test_recover_rejects_tampered_commit_bytes(self) -> None:
        self.register_and_accept("p05", "P-05", "PRIMARY", "positions/POS-005.yaml", self.common("POS-005", "P-05", "/root/p05"), "/root/p05")
        tx=self.run/"transactions/POS-005.yaml"; corrupted=qs.load_yaml(tx); corrupted["result_bytes_b64"]="QQ=="; put(tx,corrupted)
        failed=self.call("recover","--root",str(self.workspace),"--run-id","R1",ok=False)
        self.assertIn("invalid_transactions",failed["errors"])
    def test_source_outcomes_are_accepted_knowledge_not_bank_mutation(self) -> None:
        primary = self.common("PRIMARY-OUT-001", "P-01", "/root/p01")
        primary["addressed_q_ids"] = ["Q-001", "Q-002", "Q-003", "DQ-001"]
        self.register_and_accept("PRIMARY-OUT", "P-01", "PRIMARY", "positions/PRIMARY-OUT-001.yaml", primary, "/root/p01")
        result = self.common("OUT-001", "P-01", "/root/p01")
        result["records"] = [{"knowledge_state": "DERIVED", "record_kind": "SOURCE_OUTCOME", "original_q_ids": ["Q-001"], "source_disposition": "KEPT", "answer_status": "ANSWERED", "author_assignment_id": "A-PRIMARY-OUT", "answer_result_id": "PRIMARY-OUT-001", "evidence_ids": []}, {"knowledge_state": "DERIVED", "record_kind": "SOURCE_OUTCOME", "original_q_ids": ["Q-002", "Q-003"], "source_disposition": "MERGED", "disposition_target_ids": ["DQ-001"], "answer_status": "ANSWERED", "author_assignment_id": "A-PRIMARY-OUT", "answer_result_id": "PRIMARY-OUT-001", "evidence_ids": []}, {"knowledge_state": "DERIVED", "record_kind": "DERIVED_Q", "q_id": "DQ-001", "parent_source_ids": ["Q-002", "Q-003"], "answer_status": "ANSWERED", "author_assignment_id": "A-PRIMARY-OUT", "answer_result_id": "PRIMARY-OUT-001", "evidence_ids": []}]
        accepted = self.register_and_accept("OUT", "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT-001.yaml", result, "/root/p01")
        self.assertTrue((self.run / "answers" / "source-outcomes" / "OUT-001.yaml").is_file())
        self.assertEqual(accepted["current_selection"]["result_id"], "OUT-001")
        self.assertNotIn("source_outcomes", qs.load_yaml(self.run / "input" / "bank.yaml"))
    def test_commit_context_and_resume_are_bounded_and_addressable(self) -> None:
        vector=self.common("VECTOR-001","OP-VECTOR","/root/vector"); vector.update({"G":"addressable goal","NOT_G":["formal pass without benefit"]})
        self.register_and_accept("vector","OP-VECTOR","OPERATOR","operators/VECTOR-001.yaml",vector,"/root/vector")
        evidence=self.common("E-001","P-01","/root/evidence"); evidence["changed"]="UNCHANGED"
        self.register_and_accept("evidence","P-01","EVIDENCE","evidence/E-001.yaml",evidence,"/root/evidence")
        registry=qs.load_yaml(self.run/"state/assignments.yaml")["results"]
        primary=self.common("COMMIT-001","P-03","/root/p03")
        commit_assignment={"assignment_id":"A-commit","attempt_id":"TRY-1","role_id":"P-03","agent_id":"/root/p03","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/COMMIT-001.yaml","dependency_hashes":[],"result_id":"COMMIT-001"}
        primary.update({"assignment_id":"A-commit","attempt_id":"TRY-1","artifact_type":"PRIMARY"})
        ap=self.base/"commit.assignment.yaml"; rp=self.base/"commit.result.yaml"; put(ap,commit_assignment); put(rp,primary)
        self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(ap))
        self.assertEqual(self.call("commit","--root",str(self.workspace),"--run-id","R1","--assignment",str(ap),"--result",str(rp))["status"],"ACCEPTED")
        context_assignment={"assignment_id":"A-context","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/p01","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/CONTEXT-001.yaml","dependency_hashes":[registry["E-001"]["content_hash"]],"dependency_result_ids":["E-001"],"result_id":"CONTEXT-001","group_id":"1","selection_authority":"ROOT","goal_result_id":"VECTOR-001","assigned_ids":{"questions":["Q-001"],"requirements":["R-1"],"decisions":[],"evidence":[]},"allowed_reads":["input/snapshot.yaml","references/invariants.md"]}
        cap=self.base/"context.assignment.yaml"; put(cap,context_assignment)
        self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(cap))
        packet=self.base/"context.yaml"; built=self.call("context","--root",str(self.workspace),"--run-id","R1","--assignment",str(cap),"--output",str(packet))
        packet_data=qs.load_yaml(packet); self.assertEqual(built["status"],"CONTEXT_READY"); self.assertEqual(packet_data["questions"][0]["id"],"Q-001"); self.assertEqual(packet_data["goal"]["G"],"addressable goal"); self.assertEqual(len(packet_data["readset"]),2)
        blind={"assignment_id":"A-blind","attempt_id":"TRY-1","role_id":"P-02","agent_id":"/root/p02","input_snapshot":self.init["input_snapshot"],"artifact_type":"FRAMING","output_path":"positions/FRAME-002.yaml","dependency_hashes":[],"result_id":"FRAME-002","group_id":"2","selection_authority":"ROOT","goal_result_id":"VECTOR-001","assigned_ids":{},"role_instruction_paths":["references/roles/perspectives.md"],"allowed_reads":["references/phases/01-intake.md"],"blind_handoff":{"handoff_id":"HANDOFF-P02","assignment_id":"A-blind","attempt_id":"TRY-1","role_id":"P-02","input_snapshot":self.init["input_snapshot"],"phase":"FRAMING","objective":"identify broad assumptions before source disclosure","constraints":["do not read the question bank","record uncertainty"]}}
        bap=self.base/"blind.assignment.yaml"; put(bap,blind); self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(bap))
        blind_packet=self.base/"blind-context.yaml"; self.call("context","--root",str(self.workspace),"--run-id","R1","--assignment",str(bap),"--output",str(blind_packet))
        blind_data=qs.load_yaml(blind_packet); self.assertNotIn("questions",blind_data); self.assertNotIn("Q-002",blind_packet.read_text(encoding="utf-8")); self.assertEqual(blind_data["blind_handoff"]["document"]["handoff_id"],"HANDOFF-P02")
        waiting={"assignment_id":"A-wait","attempt_id":"TRY-1","role_id":"P-04","agent_id":"/root/p04","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/WAIT.yaml","dependency_hashes":["f"*64],"result_id":"WAIT","group_id":"3"}
        unknown={"assignment_id":"A-unknown","attempt_id":"TRY-1","role_id":"P-05","agent_id":"/root/p05","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/UNKNOWN.yaml","dependency_hashes":[],"result_id":"UNKNOWN","group_id":"3","launch_state":"DISPATCH_UNKNOWN"}
        for name,assignment in (("wait",waiting),("unknown",unknown)):
            path=self.base/(name+".assignment.yaml"); put(path,assignment); self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(path))
        stale_doc=self.common("S-STALE","OP-SYNTHESIS","/root/synthesis"); stale_doc["dependency_edges"]=[{"from_id":"E-001","to_id":"S-STALE"}]
        self.register_and_accept("stale","OP-SYNTHESIS","SYNTHESIS","synthesis/S-STALE.yaml",stale_doc,"/root/synthesis")
        resumed=self.call("resume","--root",str(self.workspace),"--run-id","R1","--changed","E-001")
        statuses={item["assignment_id"]:item["status"] for item in resumed["tasks"]}
        self.assertEqual(resumed["routing_status"],"NOT_CONFIGURED"); self.assertEqual(statuses["A-context"],"NOT_LAUNCHED"); self.assertEqual(statuses["A-wait"],"WAITING"); self.assertEqual(statuses["A-unknown"],"DISPATCH_UNKNOWN"); self.assertEqual(statuses["A-stale"],"STALE"); self.assertIn("S-STALE",resumed["stale_result_ids"]); self.assertEqual(resumed["metrics"]["tokens"],"UNAVAILABLE")
        checked=self.call("validate","--root",str(self.workspace),"--run-id","R1","--phase","core",ok=False)
        self.assertTrue(any(error.startswith("stale_result_ids:") for error in checked["errors"]))
    def test_launch_intent_journal_survives_index_replay(self) -> None:
        assignment={"assignment_id":"A-launch","attempt_id":"TRY-1","role_id":"P-05","agent_id":"/root/p05","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/LAUNCH.yaml","dependency_hashes":[],"result_id":"LAUNCH"}
        path=self.base/"launch.assignment.yaml"; put(path,assignment)
        self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(path))
        marked=self.call("launch-intent","--root",str(self.workspace),"--run-id","R1","--assignment-id","A-launch","--attempt-id","TRY-1","--state","READY_TO_DISPATCH")
        self.assertEqual(marked["status"],"LAUNCH_INTENT_RECORDED")
        marked=self.call("launch-intent","--root",str(self.workspace),"--run-id","R1","--assignment-id","A-launch","--attempt-id","TRY-1","--state","DISPATCH_UNKNOWN")
        self.assertEqual(marked["launch_state"],"DISPATCH_UNKNOWN")
        index=qs.load_yaml(self.run/"state/assignments.yaml"); index["registered"]["A-launch\0TRY-1"]["launch_state"]="NOT_LAUNCHED"; put(self.run/"state/assignments.yaml",index)
        recovered=self.call("recover","--root",str(self.workspace),"--run-id","R1")
        self.assertEqual(recovered["status"],"RECOVERED")
        resumed=self.call("resume","--root",str(self.workspace),"--run-id","R1")
        states={row["assignment_id"]:row["status"] for row in resumed["tasks"]}
        self.assertEqual(states["A-launch"],"DISPATCH_UNKNOWN")
        launches=qs.load_yaml(self.run/"state/launch-intents.yaml")
        launches["records"]["A-launch\0TRY-1"]["assignment_sha256"]="forged"; put(self.run/"state/launch-intents.yaml",launches)
        self.call("recover","--root",str(self.workspace),"--run-id","R1",ok=False)

    def test_blind_context_rejects_bank_foreign_dependencies_and_expand(self) -> None:
        vector=self.common("VECTOR-BLIND","OP-VECTOR","/root/vector"); vector.update({"G":"blind objective","NOT_G":["source leakage"]})
        self.register_and_accept("vector-blind","OP-VECTOR","OPERATOR","operators/VECTOR-BLIND.yaml",vector,"/root/vector")
        foreign=self.common("FOREIGN-BLIND","P-03","/root/p03")
        foreign_entry=self.register_and_accept("foreign-blind","P-03","PRIMARY","positions/FOREIGN-BLIND.yaml",foreign,"/root/p03")
        base={"assignment_id":"A-blind-safe","attempt_id":"TRY-1","role_id":"P-02","agent_id":"/root/p02","input_snapshot":self.init["input_snapshot"],"artifact_type":"FRAMING","output_path":"positions/FRAME-SAFE.yaml","dependency_hashes":[],"result_id":"FRAME-SAFE","goal_result_id":"VECTOR-BLIND","assigned_ids":{},"role_instruction_paths":["references/roles/perspectives.md"],"allowed_reads":["references/phases/01-intake.md","references/invariants.md"],"blind_handoff":{"handoff_id":"HANDOFF-SAFE","assignment_id":"A-blind-safe","attempt_id":"TRY-1","role_id":"P-02","input_snapshot":self.init["input_snapshot"],"phase":"FRAMING","objective":"state broad causal alternatives","constraints":["no source questions"]}}
        for label, mutate in (("bank",lambda item:item.update({"allowed_reads":["input/bank.yaml"]})),("foreign",lambda item:item.update({"dependency_hashes":[foreign_entry["content_hash"]],"dependency_result_ids":["FOREIGN-BLIND"]})),("question",lambda item:item.update({"assigned_ids":{"questions":["Q-001"]}}))):
            import copy
            bad=copy.deepcopy(base); mutate(bad); path=self.base/("blind-"+label+".yaml"); put(path,bad)
            self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(path),ok=False)
        safe=self.base/"blind-safe.yaml"; put(safe,base); self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(safe))
        packet=self.base/"blind-safe-context.yaml"; made=self.call("context","--root",str(self.workspace),"--run-id","R1","--assignment",str(safe),"--output",str(packet))
        self.assertEqual(made["blind"],"true"); content=packet.read_text(encoding="utf-8"); self.assertNotIn("Q-001",content); self.assertNotIn("FOREIGN-BLIND",content)
        self.call("context","--root",str(self.workspace),"--run-id","R1","--assignment",str(safe),"--output",str(packet),"--expand","FOREIGN-BLIND",ok=False)

    def test_obligation_coverage_returns_exact_unclosed_ids(self) -> None:
        # The answer body mentions Q-002, so candidate intake can bind the
        # SOURCE_OUTCOME to exact accepted bytes.  Its immutable assignment
        # authorizes only Q-001; full validation must therefore keep Q-002
        # unclosed and preserve the original provenance-negative assertion.
        answer=self.common("ANSWER-Q1","P-01","/root/p01"); answer.update({"assignment_id":"A-answer-q1","attempt_id":"TRY-1","artifact_type":"PRIMARY","addressed_q_ids":["Q-001","Q-002"]})
        answer_assignment={"assignment_id":"A-answer-q1","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/p01","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/ANSWER-Q1.yaml","dependency_hashes":[],"result_id":"ANSWER-Q1","selection_authority":"ROOT","group_id":"1","assigned_ids":{"questions":["Q-001"],"requirements":[],"decisions":[],"evidence":[]}}
        answer_assignment_path=self.base/"answer-q1.assignment.yaml"; answer_result_path=self.base/"answer-q1.result.yaml"; put(answer_assignment_path,answer_assignment); put(answer_result_path,answer)
        self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(answer_assignment_path))
        self.call("accept","--root",str(self.workspace),"--run-id","R1","--assignment",str(answer_assignment_path),"--result",str(answer_result_path))
        outcomes=self.common("OUTCOME-001","P-01","/root/p01")
        outcomes.update({"records":[
            {"knowledge_state": "DERIVED", "record_kind":"SOURCE_OUTCOME","original_q_ids":["Q-001"],"source_disposition":"KEPT","answer_status":"ANSWERED","author_assignment_id":"A-answer-q1","answer_result_id":"ANSWER-Q1","evidence_ids":[]},
            {"knowledge_state": "DERIVED", "record_kind":"SOURCE_OUTCOME","original_q_ids":["Q-002"],"source_disposition":"MERGED","disposition_target_ids":[],"answer_status":"ANSWERED","author_assignment_id":"A-answer-q1","answer_result_id":"ANSWER-Q1","evidence_ids":[]},
        ]})
        self.register_and_accept("outcomes","P-01","SOURCE_OUTCOMES","answers/source-outcomes.yaml",outcomes,"/root/p01")
        checked=self.call("validate","--root",str(self.workspace),"--run-id","R1","--phase","core",ok=False)
        self.assertEqual(checked["unclosed_q_ids"],["Q-002","Q-003"])
        self.assertEqual(checked["unclosed_r_ids"],["R-1"])
        self.assertIn("derived_trace",checked["errors"])
    def test_resume_binds_root_approved_orchestration_map(self) -> None:
        def accept_route(label: str, assignment: dict, document: dict) -> dict:
            document.update({"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"artifact_type":assignment["artifact_type"]})
            ap=self.base/(label+".assignment.yaml"); rp=self.base/(label+".result.yaml"); put(ap,assignment); put(rp,document)
            self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(ap))
            return self.call("commit","--root",str(self.workspace),"--run-id","R1","--assignment",str(ap),"--result",str(rp))
        empty={"questions":[],"requirements":[],"decisions":[],"evidence":[]}
        evidence_assignment={"assignment_id":"ASG-E","attempt_id":"TRY-1","role_id":"P-04","agent_id":"/root/p04","input_snapshot":self.init["input_snapshot"],"artifact_type":"EVIDENCE","output_path":"evidence/E-RES.yaml","dependency_hashes":[],"dependency_result_ids":[],"result_id":"E-RES","group_id":"1","selection_authority":"ROOT","assigned_ids":empty,"launch_state":"READY_TO_DISPATCH"}
        evidence=self.common("E-RES","P-04","/root/p04"); evidence.update({"tool_id":"TOOL-1","tool_version":"1","policy_hash":"POLICY","exposure_hash":"EXPOSURE","freshness":"CURRENT-AS-DECLARED","changed":"UNCHANGED"})
        accepted_e=accept_route("route-e",evidence_assignment,evidence)
        primary_assignment={"assignment_id":"ASG-Q","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/p01","input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/Q-RES.yaml","dependency_hashes":[accepted_e["content_hash"]],"dependency_result_ids":["E-RES"],"result_id":"Q-RES","group_id":"1","selection_authority":"ROOT","assigned_ids":{"questions":["Q-001"],"requirements":["R-1"],"decisions":[],"evidence":["E-RES"]},"launch_state":"READY_TO_DISPATCH","evidence_applicability":[{"evidence_id":"E-RES","content_hash":accepted_e["content_hash"],"input_snapshot":self.init["input_snapshot"],"tool_id":"TOOL-1","tool_version":"1","policy_hash":"POLICY","exposure_hash":"EXPOSURE","freshness":"CURRENT-AS-DECLARED","consumer_ids":["Q-001"]}]}
        primary=self.common("Q-RES","P-01","/root/p01"); primary["addressed_q_ids"]=["Q-001"]
        accept_route("route-q",primary_assignment,primary)
        approved={"input_snapshot":self.init["input_snapshot"],"allowed_roles":sorted(qs.ROLES),"role_competence":{role:(["analysis"] if role=="P-01" else []) for role in qs.ROLES},"question_routes":{"Q-001":{"primary_assignment_id":"ASG-Q","required_tags":["analysis"]},"Q-002":{"primary_assignment_id":"ASG-Q2","required_tags":[]},"Q-003":{"primary_assignment_id":"ASG-Q3","required_tags":[]}},"critical_reviews":{},"evidence_applicability":{"E-RES":["Q-001"]}}
        # This bank has Q-002/Q-003 too, so a map which omits their primary
        # assignments must be rejected as addressable, rather than routed by a
        # heuristic. A complete root map will be accepted on a one-Q run below.
        bad=self.base/"bad-approved.yaml"; put(bad,approved)
        rejected=self.call("resume","--root",str(self.workspace),"--run-id","R1","--approved-map",str(bad),ok=False)
        self.assertIn("orchestration_invalid",rejected["error"])
        self.assertFalse((self.run/"state/approved-map.yaml").exists())
        one_bank=self.base/"one-bank.yaml"; put(one_bank,{"requirements":["R-1"],"questions":[{"id":"Q-001","text":"one","source_offset":"1"}]})
        one_raw=self.base/"one-source.md"; one_raw.write_text("one",encoding="utf-8")
        one_workspace=self.base/"one-work"; one_init=self.call("init","--root",str(one_workspace),"--run-id","ONE","--bank",str(one_bank),"--original-bank",str(one_raw))
        def one_doc(result_id: str, role: str, agent: str) -> dict:
            return {"schema_version":"question-space-v4","id":result_id,"run_id":"ONE","input_snapshot":one_init["input_snapshot"],"author_role":role,"agent_id":agent,"knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z"}
        def one_accept(label: str, assignment: dict, document: dict) -> dict:
            document.update({"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"artifact_type":assignment["artifact_type"]})
            ap=self.base/("one-"+label+".assignment.yaml"); rp=self.base/("one-"+label+".result.yaml"); put(ap,assignment); put(rp,document)
            self.call("assign","--root",str(one_workspace),"--run-id","ONE","--assignment",str(ap))
            return self.call("commit","--root",str(one_workspace),"--run-id","ONE","--assignment",str(ap),"--result",str(rp))
        one_empty={"questions":[],"requirements":[],"decisions":[],"evidence":[]}
        one_ea={"assignment_id":"ONE-E","attempt_id":"TRY-1","role_id":"P-04","agent_id":"/root/p04","input_snapshot":one_init["input_snapshot"],"artifact_type":"EVIDENCE","output_path":"evidence/ONE-E.yaml","dependency_hashes":[],"dependency_result_ids":[],"result_id":"ONE-E","group_id":"1","selection_authority":"ROOT","assigned_ids":one_empty,"launch_state":"READY_TO_DISPATCH"}
        one_e=one_doc("ONE-E","P-04","/root/p04"); one_e.update({"tool_id":"TOOL-1","tool_version":"1","policy_hash":"POLICY","exposure_hash":"EXPOSURE","freshness":"CURRENT-AS-DECLARED","changed":"UNCHANGED"})
        one_eaccepted=one_accept("e",one_ea,one_e)
        one_qa={"assignment_id":"ONE-Q","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/p01","input_snapshot":one_init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/ONE-Q.yaml","dependency_hashes":[one_eaccepted["content_hash"]],"dependency_result_ids":["ONE-E"],"result_id":"ONE-Q","group_id":"1","selection_authority":"ROOT","assigned_ids":{"questions":["Q-001"],"requirements":["R-1"],"decisions":[],"evidence":["ONE-E"]},"launch_state":"READY_TO_DISPATCH","evidence_applicability":[{"evidence_id":"ONE-E","content_hash":one_eaccepted["content_hash"],"input_snapshot":one_init["input_snapshot"],"tool_id":"TOOL-1","tool_version":"1","policy_hash":"POLICY","exposure_hash":"EXPOSURE","freshness":"CURRENT-AS-DECLARED","consumer_ids":["Q-001"]}]}
        one_q=one_doc("ONE-Q","P-01","/root/p01"); one_q["addressed_q_ids"]=["Q-001"]; one_accept("q",one_qa,one_q)
        one_map={"input_snapshot":one_init["input_snapshot"],"allowed_roles":sorted(qs.ROLES),"role_competence":{role:(["analysis"] if role=="P-01" else []) for role in qs.ROLES},"question_routes":{"Q-001":{"primary_assignment_id":"ONE-Q","required_tags":["analysis"]}},"critical_reviews":{},"evidence_applicability":{"ONE-E":["Q-001"]}}
        one_map_path=self.base/"one-approved.yaml"; put(one_map_path,one_map)
        routed=self.call("resume","--root",str(one_workspace),"--run-id","ONE","--approved-map",str(one_map_path))
        self.assertEqual(routed["status"],"RESUMED"); self.assertEqual(routed["orchestration"]["errors"],[]); self.assertTrue((one_workspace/"runs/ONE/state/approved-map.yaml").is_file())
        first_map_sha=qs.sha((one_workspace/"runs/ONE/state/approved-map.yaml").read_bytes())
        revised=dict(one_map); revised.update({"version":"2","supersedes_sha256":first_map_sha})
        revised_path=self.base/"one-approved-v2.yaml"; put(revised_path,revised)
        revised_result=self.call("resume","--root",str(one_workspace),"--run-id","ONE","--approved-map",str(revised_path))
        self.assertEqual(revised_result["routing_status"],"VALIDATED")
        self.assertTrue((one_workspace/"runs/ONE/state/approved-maps"/(first_map_sha+".yaml")).is_file())
        stored_map=one_workspace/"runs/ONE/state/approved-map.yaml"
        current_map_sha=qs.sha(stored_map.read_bytes())
        resumed_from_stored=self.call("resume","--root",str(one_workspace),"--run-id","ONE")
        self.assertEqual(resumed_from_stored["routing_status"],"VALIDATED")
        self.assertEqual(resumed_from_stored["orchestration"]["errors"],[])
        self.assertEqual(qs.sha(stored_map.read_bytes()),current_map_sha)
        normal=self.call("validate","--root",str(one_workspace),"--run-id","ONE","--phase","core",ok=False)
        self.assertFalse(any(error.startswith("approved_map:") for error in normal["errors"]))
        stored_map.write_text("forged",encoding="utf-8")
        drifted_resume=self.call("resume","--root",str(one_workspace),"--run-id","ONE",ok=False)
        self.assertEqual(drifted_resume["error"],"approved_map_drift")
        drift=self.call("validate","--root",str(one_workspace),"--run-id","ONE","--phase","core",ok=False)
        self.assertIn("approved_map:approved_map_drift",drift["errors"])
    def test_finalize_does_not_exclude_unrelated_review_path(self) -> None:
        """A valid-looking GATE-6 cannot hide a review of another result."""
        audit_path=self.run/"audit/final.yaml"; review_path=self.run/"reviews/unrelated.yaml"
        put(audit_path,{"id":"AUDIT-001"}); put(review_path,{"target_result_id":"OTHER-001","target_hash":"x"})
        snapshot=self.init["input_snapshot"]
        state={"results":{
            "AUDIT-001":{"result_id":"AUDIT-001","artifact_type":"FINAL_AUDIT","path":"audit/final.yaml","content_hash":qs.sha(audit_path.read_bytes())},
            "REVIEW-OTHER":{"result_id":"REVIEW-OTHER","artifact_type":"REVIEW","path":"reviews/unrelated.yaml","content_hash":qs.sha(review_path.read_bytes())},
        }}
        put(self.run/"gates/GATE-6.yaml",{"input_snapshot":snapshot,"content_review":{"result_id":"REVIEW-OTHER","sha256":state["results"]["REVIEW-OTHER"]["content_hash"]},"evidence_refs":[{"result_id":"AUDIT-001","sha256":state["results"]["AUDIT-001"]["content_hash"]}]})
        self.assertIsNone(qs.administrative_review_binding(self.run,state,{},snapshot))
        self.assertIn(review_path,qs.core_files(self.run,state,snapshot))
    def test_strict_parser_and_large_banks_are_structural_only(self) -> None:
        bad = self.base / "bad.yaml"; bad.write_text("x: 12\n", encoding="utf-8")
        with self.assertRaises(qs.QSError): qs.load_yaml(bad)
        for count in (230, 300):
            bank = self.base / ("bank-" + str(count) + ".yaml"); put(bank, {"requirements": ["R-1"], "questions": [{"id": "Q-" + str(i), "text": "q", "source_offset": str(i)} for i in range(count)]})
            raw = self.base / ("source-" + str(count) + ".md"); raw.write_text("source", encoding="utf-8")
            out = self.call("init", "--root", str(self.base / ("w" + str(count))), "--run-id", "R" + str(count), "--bank", str(bank), "--original-bank", str(raw)); self.assertEqual((out["questions"], out["scope"]), (count, "STRUCTURAL_ONLY"))
    def test_current_plan_projection_selects_one_revision_chain(self) -> None:
        """Retained plan history needs an explicit hash-bound logical projection."""
        def entry(result_id: str, assignment_id: str, attempt_id: str, content: str) -> dict:
            return {"result_id": result_id, "artifact_type": "PLAN", "content_hash": content,
                    "assignment_id": assignment_id, "attempt_id": attempt_id}
        results = {
            "S-000": entry("S-000", "PLAN-S-000", "ATT-1", "a" * 64),
            "S-000-R2": entry("S-000-R2", "PLAN-S-000", "ATT-2", "b" * 64),
            "S-001": entry("S-001", "PLAN-S-001", "ATT-1", "c" * 64),
            "S-001-R2": entry("S-001-R2", "PLAN-S-001", "ATT-2", "d" * 64),
        }
        docs = {
            "S-000": {"id": "S-000", "plan_version": "1", "identity_result": {"stage_id": "S-000"}},
            "S-001": {"id": "S-001", "plan_version": "1", "identity_result": {"stage_id": "S-001"}},
            "S-000-R2": {"id": "S-000-R2", "plan_version": "2", "identity_result": {"stage_id": "S-000"}, "supersedes": "S-000"},
            "S-001-R2": {"id": "S-001-R2", "plan_version": "2", "identity_result": {"stage_id": "S-001"}, "supersedes": "S-001"},
        }
        registered = {}
        for result_id, item in results.items():
            pair = item["assignment_id"] + "\0" + item["attempt_id"]
            registered[pair] = {"assignment": {"result_id": result_id}}
        state = {"results": results, "registered": registered,
                 "current_attempts": {"PLAN-S-000": "ATT-2", "PLAN-S-001": "ATT-2"}}
        with self.assertRaisesRegex(qs.QSError, "plan_projection_required"):
            qs.resolve_current_plan_projection(state, {}, docs)
        projection = {"plan_version": "2", "stages": [
            {"logical_stage_id": "S-000", "result_id": "S-000-R2", "sha256": "b" * 64, "assignment_id": "PLAN-S-000", "attempt_id": "ATT-2"},
            {"logical_stage_id": "S-001", "result_id": "S-001-R2", "sha256": "d" * 64, "assignment_id": "PLAN-S-001", "attempt_id": "ATT-2"},
        ]}
        mapping, resolved = qs.resolve_current_plan_projection(state, {}, docs, projection)
        self.assertEqual(mapping, {"S-000-R2": "S-000", "S-001-R2": "S-001"})
        self.assertEqual(resolved, projection)
        stale_current = {"stale_result_ids": ["S-000-R2"]}
        with self.assertRaisesRegex(qs.QSError, "plan_projection_stale"):
            qs.resolve_current_plan_projection(state, stale_current, docs, projection)
        stale_attempts = dict(state); stale_attempts["current_attempts"] = dict(state["current_attempts"]); stale_attempts["current_attempts"]["PLAN-S-000"] = "ATT-1"
        with self.assertRaisesRegex(qs.QSError, "plan_projection_current_attempt"):
            qs.resolve_current_plan_projection(stale_attempts, {}, docs, projection)
        docs["S-000-ALT"] = {"id": "S-000-ALT", "plan_version": "2", "identity_result": {"stage_id": "S-000"}, "supersedes": "S-000"}
        results["S-000-ALT"] = entry("S-000-ALT", "PLAN-S-000-ALT", "ATT-1", "e" * 64)
        with self.assertRaisesRegex(qs.QSError, "plan_projection_ambiguous_history"):
            qs.resolve_current_plan_projection(state, {}, docs, projection)

    def test_resume_persists_hash_bound_current_plan_projection_with_history(self) -> None:
        """A Root map can select v2 before recovery sees retained v1/v2 plans."""
        def accept_exact(label: str, assignment: dict, document: dict) -> dict:
            document.update({"assignment_id": assignment["assignment_id"], "attempt_id": assignment["attempt_id"], "artifact_type": assignment["artifact_type"]})
            assignment_path=self.base/(label+".assignment.yaml"); result_path=self.base/(label+".result.yaml")
            put(assignment_path,assignment); put(result_path,document)
            self.call("assign","--root",str(self.workspace),"--run-id","R1","--assignment",str(assignment_path))
            return self.call("accept","--root",str(self.workspace),"--run-id","R1","--assignment",str(assignment_path),"--result",str(result_path))
        empty={"questions":[],"requirements":[],"decisions":[],"evidence":[]}
        primary_assignments=[]
        for index in range(1,4):
            qid="Q-%03d" % index; assignment={"assignment_id":"ASG-"+qid,"attempt_id":"ATT-1","role_id":"P-%02d" % index,"agent_id":"/root/p"+str(index),"input_snapshot":self.init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/"+qid+".yaml","dependency_hashes":[],"result_id":"RESULT-"+qid,"selection_authority":"ROOT","group_id":"1","assigned_ids":{"questions":[qid],"requirements":["R-1"],"decisions":[],"evidence":[]}}
            doc=self.common("RESULT-"+qid,assignment["role_id"],assignment["agent_id"]); doc["addressed_q_ids"]=[qid]
            accept_exact("primary-"+qid,assignment,doc); primary_assignments.append(assignment)
        accepted={}
        for logical in ("S-000","S-001"):
            assignment={"assignment_id":"ASG-PLAN-"+logical,"attempt_id":"ATT-1","role_id":"PLAN-AUTHOR","agent_id":"/root/plan","input_snapshot":self.init["input_snapshot"],"artifact_type":"PLAN","output_path":"plan/"+logical+".yaml","dependency_hashes":[],"result_id":logical,"selection_authority":"ROOT","group_id":"1","assigned_ids":empty}
            entry=logical+"-ENTRY"; exit_id=logical+"-EXIT"
            doc=self.common(logical,"PLAN-AUTHOR","/root/plan"); doc.update({"plan_version":"1","identity_result":{"stage_id":logical},"tasks":[],"local_execution_graph":{"entry":entry,"exit":exit_id,"edges":[{"from":entry,"to":exit_id}],"predecessors":[],"next_stages":[]}})
            accepted[logical]=accept_exact("plan-"+logical,assignment,doc)
        for logical in ("S-000","S-001"):
            result_id=logical+"-R2"; assignment={"assignment_id":"ASG-PLAN-"+logical,"attempt_id":"ATT-2","supersedes_attempt_id":"ATT-1","role_id":"PLAN-AUTHOR","agent_id":"/root/plan","input_snapshot":self.init["input_snapshot"],"artifact_type":"PLAN","output_path":"plan/"+result_id+".yaml","dependency_hashes":[],"result_id":result_id,"selection_authority":"ROOT","group_id":"1","assigned_ids":empty}
            entry=logical+"-ENTRY"; exit_id=logical+"-EXIT"
            doc=self.common(result_id,"PLAN-AUTHOR","/root/plan"); doc.update({"plan_version":"2","identity_result":{"stage_id":logical},"tasks":[],"local_execution_graph":{"entry":entry,"exit":exit_id,"edges":[{"from":entry,"to":exit_id}],"predecessors":[],"next_stages":[]},"supersedes":logical,"revision":"2","related_ids":[logical]})
            accepted[result_id]=accept_exact("plan-"+result_id,assignment,doc)
        graph={"plan_version":"2","stages":[{"logical_stage_id":logical,"result_id":logical+"-R2","sha256":accepted[logical+"-R2"]["content_hash"],"assignment_id":"ASG-PLAN-"+logical,"attempt_id":"ATT-2"} for logical in ("S-000","S-001")]}
        approved={"input_snapshot":self.init["input_snapshot"],"allowed_roles":sorted(qs.ROLES),"role_competence":{role:(["analysis"] if role in {"P-01","P-02","P-03"} else []) for role in qs.ROLES},"question_routes":{assignment["assigned_ids"]["questions"][0]:{"primary_assignment_id":assignment["assignment_id"],"required_tags":["analysis"]} for assignment in primary_assignments},"critical_reviews":{},"evidence_applicability":{},"current_plan_graph":graph}
        map_path=self.base/"projection-map.yaml"; put(map_path,approved)
        resumed=self.call("resume","--root",str(self.workspace),"--run-id","R1","--approved-map",str(map_path))
        self.assertEqual(resumed["status"],"RESUMED")
        current=qs.load_yaml(self.run/"state/current.yaml")
        self.assertEqual(current["current_plan_projection"],graph); self.assertEqual(current["current_plan_projection_sha256"],qs.sha(qs.canon_yaml(graph)))
        self.assertEqual(self.call("recover","--root",str(self.workspace),"--run-id","R1")["status"],"RECOVERED")
        tampered=dict(current); tampered["current_plan_projection"]={"plan_version":"2","stages":[graph["stages"][0]]}; put(self.run/"state/current.yaml",tampered)
        state=qs.load_yaml(self.run/"state/assignments.yaml")
        with self.assertRaisesRegex(qs.QSError,"plan_projection_hash_drift"):
            qs.plan_projection_for_run(self.run,state,tampered)
        failed=self.call("recover","--root",str(self.workspace),"--run-id","R1",ok=False)
        self.assertIn("index_or_input_corrupt:current_replay_descendant_or_conflict",failed["errors"])

    def test_critical_string_enums_and_typed_closure_fail_closed(self) -> None:
        # A small assembled structural fixture proves quoted enum values reach the checks.
        roles = ["P-" + str(i).zfill(2) for i in range(1, 11)] + ["OP-VECTOR", "OP-PROJECTION", "OP-DIVERGENCE", "OP-REVERSE", "OP-INDULGENCE", "OP-SYNTHESIS", "PLAN-AUTHOR", "FINAL-AUDITOR"]
        for role in roles:
            kinds = ["FRAMING", "PRIMARY"] if role.startswith("P-") else ["SYNTHESIS"] if role == "OP-SYNTHESIS" else ["PLAN"] if role == "PLAN-AUTHOR" else ["FINAL_AUDIT"] if role == "FINAL-AUDITOR" else ["OPERATOR"]
            for kind in kinds:
                result_id = role.replace("-", "") + kind; output = "positions/" + result_id + ".yaml" if kind in {"FRAMING", "PRIMARY"} else "operators/" + result_id + ".yaml" if kind == "OPERATOR" else "synthesis/" + result_id + ".yaml" if kind == "SYNTHESIS" else "plan/" + result_id + ".yaml" if kind == "PLAN" else "audit/final.yaml"
                doc = self.common(result_id, role, "/root/" + role)
                if kind == "PRIMARY" and role == "P-01": doc["addressed_q_ids"] = ["Q-001", "Q-002", "Q-003"]
                if kind == "PLAN":
                    entry=result_id+"-ENTRY"; exit_id=result_id+"-EXIT"
                    doc.update({"plan_version":"1","identity_result":{"stage_id":result_id},"tasks":[],"local_execution_graph":{"entry":entry,"exit":exit_id,"edges":[{"from":entry,"to":exit_id}],"predecessors":[],"next_stages":[]}})
                if kind == "FINAL_AUDIT": doc.update({"areas": {str(i): "PASS" for i in range(1, 9)}, "verdict": "REPAIR_REQUIRED", "independence_mode": "INDEPENDENT", "last_fix_status": "CHECKED", "core_manifest_sha256": "later", "readiness": {x: "UNVERIFIED" for x in ("package", "runtime", "quality", "plan", "implementation", "effect")}})
                self.register_and_accept("r" + result_id, role, kind, output, doc, "/root/" + role, root_assignment=(kind == "PRIMARY" and role == "P-01"))
        for n in range(1, 7): put(self.run / "gates" / ("GATE-" + str(n) + ".yaml"), {"gate_id": "GATE-" + str(n), "input_snapshot": self.init["input_snapshot"], "required_objects": [], "completed_checks": [], "content_review": "done", "unresolved_critical": [], "decision": "PASS", "allowed_next": [], "blocked_dependencies": [], "evidence_refs": []})
        outcome = self.common("OUT", "P-01", "/root/P-01"); outcome["records"] = [{"knowledge_state": "DERIVED", "record_kind": "SOURCE_OUTCOME", "original_q_ids": ["Q-001", "Q-002", "Q-003"], "source_disposition": "KEPT", "answer_status": "ANSWERED", "author_assignment_id": "A-rP01PRIMARY", "answer_result_id": "P01PRIMARY", "evidence_ids": []}]; self.register_and_accept("OUT", "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/OUT.yaml", outcome, "/root/P-01")
        decision = self.common("DEC-1", "OP-SYNTHESIS", "/root/OP-SYNTHESIS"); decision["criticality"] = "CRITICAL"; self.register_and_accept("DEC", "OP-SYNTHESIS", "DECISION", "decisions/DEC-1.yaml", decision, "/root/OP-SYNTHESIS")
        review = self.common("REV-1", "P-01", "/root/P-01"); review.update({"target_result_id": "DEC-1", "target_hash": qs.sha((self.run / "decisions" / "DEC-1.yaml").read_bytes()), "outcome": "PASS", "review_kind": "SUBJECT", "check_provenance": "p", "blind_input_hash": "b", "risk_coverage": "r"}); self.register_and_accept("REV", "P-01", "REVIEW", "reviews/REV-1.yaml", review, "/root/P-01")
        checked = self.call("validate", "--root", str(self.workspace), "--run-id", "R1", "--phase", "final", ok=False)
        self.assertIn("question_answer_provenance", checked["errors"])
        self.assertIn("decision:DEC-1:critical_review_coverage", checked["errors"])
        self.assertIn("execution_graph:missing", checked["errors"])
    def test_public_lifecycle_allows_renamed_administrative_gate6_review_path(self) -> None:
        """A GATE-6 review is excluded by its binding, never its filename."""
        import copy
        run_id = self.run.name
        snapshot_doc=qs.load_yaml(self.run / "input" / "snapshot.yaml")
        new_mode=isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in qs.NEW_MODES
        arch_mode=new_mode and "ARCHITECTURE_GRAPH_V1" in snapshot_doc.get("methodology_features",[])
        fixture_name = "test_architecture_integration.py" if arch_mode else ("test_iceberg_contracts.py" if new_mode else "test_model_contracts.py")
        fixture_path = HERE / "tests" / fixture_name
        fixture_spec = importlib.util.spec_from_file_location("complete_model_fixture", fixture_path)
        assert fixture_spec and fixture_spec.loader
        fixture_module = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture_module)
        source_class = fixture_module.CompleteArchitectureModel if arch_mode else (fixture_module.CompleteIcebergModel if new_mode else fixture_module.CompleteModel)
        source = source_class(self.base / "source-model")
        core_admission=qs.load_yaml(self.run / "state" / "current.yaml").get("prep_admission_sha256") if new_mode else None
        if new_mode: self.assertIsInstance(core_admission,str)

        def registry() -> dict:
            return qs.load_yaml(self.run / "state" / "assignments.yaml")["results"]

        def replace_requirement(value: object) -> object:
            if isinstance(value, str):
                return "R-1" if (not new_mode and value == "R-001") else value
            if isinstance(value, list):
                return [replace_requirement(item) for item in value]
            if isinstance(value, dict):
                return {key: replace_requirement(item) for key, item in value.items()}
            return value
        source.docs = {key: replace_requirement(value) for key, value in source.docs.items()}
        vector_id="VECTOR-001"
        vector_hash=source.results[vector_id]["content_hash"]
        vector_revision="1"
        approval_id="APPROVAL-001"
        approval_hash=qs.sha((source.run/"input/vector-approval.yaml").read_bytes()) if new_mode else ""
        applicability_id=vector_id
        imported_vector_source=None
        if new_mode:
            if snapshot_doc.get("input_mode")=="BANK_TO_PLAN":
                imported=snapshot_doc["import_binding"]
                vector_id=imported["source_vector_result_id"]
                vector_hash=imported["source_vector_sha256"]
                vector_revision=imported["source_vector_revision"]
                approval_id=imported["source_approval_id"]
                approval_hash=imported["approval_sha256"]
                applicability_id=next(
                    result_id for result_id,entry in registry().items()
                    if entry.get("artifact_type")=="OPERATOR" and
                       qs.load_yaml(self.run/entry["path"]).get("operator_contract")=="BANK_APPLICABILITY"
                )
                imported_vector_source={
                    "source_kind":"IMMUTABLE_INPUT", "source_id":vector_id,
                    "source_sha256":vector_hash, "source_path":imported["vector_path"],
                }
            else:
                approval_doc=qs.load_yaml(self.run/"input/vector-approval.yaml")
                vector_id=approval_doc["vector_result_id"]
                vector_hash=approval_doc["vector_sha256"]
                vector_revision=approval_doc["vector_revision"]
                approval_id=approval_doc["approval_id"]
                approval_hash=qs.sha((self.run/"input/vector-approval.yaml").read_bytes())
                applicability_id=vector_id

            def rebind_iceberg(value: object) -> object:
                if isinstance(value,str):
                    if value==source.snapshot: return self.init["input_snapshot"]
                    if value=="VECTOR-001": return vector_id
                    return value
                if isinstance(value,list): return [rebind_iceberg(item) for item in value]
                if isinstance(value,dict): return {key:rebind_iceberg(item) for key,item in value.items()}
                return value
            source.docs={key:rebind_iceberg(value) for key,value in source.docs.items()}
            for number in range(1,11):
                framing=source.docs[f"FRAME-P-{number:02d}"]
                framing["vector_result_id"]=vector_id
                framing["vector_sha256"]=vector_hash
                framing["iceberg_position"]["input_snapshot"]=self.init["input_snapshot"]
                for element in framing["iceberg_position"]["elements"]: element["g_refs"]=[vector_id]
        # SOURCE_OUTCOMES can only close an original question when the accepted
        # answer itself names that question; a generic primary completion is
        # deliberately insufficient.
        source.docs["PRIMARY-P-01"]["addressed_q_ids"] = ["Q-001", "Q-002", "Q-003"]

        def accept_doc(result_id: str, doc: dict, *, path: str | None = None) -> dict:
            value = copy.deepcopy(doc)
            entry = source.results[result_id]
            value["run_id"] = run_id
            value["input_snapshot"] = self.init["input_snapshot"]
            artifact = entry["artifact_type"]
            if arch_mode and artifact in {"PLAN","FINAL_AUDIT"}:
                graph=source.docs["SYNTHESIS-001"]["architecture_graph"]
                value["architecture_ref"]={"synthesis_id":"SYNTHESIS-001","synthesis_sha256":registry()["SYNTHESIS-001"]["content_hash"],"graph_sha256":qs.sha(qs.canon_yaml(graph))}
                if artifact == "FINAL_AUDIT":
                    state=qs.load_yaml(self.run/"state/assignments.yaml")
                    current=qs.load_yaml(self.run/"state/current.yaml")
                    bank=qs.load_yaml(self.run/"input/bank.yaml")
                    selected=qs.architecture_projection_for_run(self.run,state,current,bank)
                    value["architecture_review"]={"structural_report_sha256":qs.sha(qs.canon_yaml(selected["report"])),"semantic_outcome":"PASS","procedure":"execute structural fixture composition and inspect required transitions","observation":"bounded public CLI fixture; no native methodology qualification","evidence_ids":["E-001"],"findings":[],"limits":["STRUCTURAL_FIXTURE_ONLY; no native semantic qualification"]}
            role = entry["role_id"]
            agent = entry["agent_id"]
            output = path or entry["path"]
            assignment = {
                "assignment_id": value["assignment_id"], "attempt_id": value["attempt_id"],
                "role_id": role, "agent_id": agent, "input_snapshot": self.init["input_snapshot"],
                "artifact_type": artifact, "output_path": output, "dependency_hashes": [],
                "result_id": result_id, "selection_authority": "ROOT", "group_id": "1",
                "assigned_ids": {"questions": [], "requirements": [], "decisions": [], "evidence": []},
            }
            if artifact == "PRIMARY" and result_id == "PRIMARY-P-01":
                assignment["assigned_ids"]={"questions": ["Q-001", "Q-002", "Q-003"], "requirements": ["R-001" if new_mode else "R-1"], "decisions": [], "evidence": []}
            if artifact == "PRIMARY" and result_id == "PRIMARY-ICE-001":
                assignment["assigned_ids"]={"questions":["Q-ICE-001"],"requirements":["R-001"],"decisions":[],"evidence":[]}
            if "supersedes_attempt_id" in value:
                assignment["supersedes_attempt_id"]=value["supersedes_attempt_id"]
            if arch_mode and result_id=="S-001-R3":
                assignment["dependency_result_ids"]=["S-000-R3"]
                assignment["dependency_hashes"]=[registry()["S-000-R3"]["content_hash"]]
            if new_mode:
                assignment.update({"lifecycle_scope":"CORE","prep_admission_sha256":core_admission})
                value.update({"lifecycle_scope":"CORE","prep_admission_sha256":core_admission})
            if artifact == "FRAMING":
                assignment.update({
                    "assigned_ids": {},
                    "role_instruction_paths": ["references/roles/perspectives.md"],
                    "allowed_reads": ["references/phases/01-intake.md"],
                    "blind_handoff": {
                        "handoff_id": "HANDOFF-" + result_id,
                        "assignment_id": assignment["assignment_id"],
                        "attempt_id": assignment["attempt_id"],
                        "role_id": role,
                        "input_snapshot": self.init["input_snapshot"],
                        "phase": "FRAMING",
                        "objective": "establish independent framing before source disclosure",
                        "constraints": ["do not read the source bank", "record uncertainty"],
                    },
                })
            value.update({"artifact_type": artifact, "author_role": role, "agent_id": agent,
                          "assignment_id": assignment["assignment_id"], "attempt_id": assignment["attempt_id"]})
            ap = self.base / ("assignment-" + result_id + ".yaml")
            rp = self.base / ("result-" + result_id + ".yaml")
            put(ap, assignment); put(rp, value)
            self.call("assign", "--root", str(self.workspace), "--run-id", run_id, "--assignment", str(ap))
            if new_mode:
                # CORE acceptance is bound to a durable context packet before
                # the immutable transaction can become a Core candidate.
                context_path=self.base / ("context-" + result_id + ".yaml")
                self.call("context", "--root", str(self.workspace), "--run-id", run_id, "--assignment", str(ap), "--output", str(context_path))
                if arch_mode and result_id=="S-001-R2":
                    packet=qs.load_yaml(context_path)
                    self.assertEqual(packet["architecture_selection_state"],"INCOMPLETE")
                    self.assertEqual(packet["architecture_errors"],["architecture:plan_projection_required"])
                    self.assertNotIn("architecture_ref",packet)
                    diagram=self.base/"unselected-revision.mmd"
                    rejected=self.call("render","--root",str(self.workspace),"--run-id",run_id,"--architecture-view","functional","--output",str(diagram),ok=False)
                    self.assertEqual(rejected["error"],"plan_projection_required")
                    self.assertFalse(diagram.exists())
                if arch_mode and result_id=="S-001-R3":
                    packet=qs.load_yaml(context_path)
                    self.assertEqual(packet["architecture_selection_state"],"INCOMPLETE")
                    self.assertEqual(packet["plan_dependency_selection_state"],"REVISION_IN_PROGRESS")
                    self.assertNotIn("architecture_ref",packet)
                    self.assertEqual([row["source"]["result_id"] for row in packet["accepted_dependencies"]],["S-000-R3"])
            return self.call("accept", "--root", str(self.workspace), "--run-id", run_id, "--assignment", str(ap), "--result", str(rp))

        def patch_review(result_id: str) -> dict:
            doc = copy.deepcopy(source.docs[result_id])
            target = doc["target_result_id"]
            role = source.results[result_id]["role_id"]
            framing = "FRAME-" + role
            doc["target_hash"] = registry()[target]["content_hash"]
            doc["blind_position_hash"] = registry()[framing]["content_hash"]
            doc["blind_input_hash"] = registry()[framing]["content_hash"]
            return doc

        # Accepted evidence, independent framing/primary work, required operators,
        # decision, and non-author reviews all pass through assign+accept.
        evidence_doc=copy.deepcopy(source.docs["E-001"])
        evidence_doc.update({"tool_id":"FIXTURE-TOOL","tool_version":"1","policy_hash":"FIXTURE-POLICY","exposure_hash":"FIXTURE-EXPOSURE","freshness":"CURRENT-AS-DECLARED"})
        if "E-001" not in registry():
            accept_doc("E-001", evidence_doc)
        for number in range(1, 11):
            framing_id="FRAME-P-%02d" % number
            # In new GOAL/BANK runs the accepted PREP-2 framing is the core
            # blind position: it is reused by exact result ID and never
            # regenerated after admission.
            if framing_id not in registry():
                accept_doc(framing_id, source.docs[framing_id])
            accept_doc("PRIMARY-P-%02d" % number, source.docs["PRIMARY-P-%02d" % number])
        iceberg_bindings=[]
        if new_mode:
            origins=[{
                "framing_result_id":f"FRAME-P-{number:02d}",
                "framing_result_sha256":registry()[f"FRAME-P-{number:02d}"]["content_hash"],
                "position_id":f"ICEPOS-P-{number:02d}", "seed_id":f"SEED-P-{number:02d}",
                "origin_role":f"P-{number:02d}",
            } for number in (1,2)]
            hypothesis=copy.deepcopy(source.docs["H-001"])
            hypothesis["origin_position_refs"]=copy.deepcopy(origins)
            h_accept=accept_doc("H-001",hypothesis)
            h_source={
                "source_kind":"ACCEPTED_RESULT", "source_id":"H-001",
                "source_sha256":h_accept["content_hash"], "source_path":registry()["H-001"]["path"],
            }
            if imported_vector_source is None:
                vector_entry=registry()[vector_id]
                vector_source={
                    "source_kind":"ACCEPTED_RESULT", "source_id":vector_id,
                    "source_sha256":vector_hash, "source_path":vector_entry["path"],
                }
                anchor_kind="LOCAL_ACCEPTED_VECTOR"
            else:
                vector_source=copy.deepcopy(imported_vector_source)
                anchor_kind="IMPORTED_APPROVED_VECTOR"
            anchor={
                "anchor_kind":anchor_kind, "vector_id":vector_id,
                "vector_sha256":vector_hash, "revision":vector_revision,
                "approval_binding_id":approval_id, "approval_sha256":approval_hash,
                "applicability_result_id":applicability_id, "goal_claim_path":"/G",
            }
            view_specs=(
                ("VIEW-CURRENT-RESULT",h_source),
                ("VIEW-DESIRED-RESULT",vector_source),
            )
            for view_result_id,source_binding in view_specs:
                view_doc=copy.deepcopy(source.docs[view_result_id])
                view=view_doc["iceberg_view"]
                view["input_snapshot"]=self.init["input_snapshot"]
                view["normative_vector_binding"]=copy.deepcopy(anchor)
                view["source_bindings"]=[copy.deepcopy(source_binding)]
                view["members"][0]["source_binding"]=copy.deepcopy(source_binding)
                view["members"][0]["origin_position_refs"]=copy.deepcopy(origins)
                accepted_view=accept_doc(view_result_id,view_doc)
                iceberg_bindings.append({
                    "view_id":view["view_id"], "view_kind":view["view_kind"],
                    "result_id":view_result_id, "result_sha256":accepted_view["content_hash"],
                    "revision":view["revision"],
                })
        for role in ("OP-VECTOR", "OP-PROJECTION", "OP-DIVERGENCE", "OP-REVERSE", "OP-INDULGENCE"):
            if new_mode and role in {"OP-VECTOR","OP-PROJECTION","OP-DIVERGENCE","OP-REVERSE"} and any(
                entry.get("role_id") == role and entry.get("artifact_type")=="OPERATOR"
                for entry in registry().values()
            ):
                # Reuse an admitted foundational operator only when that
                # precise role already has an immutable accepted card.
                continue
            result_id = "VECTOR-001" if role == "OP-VECTOR" else "OPERATOR-" + role
            if result_id not in source.results:
                source.add_result(result_id, "OPERATOR", role, "/root/" + role,
                                  "operators/" + result_id + ".yaml",
                                  {"operator": "addressable structural operator " + role})
            accept_doc(result_id, source.docs[result_id])
        accept_doc("D-001", source.docs["D-001"])
        for result_id, entry in list(source.results.items()):
            if entry["artifact_type"] == "REVIEW" and not result_id.startswith("REVIEW-GATE-"):
                accept_doc(result_id, patch_review(result_id))
        def prepared_synthesis():
            synthesis = copy.deepcopy(source.docs["SYNTHESIS-001"])
            if new_mode:
                synthesis["iceberg_view_bindings"]=copy.deepcopy(iceberg_bindings)
            # GATE-6 reviews the final audit and is bound by the release gate;
            # it is not a substantive E→Q→D→REVIEW→T→C research edge.
            synthesis["execution_edges"] = [edge for edge in synthesis["execution_edges"] if edge.get("from_id") != "REVIEW-GATE-6" and edge.get("to_id") != "REVIEW-GATE-6"]
            for qid in ("Q-002", "Q-003"):
                synthesis["execution_edges"].append({"from_type": "E", "from_id": "E-001", "to_type": "Q", "to_id": qid})
                synthesis["execution_edges"].append({"from_type": "Q", "from_id": qid, "to_type": "D", "to_id": "D-001"})
            if new_mode:
                synthesis["execution_edges"].extend([
                    {"from_type":"D","from_id":"D-001","to_type":"REVIEW","to_id":"REVIEW-ICE-001"},
                    {"from_type":"REVIEW","from_id":"REVIEW-ICE-001","to_type":"T","to_id":"T-000"},
                ])
            if arch_mode:
                graph=synthesis["architecture_graph"]
                for node in graph["nodes"]:
                    if node["kind"]=="G": node["existing_id"]=vector_id
                    if node["kind"]=="DATA" and node.get("availability")=="EXISTING": node["accepted_hash"]=registry()["E-001"]["content_hash"]
                source.docs["SYNTHESIS-001"]=copy.deepcopy(synthesis)
            return synthesis
        if arch_mode:
            accept_doc("SYNTHESIS-001",prepared_synthesis())
        for result_id in ("S-000", "S-001"):
            doc = copy.deepcopy(source.docs[result_id])
            if new_mode: doc["iceberg_trace"]["view_bindings"]=copy.deepcopy(iceberg_bindings)
            for item in doc["inputs_contracts"]["inputs"]:
                if item.get("input_kind") == "EXISTING": item["accepted_hash"] = registry()["E-001"]["content_hash"]
            accept_doc(result_id, doc)
        # Preserve accepted v1 plan bytes, then accept a fully reciprocal v2
        # revision graph with the same logical S-000/S-001 stages.
        for old_id,new_id,predecessors,next_stages in (("S-000","S-000-R2",[],["S-001-R2"]),("S-001","S-001-R2",["S-000-R2"],[])):
            doc=copy.deepcopy(source.docs[old_id]); doc.update({"id":new_id,"assignment_id":"ASG-"+old_id,"attempt_id":"ATT-2","supersedes_attempt_id":"ATT-1","supersedes":old_id,"revision":"2","related_ids":[old_id],"plan_version":"2"})
            doc["identity_result"]=copy.deepcopy(doc["identity_result"]); doc["identity_result"].update({"stage_id":old_id,"plan_version":"2"})
            doc["local_execution_graph"]=copy.deepcopy(doc["local_execution_graph"]); doc["local_execution_graph"].update({"predecessors":predecessors,"next_stages":next_stages})
            if new_mode: doc["iceberg_trace"]["view_bindings"]=copy.deepcopy(iceberg_bindings)
            for item in doc["inputs_contracts"]["inputs"]:
                if item.get("input_kind") == "EXISTING": item["accepted_hash"]=registry()["E-001"]["content_hash"]
            source.add_result(new_id,"PLAN","PLAN-AUTHOR","/root/PLAN-AUTHOR","plan/"+new_id+".yaml",doc)
            accept_doc(new_id,doc)
        current_graph={"plan_version":"2","stages":[{"logical_stage_id":logical,"result_id":logical+"-R2","sha256":registry()[logical+"-R2"]["content_hash"],"assignment_id":"ASG-"+logical,"attempt_id":"ATT-2"} for logical in ("S-000","S-001")]}
        approved_map={"input_snapshot":self.init["input_snapshot"],"allowed_roles":sorted(qs.ROLES),"role_competence":{role:(["analysis"] if role=="P-01" else []) for role in qs.ROLES},"question_routes":{qid:{"primary_assignment_id":"ASG-PRIMARY-P-01","required_tags":["analysis"]} for qid in ("Q-001","Q-002","Q-003")},"critical_reviews":{},"evidence_applicability":{"E-001":[]},"current_plan_graph":current_graph}
        map_path=self.base/"public-current-plan-map.yaml"; put(map_path,approved_map)
        resumed=self.call("resume","--root",str(self.workspace),"--run-id",run_id,"--approved-map",str(map_path))
        self.assertEqual(resumed["status"],"RESUMED")
        if arch_mode:
            self.assertEqual(resumed["architecture_selection_state"],"CURRENT")
            self.assertNotIn("architecture_errors",resumed)
        # The immutable source bank has three questions; each receives an
        # accepted answer reference and the graph explicitly reaches all three.
        primary = registry()["PRIMARY-P-01"]
        outcome = self.common("SOURCE-OUTCOMES-001", "P-01", "/root/P-01")
        outcome.update({"artifact_type": "SOURCE_OUTCOMES", "records": [
            {"knowledge_state": "DERIVED", "record_kind": "SOURCE_OUTCOME", "original_q_ids": [qid], "source_disposition": "KEPT",
             "answer_status": "ANSWERED", "author_assignment_id": primary["assignment_id"],
             "answer_result_id": "PRIMARY-P-01", "evidence_ids": ["E-001"]}
            for qid in ("Q-001", "Q-002", "Q-003")
        ]})
        if new_mode:
            outcome["records"].append({
                "id":"DERIVED-Q-ICE-001", "record_kind":"DERIVED_Q", "q_id":"Q-ICE-001",
                "parent_source_ids":["Q-001"], "materiality":"answer changes D-001",
                "origin_position_refs":copy.deepcopy(origins),
                "finite_check":{"check_kind":"EXPERIMENT","procedure":"run one finite distinguishing experiment","discriminating_outcomes":["supports","refutes","remains unknown"]},
                "equivalent_q_ids":[], "resource_cap_impact":"one bounded question within the declared cap",
                "evidence_ids":["E-001"],
            })
            draft=copy.deepcopy(outcome)
            draft.update({"id":"SOURCE-OUTCOMES-DRAFT","assignment_id":"A-source-outcomes-draft","revision":"1"})
            self.register_and_accept("source-outcomes-draft", "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/SOURCE-OUTCOMES-DRAFT.yaml", draft, "/root/P-01", root_assignment=True)
            ice_primary=copy.deepcopy(source.docs["PRIMARY-P-01"])
            ice_primary.update({"id":"PRIMARY-ICE-001","assignment_id":"ASG-PRIMARY-ICE-001","addressed_q_ids":["Q-ICE-001"]})
            source.add_result("PRIMARY-ICE-001","PRIMARY","P-01","/root/P-01","positions/PRIMARY-ICE-001.yaml",ice_primary)
            accept_doc("PRIMARY-ICE-001",source.docs["PRIMARY-ICE-001"])
            ice_review=copy.deepcopy(source.docs["REVIEW-PEER-02-03"])
            ice_review.update({
                "id":"REVIEW-ICE-001", "assignment_id":"ASG-REVIEW-ICE-001",
                "target_result_id":"PRIMARY-ICE-001", "target_hash":registry()["PRIMARY-ICE-001"]["content_hash"],
                "blind_position_hash":registry()["FRAME-P-02"]["content_hash"],
                "blind_input_hash":registry()["FRAME-P-02"]["content_hash"],
            })
            source.add_result("REVIEW-ICE-001","REVIEW","P-02","/root/P-02","reviews/REVIEW-ICE-001.yaml",ice_review)
            accept_doc("REVIEW-ICE-001",source.docs["REVIEW-ICE-001"])
            outcome.update({"supersedes":"SOURCE-OUTCOMES-DRAFT","revision":"2"})
            outcome["records"][-1].update({"answer_status":"ANSWERED","author_assignment_id":registry()["PRIMARY-ICE-001"]["assignment_id"],"answer_result_id":"PRIMARY-ICE-001"})
        self.register_and_accept("source-outcomes", "P-01", "SOURCE_OUTCOMES", "answers/source-outcomes/SOURCE-OUTCOMES-001.yaml", outcome, "/root/P-01", root_assignment=True)
        if new_mode:
            approved_map2=copy.deepcopy(approved_map)
            approved_map2.update({"version":"derived-2","supersedes_sha256":qs.sha(map_path.read_bytes())})
            approved_map2["question_routes"]["Q-ICE-001"]={"primary_assignment_id":"ASG-PRIMARY-ICE-001","required_tags":["analysis"]}
            map2_path=self.base/"public-current-plan-map-derived.yaml"; put(map2_path,approved_map2)
            self.assertEqual(self.call("resume","--root",str(self.workspace),"--run-id",run_id,"--approved-map",str(map2_path))["status"],"RESUMED")
            approved_map=approved_map2; map_path=map2_path
        if not arch_mode:
            accept_doc("SYNTHESIS-001",prepared_synthesis())

        first_normative_review=next(result_id for result_id,entry in registry().items() if entry.get("artifact_type")=="REVIEW" and source.docs[result_id].get("target_result_id") in {"D-001","PRIMARY-P-01"})
        gate_targets={1:next(result_id for result_id,entry in registry().items() if entry.get("role_id")=="OP-VECTOR" and entry.get("artifact_type")=="OPERATOR"),2:"SOURCE-OUTCOMES-001",3:first_normative_review,4:"SYNTHESIS-001",5:"S-000-R2"}
        for number,target_id in gate_targets.items():
            review_id="REVIEW-GATE-"+str(number)
            review=copy.deepcopy(source.docs[review_id]); review["target_result_id"]=target_id
            review["target_hash"]=registry()[target_id]["content_hash"]
            role=source.results[review_id]["role_id"]; framing="FRAME-"+role
            review["blind_position_hash"]=registry()[framing]["content_hash"]
            review["blind_input_hash"]=registry()[framing]["content_hash"]
            accept_doc(review_id,review)

        framing_ids={result_id for result_id,entry in registry().items() if entry.get("artifact_type")=="FRAMING"}
        normative_reviews={result_id for result_id,entry in registry().items() if entry.get("artifact_type")=="REVIEW" and not result_id.startswith("REVIEW-GATE-")}
        required_by_gate={
            1:{"input/bank.yaml",gate_targets[1]}|framing_ids,
            2:{"SOURCE-OUTCOMES-001","PRIMARY-P-01","E-001"}|({"PRIMARY-ICE-001"} if new_mode else set()),
            3:normative_reviews|({"REVIEW-H-001"} if new_mode else set()),
            4:{"SYNTHESIS-001"}|({"VIEW-CURRENT-RESULT","VIEW-DESIRED-RESULT"} if new_mode else set()),
            5:{"S-000-R2","S-001-R2"}|({"VIEW-CURRENT-RESULT","VIEW-DESIRED-RESULT"} if new_mode else set()),
        }
        for number in range(1,6):
            review_id="REVIEW-GATE-"+str(number); gate=copy.deepcopy(source.gate(number,review_id,"E-001"))
            required=sorted(required_by_gate[number]); gate["required_objects"]=required
            gate["run_id"]=run_id; gate["input_snapshot"]=self.init["input_snapshot"]
            gate["content_review"]["sha256"]=registry()[review_id]["content_hash"]
            gate["content_review"]["target_hashes"]=[qs.sha((self.run/item).read_bytes()) if item=="input/bank.yaml" else registry()[item]["content_hash"] for item in required]
            gate["evidence_refs"][0]["sha256"]=registry()["E-001"]["content_hash"]
            gate["completed_checks"][0]["evidence_sha256"]=registry()["E-001"]["content_hash"]
            put(self.run/"gates"/("GATE-"+str(number)+".yaml"),gate)

        pre = self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "core")
        self.assertEqual(pre["errors"], [])
        prepared = self.call("seal", "--root", str(self.workspace), "--run-id", run_id, "--release-id", "REL-1", "--phase", "prepare")
        hook=getattr(self,"_core_propagation_hook",None)
        if callable(hook): hook("prepared", self)

        repair_audit=copy.deepcopy(source.docs["AUDIT-001"])
        repair_audit.update({"id":"AUDIT-REPAIR-001","assignment_id":"ASG-AUDIT-REPAIR-001","attempt_id":"ATT-1","verdict":"REPAIR_REQUIRED","core_manifest_sha256":prepared["core_manifest_sha256"]})
        if new_mode: repair_audit["iceberg_view_bindings"]=copy.deepcopy(iceberg_bindings)
        for detail in repair_audit["area_details"].values():
            detail["files"]=["plan/S-000-R2.yaml"]
            detail["hashes"]=[registry()["S-000-R2"]["content_hash"]]
        source.add_result("AUDIT-REPAIR-001","FINAL_AUDIT","FINAL-AUDITOR","/root/FINAL-AUDITOR","audit/final/AUDIT-REPAIR-001.yaml",repair_audit)
        repair_accept=accept_doc("AUDIT-REPAIR-001",source.docs["AUDIT-REPAIR-001"],path="audit/final/AUDIT-REPAIR-001.yaml")
        self.assertEqual(repair_accept["current_selection"]["result_id"],"AUDIT-REPAIR-001")

        # The negative audit is never relabeled.  The author produces a new
        # plan graph, Root selects it through a lineage-bound map revision,
        # and a new pre-audit core identity is prepared.
        for old_id,new_id,predecessors,next_stages in (("S-000-R2","S-000-R3",[],["S-001-R3"]),("S-001-R2","S-001-R3",["S-000-R3"],[])):
            logical=old_id.removesuffix("-R2"); doc=copy.deepcopy(source.docs[logical])
            doc.update({"id":new_id,"assignment_id":"ASG-"+logical,"attempt_id":"ATT-3","supersedes_attempt_id":"ATT-2","supersedes":old_id,"revision":"3","related_ids":[old_id],"plan_version":"3"})
            doc["identity_result"]=copy.deepcopy(doc["identity_result"]); doc["identity_result"].update({"stage_id":logical,"plan_version":"3"})
            doc["local_execution_graph"]=copy.deepcopy(doc["local_execution_graph"]); doc["local_execution_graph"].update({"predecessors":predecessors,"next_stages":next_stages})
            if new_mode: doc["iceberg_trace"]["view_bindings"]=copy.deepcopy(iceberg_bindings)
            for item in doc["inputs_contracts"]["inputs"]:
                if item.get("input_kind")=="EXISTING": item["accepted_hash"]=registry()["E-001"]["content_hash"]
            source.add_result(new_id,"PLAN","PLAN-AUTHOR","/root/PLAN-AUTHOR","plan/"+new_id+".yaml",doc)
            accept_doc(new_id,doc)
        graph3={"plan_version":"3","stages":[{"logical_stage_id":logical,"result_id":logical+"-R3","sha256":registry()[logical+"-R3"]["content_hash"],"assignment_id":"ASG-"+logical,"attempt_id":"ATT-3"} for logical in ("S-000","S-001")]}
        approved_map3=copy.deepcopy(approved_map); approved_map3.update({"version":"3","supersedes_sha256":qs.sha(map_path.read_bytes()),"current_plan_graph":graph3})
        map3_path=self.base/"public-current-plan-map-v3.yaml"; put(map3_path,approved_map3)
        revised=self.call("resume","--root",str(self.workspace),"--run-id",run_id,"--approved-map",str(map3_path))
        self.assertEqual(revised["status"],"RESUMED")
        gate5=qs.load_yaml(self.run/"gates/GATE-5.yaml")
        gate5_required=["S-000-R2","S-001-R2","S-000-R3","S-001-R3"] + (["VIEW-CURRENT-RESULT","VIEW-DESIRED-RESULT"] if new_mode else [])
        gate5["required_objects"]=gate5_required
        gate5["content_review"]["target_hashes"]=[registry()[result_id]["content_hash"] for result_id in gate5_required]
        put(self.run/"gates/GATE-5.yaml",gate5)
        self.assertEqual(self.call("validate","--root",str(self.workspace),"--run-id",run_id,"--phase","core")["errors"],[])
        prepared2=self.call("seal","--root",str(self.workspace),"--run-id",run_id,"--release-id","REL-2","--phase","prepare")
        self.assertNotEqual(prepared2["core_manifest_sha256"],prepared["core_manifest_sha256"])

        audit = copy.deepcopy(source.docs["AUDIT-001"])
        audit.update({"core_manifest_sha256":prepared2["core_manifest_sha256"],"supersedes":"AUDIT-REPAIR-001","related_ids":["AUDIT-REPAIR-001"],"revision":"2"})
        if new_mode: audit["iceberg_view_bindings"]=copy.deepcopy(iceberg_bindings)
        for detail in audit["area_details"].values():
            detail["files"] = ["plan/S-000-R3.yaml"]
            detail["hashes"] = [registry()["S-000-R3"]["content_hash"]]
        audit_accept = accept_doc("AUDIT-001", audit, path="audit/final/AUDIT-001.yaml")
        administrative_path = "reviews/admin-final-review.yaml"
        accept_doc("REVIEW-GATE-6", patch_review("REVIEW-GATE-6"), path=administrative_path)
        gate6 = copy.deepcopy(source.gate(6, "REVIEW-GATE-6", "AUDIT-001"))
        gate6["run_id"] = run_id; gate6["input_snapshot"] = self.init["input_snapshot"]
        gate6["required_objects"]=["AUDIT-001"] + (["VIEW-CURRENT-RESULT","VIEW-DESIRED-RESULT"] if new_mode else [])
        gate6["content_review"]["sha256"] = registry()["REVIEW-GATE-6"]["content_hash"]
        gate6["content_review"]["target_hashes"] = [registry()[result_id]["content_hash"] for result_id in gate6["required_objects"]]
        gate6["evidence_refs"][0]["sha256"] = audit_accept["content_hash"]
        gate6["completed_checks"][0]["evidence_sha256"] = audit_accept["content_hash"]
        (self.run / "gates/GATE-6").mkdir(parents=True, exist_ok=True)
        put(self.run / "gates/GATE-6/AUDIT-001.yaml", gate6)
        self.assertEqual(self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "final")["errors"], [])
        self.assertEqual(self.call("seal", "--root", str(self.workspace), "--run-id", run_id, "--release-id", "REL-2", "--phase", "finalize")["status"], "SEALED")
        hook=getattr(self,"_core_propagation_hook",None)
        if callable(hook): hook("sealed", self)
        self.assertEqual(self.call("verify", "--root", str(self.workspace), "--run-id", run_id)["status"], "PASS")
        # Selected v2 stages and non-PLAN evidence remain blocking.  A missing
        # or hash-drifted projection also cannot excuse stale historical data.
        sealed_current_path=self.run / "state/current.yaml"
        sealed_current_raw=sealed_current_path.read_bytes()
        selected_stale=qs.load_yaml(sealed_current_path); selected_stale["stale_result_ids"]=["S-000-R3"]
        put(sealed_current_path,selected_stale)
        selected_failure=self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "core", ok=False)
        if new_mode:
            # Direct post-admission current rewrites fail at the contiguous
            # transition-chain guard before a stale selector can be consumed.
            self.assertTrue(any(error.startswith("prep:current_transition:") for error in selected_failure["errors"]))
        else:
            self.assertTrue(any(error == "stale_result_ids:S-000-R3" for error in selected_failure["errors"]))
        sealed_current_path.write_bytes(sealed_current_raw)
        evidence_stale=qs.load_yaml(sealed_current_path); evidence_stale["stale_result_ids"]=["E-001"]
        put(sealed_current_path,evidence_stale)
        evidence_failure=self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "core", ok=False)
        if new_mode:
            self.assertTrue(any(error.startswith("prep:current_transition:") for error in evidence_failure["errors"]))
        else:
            self.assertTrue(any(error == "stale_result_ids:E-001" for error in evidence_failure["errors"]))
        sealed_current_path.write_bytes(sealed_current_raw)
        missing_projection=qs.load_yaml(sealed_current_path); missing_projection.pop("current_plan_projection",None); missing_projection.pop("current_plan_projection_sha256",None)
        put(sealed_current_path,missing_projection)
        missing_failure=self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "core", ok=False)
        if new_mode:
            self.assertTrue(any(error.startswith("prep:current_transition:") for error in missing_failure["errors"]))
        else:
            self.assertIn("plan_projection_required",missing_failure["errors"])
        sealed_current_path.write_bytes(sealed_current_raw)
        drifted_projection=qs.load_yaml(sealed_current_path); drifted_projection["current_plan_projection_sha256"]="0" * 64
        put(sealed_current_path,drifted_projection)
        drift_failure=self.call("validate", "--root", str(self.workspace), "--run-id", run_id, "--phase", "core", ok=False)
        if new_mode:
            self.assertTrue(any(error.startswith("prep:current_transition:") for error in drift_failure["errors"]))
        else:
            self.assertIn("plan_projection_hash_drift",drift_failure["errors"])
        sealed_current_path.write_bytes(sealed_current_raw)
        self.assertEqual(self.call("verify", "--root", str(self.workspace), "--run-id", run_id)["status"], "PASS")
        # The release binds the selected logical plan graph as well as bytes in
        # the core manifest.  A selection rewrite after seal is a release
        # failure even when none of the selected PLAN files changed.
        release_path=self.run / "release.yaml"; original_release=release_path.read_bytes()
        forged_release=qs.load_yaml(release_path); forged_release["current_plan_projection"]["plan_version"]="forged"
        put(release_path,forged_release)
        projection_tamper=self.call("verify", "--root", str(self.workspace), "--run-id", run_id, ok=False)
        self.assertIn("current_plan_projection_tamper",projection_tamper["errors"])
        release_path.write_bytes(original_release)
        self.assertEqual(self.call("verify", "--root", str(self.workspace), "--run-id", run_id)["status"], "PASS")
        # This review is outside the pre-audit manifest, but its exact accepted
        # path and hash are bound in the release and therefore still protected.
        original_admin = (self.run / administrative_path).read_bytes()
        (self.run / administrative_path).write_text("tampered after seal\n", encoding="utf-8")
        tampered = self.call("verify", "--root", str(self.workspace), "--run-id", run_id, ok=False)
        self.assertIn("administrative_review_tamper", tampered["errors"])
        (self.run / administrative_path).write_bytes(original_admin)
        self.assertEqual(self.call("verify", "--root", str(self.workspace), "--run-id", run_id)["status"], "PASS")
        # A core file added after prepare is not part of the immutable pre-audit
        # manifest and cannot be smuggled into a later finalize call.
        late = self.run / "evidence" / "late-core-drift.yaml"; put(late, {"late": "drift"})
        drift = self.call("seal", "--root", str(self.workspace), "--run-id", run_id, "--release-id", "REL-2", "--phase", "finalize", ok=False)
        self.assertEqual(drift["error"], "prepared_core_missing_or_drifted")
        late.unlink()
        self.assertEqual(self.call("verify", "--root", str(self.workspace), "--run-id", run_id)["status"], "PASS")

    def test_goal_prep2_map_context_and_result_binds_goal_result_id(self) -> None:
        """PREP P results bind the immutable assignment's goal_result_id.

        The public assignment format deliberately has no vector_result_id: it
        calls the selected Vector `goal_result_id`.  This guards the real
        ten-P packet format against an accidental controller-only field.
        """
        workspace=self.base/"goal-work"; goal=self.base/"goal.md"; statement=self.base/"statement.md"
        goal.write_text("goal fixture\n",encoding="utf-8"); statement.write_text("owner statement\n",encoding="utf-8")
        init=self.call("init","--root",str(workspace),"--run-id","GOAL1","--mode","GOAL_TO_PLAN","--goal",str(goal),"--material",str(statement),"--test-fixture")
        run=workspace/"runs"/"GOAL1"; snapshot=init["input_snapshot"]
        def common(result_id: str, role: str, agent: str) -> dict:
            return {"schema_version":"question-space-v4","id":result_id,"run_id":"GOAL1","input_snapshot":snapshot,"author_role":role,"agent_id":agent,"knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z"}
        vector_assignment={"assignment_id":"A-VECTOR","attempt_id":"TRY-1","role_id":"OP-VECTOR","agent_id":"/test/vector","input_snapshot":snapshot,"artifact_type":"OPERATOR","output_path":"operators/vector.yaml","dependency_hashes":[],"result_id":"VECTOR-001","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR"}
        vector=common("VECTOR-001","OP-VECTOR","/test/vector")
        vector.update({"assignment_id":"A-VECTOR","attempt_id":"TRY-1","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR","G":"goal","NOT_G":["not goal"],"value_recipient":"owner","scope":"fixture","success_criteria":["criterion"],"requirements":["R-001"],"requirement_definitions":[{"id":"R-001","definition":"bound"}],"hard_constraints":["64 MiB"],"changeable_assumptions":["choice"],"current_state":"research","resources":"local","authority":"owner","owner_decisions":[{"id":"OD-1","decision_needed":"approve","why_owner_only":"owner","known_context":"fixture","answer_effect":"continue"}],"open_research_questions":["unknown"]})
        vap=self.base/"goal-vector-assignment.yaml"; vrp=self.base/"goal-vector-result.yaml"; put(vap,vector_assignment); put(vrp,vector)
        self.call("assign","--root",str(workspace),"--run-id","GOAL1","--assignment",str(vap)); accepted=self.call("accept","--root",str(workspace),"--run-id","GOAL1","--assignment",str(vap),"--result",str(vrp))
        approval={"schema_version":"question-space-v4","approval_id":"APP-1","run_id":"GOAL1","input_snapshot":snapshot,"vector_result_id":"VECTOR-001","vector_sha256":accepted["content_hash"],"vector_revision":"1","decision":"APPROVE","approval_mode":"SIMULATED_TEST","approver_id":"ROOT-AS-SIMULATED_TEST_OWNER","authority_basis":"fixture","observation_provenance":"fixture-1","statement_path":"input/materials/001-statement.md","statement_sha256":qs.sha(statement.read_bytes()),"created_at":"2026-10-21T00:00:00Z","fixture_only":"YES"}
        approval_path=self.base/"approval.yaml"; put(approval_path,approval); approved=self.call("approve-vector","--root",str(workspace),"--run-id","GOAL1","--approval",str(approval_path))
        rows=[]
        for number in range(1,11):
            role=f"P-{number:02d}"; profile={"role_id":role,"agent_id":f"/test/{role}","title":role,"competence":"competence","analysis_scope":"scope","unique_contribution":"contribution","boundary":"boundary","plausible_blindspots":"blindspot"}
            rows.append({**profile,"profile_sha256":qs.sha(qs.canon_yaml(profile))})
        perspective_map={"schema_version":"question-space-v4","id":"MAP-1","run_id":"GOAL1","input_snapshot":snapshot,"vector_result_id":"VECTOR-001","vector_sha256":accepted["content_hash"],"approval_sha256":approved["approval_sha256"],"selection_authority":"ROOT","perspectives":rows}
        map_path=self.base/"perspective-map.yaml"; put(map_path,perspective_map)
        self.assertEqual(self.call("publish-perspective-map","--root",str(workspace),"--run-id","GOAL1","--perspective-map",str(map_path))["status"],"PERSPECTIVE_MAP_PUBLISHED")
        profile={key:rows[0][key] for key in ("role_id","agent_id","title","competence","analysis_scope","unique_contribution","boundary","plausible_blindspots")}
        p_assignment={"assignment_id":"A-P01","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/test/P-01","input_snapshot":snapshot,"artifact_type":"FRAMING","output_path":"positions/P-01.yaml","dependency_hashes":[],"result_id":"FRAME-001","lifecycle_scope":"PREP","prep_stage":"PREP-2_PERSPECTIVES","goal_result_id":"VECTOR-001","vector_sha256":accepted["content_hash"],"approval_sha256":approved["approval_sha256"],"perspective_profile":profile,"perspective_profile_hash":rows[0]["profile_sha256"],"role_instruction_paths":["references/roles/perspectives.md"],"allowed_reads":["references/invariants.md","references/preparation.md","input/materials/001-statement.md"],"assigned_ids":{}}
        pap=self.base/"goal-p01-assignment.yaml"; put(pap,p_assignment); self.call("assign","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap))
        context=self.base/"goal-p01-context.yaml"; context_out=self.call("context","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--output",str(context)); packet=qs.load_yaml(context)
        self.assertEqual(packet["bank"],"ABSENT"); self.assertEqual(packet["goal"]["requirement_definitions"][0]["id"],"R-001")
        self.assertEqual([item["path"] for item in packet["materials"]],["input/materials/001-statement.md"])
        self.assertEqual(packet["materials"][0]["sha256"],qs.sha((run/"input/materials/001-statement.md").read_bytes()))
        self.assertNotIn("input/materials/001-statement.md",[item["path"] for item in packet["readset"]])
        framing=common("FRAME-001","P-01","/test/P-01")
        framing.update({"assignment_id":"A-P01","attempt_id":"TRY-1","artifact_type":"FRAMING","lifecycle_scope":"PREP","prep_stage":"PREP-2_PERSPECTIVES","perspective_profile_hash":rows[0]["profile_sha256"],"vector_result_id":"VECTOR-001","vector_sha256":accepted["content_hash"],"approval_sha256":approved["approval_sha256"],"context_packet_sha256":context_out["sha256"],"blind_input":{"contains_bank":"NO","contains_foreign_p_results":"NO","contains_preferred_solution":"NO","allowed":["context packet","own assignment","own profile","role instructions"],"protocol":"native-P-v1","context_source":"state/contexts/A-P01--TRY-1.yaml"},"initial_questions":[],"iceberg_position":{"position_id":"ICEPOS-P-01","origin_role":"P-01","origin_assignment_id":"A-P01","input_snapshot":snapshot,"elements":[{"seed_id":"SEED-P-01","candidate_view":"CURRENT_REALITY","element_kind":"ASSUMPTION","statement":"bounded assumption","scope":"fixture scope","g_refs":["VECTOR-001"],"r_refs":["R-001"],"materiality":"changes a decision","knowledge_status":"HYPOTHESIS","source_refs":[],"finite_check":{"check_kind":"OBSERVATION","procedure":"perform one bounded observation","discriminating_outcomes":["supports","refutes"]}}],"relations":[]}})
        wrong={**framing,"vector_result_id":"OTHER"}; wrong_path=self.base/"goal-p01-wrong.yaml"; put(wrong_path,wrong)
        self.call("accept","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--result",str(wrong_path),ok=False)
        wrong_context={**framing,"context_packet_sha256":"0"*64}; wrong_context_path=self.base/"goal-p01-context-wrong.yaml"; put(wrong_context_path,wrong_context)
        self.call("accept","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--result",str(wrong_context_path),ok=False)
        wrong_blind={**framing,"blind_input":{**framing["blind_input"],"contains_bank":"YES"}}; wrong_blind_path=self.base/"goal-p01-blind-wrong.yaml"; put(wrong_blind_path,wrong_blind)
        self.call("accept","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--result",str(wrong_blind_path),ok=False)
        framing_path=self.base/"goal-p01-result.yaml"; put(framing_path,framing)
        self.assertEqual(self.call("accept","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--result",str(framing_path))["status"],"ACCEPTED")
        resumed=self.call("resume","--root",str(workspace),"--run-id","GOAL1")
        self.assertEqual(resumed["tasks"],[])
        stored=run/"state/contexts/A-P01--TRY-1.yaml"; original_context=stored.read_bytes(); stored.write_text("tampered context\n",encoding="utf-8")
        fresh=self.base/"goal-p01-fresh-context.yaml"; self.call("context","--root",str(workspace),"--run-id","GOAL1","--assignment",str(pap),"--output",str(fresh),ok=False)
        self.assertFalse(fresh.exists())
        checked=self.call("validate","--root",str(workspace),"--run-id","GOAL1","--phase","core",ok=False)
        self.assertIn("prep:framing_context:FRAME-001:hash_mismatch",checked["errors"])
        stored.write_bytes(original_context)


    def test_bank_import_is_one_way_and_tamper_checked(self) -> None:
        """BANK init copies a foreign approved Vector once and pins every byte."""
        workspace=self.base/"bank-work"; goal=self.base/"bank-goal.md"; statement=self.base/"bank-statement.md"
        goal.write_text("bank goal\n",encoding="utf-8"); statement.write_text("foreign statement\n",encoding="utf-8")
        source_snapshot="a"*64
        vector={"schema_version":"question-space-v4","id":"FOREIGN-VECTOR","run_id":"SOURCE-RUN","input_snapshot":source_snapshot,"author_role":"OP-VECTOR","role_id":"OP-VECTOR","agent_id":"/source/vector","knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"7","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z","artifact_type":"OPERATOR","G":"goal","NOT_G":["not goal"],"value_recipient":"owner","scope":"fixture","success_criteria":["criterion"],"requirements":["R-001"],"requirement_definitions":[{"id":"R-001","text":"Satisfy the exact bounded imported requirement for this fixture.","hard_requirement":"true","approved_contract":"true","working_assumption":"false"}],"hard_constraints":["64 MiB"],"changeable_assumptions":["choice"],"current_state":"research","resources":"local","authority":"owner","owner_decisions":[{"id":"OD-1","decision_needed":"approve","why_owner_only":"owner","known_context":"fixture","answer_effect":"continue"}],"open_research_questions":["unknown"]}
        vector_path=self.base/"foreign-vector.yaml"; put(vector_path,vector); vector_hash=qs.sha(vector_path.read_bytes())
        approval={"schema_version":"question-space-v4","approval_id":"FOREIGN-APP","run_id":"SOURCE-RUN","input_snapshot":source_snapshot,"vector_result_id":"FOREIGN-VECTOR","vector_sha256":vector_hash,"vector_revision":"7","decision":"APPROVE","approval_mode":"SIMULATED_TEST","approver_id":"ROOT-AS-SIMULATED_TEST_OWNER","authority_basis":"fixture","observation_provenance":"fixture-1","statement_path":"source/statement.md","statement_sha256":qs.sha(statement.read_bytes()),"created_at":"2026-10-21T00:00:00Z","fixture_only":"YES"}
        approval_path=self.base/"foreign-approval.yaml"; put(approval_path,approval)
        bank={"schema_version":"bank-v1","questions":[{"id":f"Q-{number:03d}","text":f"investigate {number}","source_offset":str(number),"unknown_key":f"u-{number}","mechanism":f"m-{number}","applicability":f"a-{number}","changeable_decision":f"d-{number}","evidence_needed":f"e-{number}","lineage":{"operation":"DERIVED_UNKNOWN","source_question_ids":[f"SOURCE-Q-{number:03d}"],"source_result_ids":["FOREIGN-VECTOR"],"source_roles":["OP-VECTOR"],"reason":"import"},"g_refs":["G-1"],"r_refs":["R-001"],"not_g_refs":[]} for number in range(1,4)],"requirements":["R-001"]}
        bank_path=self.base/"bank-import.yaml"; put(bank_path,bank)
        init=self.call("init","--root",str(workspace),"--run-id","BANK1","--mode","BANK_TO_PLAN","--goal",str(goal),"--bank",str(bank_path),"--approved-vector",str(vector_path),"--vector-approval",str(approval_path),"--approval-statement",str(statement),"--test-fixture")
        run=workspace/"runs"/"BANK1"; snap=qs.load_yaml(run/"input/snapshot.yaml"); binding=snap["import_binding"]
        self.assertEqual(binding["source_vector_sha256"],vector_hash); self.assertEqual((run/binding["vector_path"]).read_bytes(),vector_path.read_bytes())
        original=(run/binding["vector_path"]).read_bytes(); (run/binding["vector_path"]).write_text("tampered\n",encoding="utf-8")
        failed=self.call("recover","--root",str(workspace),"--run-id","BANK1",ok=False)
        self.assertIn("immutable_import_bytes_drift",failed["errors"][0]); (run/binding["vector_path"]).write_bytes(original)
        self.assertEqual(self.call("recover","--root",str(workspace),"--run-id","BANK1")["status"],"RECOVERED")
        rows=[]
        for number in range(1,11):
            role=f"P-{number:02d}"; profile={"role_id":role,"agent_id":f"/root/{role}","title":role,"competence":f"competence {number}","analysis_scope":f"scope {number}","unique_contribution":f"contribution {number}","boundary":f"boundary {number}","plausible_blindspots":f"blindspot {number}"}
            rows.append({**profile,"profile_sha256":qs.sha(qs.canon_yaml(profile))})
        perspective={"schema_version":"question-space-v4","id":"MAP-BANK","run_id":"BANK1","input_snapshot":init["input_snapshot"],"vector_result_id":"FOREIGN-VECTOR","vector_sha256":vector_hash,"approval_sha256":binding["approval_sha256"],"selection_authority":"ROOT","perspectives":rows}
        map_path=self.base/"bank-map.yaml"; put(map_path,perspective); map_out=self.call("publish-perspective-map","--root",str(workspace),"--run-id","BANK1","--perspective-map",str(map_path))
        source_ids=["VECTOR_NO_DRIFT","PERSPECTIVES_DISTINCT_NECESSARY","EACH_R","EACH_MATERIAL_NOT_G","ALTERNATIVES","COMMON_UNPROVED_ASSUMPTIONS","INTERACTIONS","RESEARCHABILITY","CHANGEABLE_DECISIONS","CENTRAL_MECHANISM_SUFFICIENCY"]
        source_checks=[]
        for check_id in source_ids:
            row={"check_id":check_id,"outcome":"PASS","basis":f"Root basis {check_id}","evidence_refs":["E-001"],"targeted_repair_assignments":[]}
            if check_id=="PERSPECTIVES_DISTINCT_NECESSARY": row.update({"root_semantic_outcome":"PASS","perspective_assessments":[{"role_id":item["role_id"],"competence":item["competence"],"analysis_scope":item["analysis_scope"],"unique_contribution":item["unique_contribution"],"boundary":item["boundary"],"necessity":f"necessary {item['role_id']}","basis_refs":["E-001"]} for item in rows],"material_overlap_assessments":[]})
            if check_id=="COMMON_UNPROVED_ASSUMPTIONS": row.update({"root_semantic_outcome":"PASS","detected_assumptions":[],"none_with_basis":{"outcome":"NONE_WITH_BASIS","basis_refs":["E-001"]}})
            source_checks.append(row)
        e_assignment={"assignment_id":"A-EVIDENCE","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/P-01","input_snapshot":init["input_snapshot"],"artifact_type":"EVIDENCE","output_path":"evidence/PREP-E-BANK.yaml","dependency_hashes":[],"result_id":"E-001","lifecycle_scope":"PREP","selection_authority":"ROOT","group_id":"1","prep_stage":"PREP-3_EXPAND_NORMALIZE"}
        e_result={"schema_version":"question-space-v4","id":"E-001","run_id":"BANK1","input_snapshot":init["input_snapshot"],"author_role":"P-01","agent_id":"/root/P-01","knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z","assignment_id":"A-EVIDENCE","attempt_id":"TRY-1","artifact_type":"EVIDENCE","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","tool_id":"FIXTURE-TOOL","tool_version":"1","policy_hash":"FIXTURE-POLICY","exposure_hash":"FIXTURE-EXPOSURE","freshness":"CURRENT-AS-DECLARED","changed":"UNCHANGED"}
        eap=self.base/"bank-evidence.assignment.yaml"; erp=self.base/"bank-evidence.result.yaml"; put(eap,e_assignment); put(erp,e_result); self.call("assign","--root",str(workspace),"--run-id","BANK1","--assignment",str(eap)); self.call("accept","--root",str(workspace),"--run-id","BANK1","--assignment",str(eap),"--result",str(erp))
        admission={"schema_version":"prep-admission-v1","id":"ADMISSION-BANK","run_id":"BANK1","input_snapshot":init["input_snapshot"],"input_mode":"BANK_TO_PLAN","goal_sha256":snap["goal_sha256"],"vector_binding":{"result_id":"FOREIGN-VECTOR","sha256":vector_hash},"approval_binding":{"id":"FOREIGN-APP","sha256":binding["approval_sha256"]},"perspective_map_binding":{"id":"MAP-BANK","sha256":map_out["perspective_map_sha256"]},"initial_set_bindings":[],"expansion_bindings":[],"normalization_decisions":[{"id":"N-1","decision":"import retained"}],"bank_path":"input/bank.yaml","bank_sha256":snap["bank_sha256"],"requirements_sha256":snap["requirements_sha256"],"question_count":"3","size_assessment":{"classification":"BELOW_TARGET_WITH_RATIONALE","rationale":"small complete fixture"},"source_checks":source_checks,"uncovered_areas":[],"generation_performed":"NO","status":"ADMITTED"}
        # The applicability proof is pre-admission PREP-3 evidence and binds this exact admission outcome set.
        applicability={"schema_version":"question-space-v4","id":"APPLY-BANK","run_id":"BANK1","input_snapshot":init["input_snapshot"],"author_role":"OP-VECTOR","role_id":"OP-VECTOR","agent_id":"/root/OP-VECTOR","knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z","assignment_id":"A-APPLY-BANK","attempt_id":"TRY-1","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","operator_contract":"BANK_APPLICABILITY","current_run_id":"BANK1","current_input_snapshot":init["input_snapshot"],"import_payload_sha256":binding["import_payload_sha256"],"current_goal_sha256":snap["goal_sha256"],"current_material_hashes":snap["materials"],"current_bank_sha256":snap["bank_sha256"],"current_requirements_sha256":snap["requirements_sha256"],"g_comparison":{"outcome":"UNCHANGED","basis":"exact goal"},"requirement_comparisons":[{"source_requirement_id":"R-001","current_requirement_id":"R-001","outcome":"UNCHANGED"}],"authority_comparison":{"outcome":"UNCHANGED","basis":"exact authority"},"source_check_outcomes":{check_id:"PASS" for check_id in source_ids},"overall":"UNCHANGED"}
        a_assignment={"assignment_id":"A-APPLY-BANK","attempt_id":"TRY-1","role_id":"OP-VECTOR","agent_id":"/root/OP-VECTOR","input_snapshot":init["input_snapshot"],"artifact_type":"OPERATOR","output_path":"operators/APPLY-BANK.yaml","dependency_hashes":[],"result_id":"APPLY-BANK","lifecycle_scope":"PREP","selection_authority":"ROOT","group_id":"1","prep_stage":"PREP-3_EXPAND_NORMALIZE"}
        aap=self.base/"bank-apply.assignment.yaml"; arp=self.base/"bank-apply.result.yaml"; put(aap,a_assignment); put(arp,applicability); self.call("assign","--root",str(workspace),"--run-id","BANK1","--assignment",str(aap)); applied=self.call("accept","--root",str(workspace),"--run-id","BANK1","--assignment",str(aap),"--result",str(arp))
        admission_path=self.base/"bank-admission.yaml"; put(admission_path,admission)
        admitted=self.call("admit-bank","--root",str(workspace),"--run-id","BANK1","--bank",str(bank_path),"--admission",str(admission_path),"--test-fixture")
        self.assertEqual(admitted["status"],"CORE_ADMITTED")
        # Isolate the fault probe from the complete lifecycle fixture so that
        # its extra registered attempt does not change routing obligations.
        capture_workspace=self.base/"bank-capture-work"
        shutil.copytree(workspace,capture_workspace)
        reader={"assignment_id":"A-BANK-CAPTURE","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/P-01",
                "input_snapshot":init["input_snapshot"],"artifact_type":"PRIMARY","output_path":"positions/BANK-CAPTURE.yaml",
                "dependency_hashes":[],"result_id":"BANK-CAPTURE","lifecycle_scope":"CORE",
                "prep_admission_sha256":qs.sha(admission_path.read_bytes())}
        reader_path=self.base/"bank-capture.assignment.yaml"; put(reader_path,reader)
        self.call("assign","--root",str(capture_workspace),"--run-id","BANK1","--assignment",str(reader_path))
        packet=self.base/"bank-capture.context.yaml"
        imported_path=capture_workspace/"runs"/"BANK1"/binding["vector_path"]
        invocation=("context","--root",str(capture_workspace),"--run-id","BANK1","--assignment",str(reader_path),"--output",str(packet))
        proc=source_swap(invocation,imported_path,b'"G": "goal"',b'"G": "post-capture goal"')
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertIn(b"post-capture goal",imported_path.read_bytes())
        self.assertEqual(qs.load_yaml(packet)["goal"],{"result_id":"FOREIGN-VECTOR","sha256":vector_hash,
                                                      "G":"goal","NOT_G":["not goal"]})
        self.assertEqual((run/binding["vector_path"]).read_bytes(),original)
        # BANK admission must lead through the same public CORE → audit →
        # GATE-6 → final release path; imported bytes are retained, never
        # replaced by a synthetic GOAL prelude.
        old_workspace, old_run, old_init = self.workspace, self.run, self.init
        try:
            self.workspace, self.run, self.init = workspace, run, init
            self.test_public_lifecycle_allows_renamed_administrative_gate6_review_path()
        finally:
            self.workspace, self.run, self.init = old_workspace, old_run, old_init


    def test_core_durable_candidate_rejects_mutable_provenance_removal_across_consumers(self) -> None:
        """Every accepted new-mode transaction is classified before Core branching."""
        events=[]
        pair="A-CORE-META\0TRY-1"; prefix="core_propagation:A-CORE-META"

        def mutate(run: Path, kind: str) -> tuple[Path,bytes]:
            if kind.startswith("registry"):
                path=run/"state/assignments.yaml"; doc=qs.load_yaml(path)
                if kind=="registry_scope": doc["registered"][pair]["assignment"].pop("lifecycle_scope")
                elif kind=="registry_admission": doc["registered"][pair]["assignment"]["prep_admission_sha256"]="0"*64
                else: doc["registered"].pop(pair)
            elif kind=="intent_scope":
                path=run/"intents"/(qs.sha(pair.encode()).upper()+".yaml"); doc=qs.load_yaml(path)
                doc["assignment"].pop("lifecycle_scope")
            elif kind.startswith("header_"):
                path=run/"transactions/CORE-META.yaml"; doc=qs.load_yaml(path)
                if kind=="header_status_invalid": doc["status"]="INVALID_STATUS"
                elif kind=="header_run_missing": doc.pop("run_id")
                elif kind=="header_run_wrong": doc["run_id"]="OTHER-RUN"
                elif kind=="header_snapshot_missing": doc.pop("input_snapshot")
                elif kind=="header_snapshot_wrong": doc["input_snapshot"]="0"*64
                elif kind=="header_nonmapping":
                    raw=path.read_bytes(); path.write_bytes(qs.canon_yaml(["not","a","transaction","mapping"])); return path,raw
                else: raise AssertionError(kind)
            else:
                path=run/"transactions/CORE-META.yaml"; doc=qs.load_yaml(path)
                embedded=dict(doc["assignment"]); canonical=dict(embedded)
                if kind=="transaction_scope_missing":
                    embedded.pop("lifecycle_scope"); canonical.pop("lifecycle_scope")
                elif kind=="transaction_scope_prep":
                    embedded["lifecycle_scope"]="PREP"; canonical["lifecycle_scope"]="PREP"
                elif kind=="transaction_raw_embedded_mismatch":
                    canonical["lifecycle_scope"]="PREP"
                elif kind=="transaction_assignment_digest_mismatch":
                    pass
                else:
                    raise AssertionError(kind)
                canonical_raw=qs.canon_yaml(canonical)
                doc["assignment"]=embedded
                doc["assignment_bytes_b64"]=__import__("base64").b64encode(canonical_raw).decode("ascii")
                doc["assignment_sha256"]=("0"*64 if kind=="transaction_assignment_digest_mismatch" else qs.sha(canonical_raw))
            raw=path.read_bytes(); put(path,doc); return path,raw

        def hook(phase: str, case: "QsTest") -> None:
            run=case.run
            kinds=("registry_scope","intent_scope","registry_missing","registry_admission","transaction_scope_missing","transaction_scope_prep","transaction_raw_embedded_mismatch","transaction_assignment_digest_mismatch","header_status_invalid","header_run_missing","header_run_wrong","header_snapshot_missing","header_snapshot_wrong","header_nonmapping")
            def expected_prefix(kind: str) -> str:
                return "core_propagation:transaction_header:CORE-META.yaml" if kind.startswith("header_") else prefix
            if phase=="prepared":
                for kind in kinds:
                    path,raw=mutate(run,kind); expected=expected_prefix(kind)
                    try:
                        validation=case.call("validate","--root",str(case.workspace),"--run-id",run.name,"--phase","core",ok=False)
                        case.assertTrue(any(error.startswith(expected) for error in validation["errors"]))
                        recovered=case.call("recover","--root",str(case.workspace),"--run-id",run.name,ok=False)
                        case.assertIn(expected,recovered["errors"][0])
                        resumed=case.call("resume","--root",str(case.workspace),"--run-id",run.name,ok=False)
                        case.assertIn(expected,resumed["errors"][0])
                        sealed=case.call("seal","--root",str(case.workspace),"--run-id",run.name,"--release-id","MUTATION-"+kind.upper(),"--phase","prepare",ok=False)
                        case.assertEqual(sealed["error"],"cannot_seal_invalid_run")
                        events.append("prepared:"+kind)
                    finally:
                        path.write_bytes(raw)
            elif phase=="sealed":
                for kind in kinds:
                    path,raw=mutate(run,kind); expected=expected_prefix(kind)
                    try:
                        verified=case.call("verify","--root",str(case.workspace),"--run-id",run.name,ok=False)
                        case.assertTrue(any(expected in error for error in verified["errors"]))
                        events.append("sealed:"+kind)
                    finally:
                        path.write_bytes(raw)

        self._core_propagation_hook=hook
        try:
            self.test_goal_admission_is_atomic_replayable_and_tamper_checked()
        finally:
            delattr(self,"_core_propagation_hook")
        kinds=("registry_scope","intent_scope","registry_missing","registry_admission","transaction_scope_missing","transaction_scope_prep","transaction_raw_embedded_mismatch","transaction_assignment_digest_mismatch","header_status_invalid","header_run_missing","header_run_wrong","header_snapshot_missing","header_snapshot_wrong","header_nonmapping")
        self.assertEqual(events,["prepared:"+kind for kind in kinds]+["sealed:"+kind for kind in kinds])

    def test_goal_admission_is_atomic_replayable_and_tamper_checked(self) -> None:
        """Public GOAL PREP-1/2/3 admission creates an exact replay boundary."""
        workspace=self.base/"admission-goal"; goal=self.base/"admission-goal.md"; statement=self.base/"admission-statement.md"
        goal.write_text("goal admission fixture\n",encoding="utf-8"); statement.write_text("approval statement\n",encoding="utf-8")
        init=self.call("init","--root",str(workspace),"--run-id","R1","--mode","GOAL_TO_PLAN","--goal",str(goal),"--material",str(statement),"--test-fixture")
        run=workspace/"runs"/"R1"; snapshot=init["input_snapshot"]
        def common(result_id,role,agent):
            return {"schema_version":"question-space-v4","id":result_id,"run_id":"R1","input_snapshot":snapshot,"author_role":role,"agent_id":agent,"knowledge_status":"DERIVED","workflow_status":"ACCEPTED","source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-10-21T00:00:00Z","updated_at":"2026-10-21T00:00:00Z"}
        vector_assignment={"assignment_id":"A-VECTOR","attempt_id":"TRY-1","role_id":"OP-VECTOR","agent_id":"/root/OP-VECTOR","input_snapshot":snapshot,"artifact_type":"OPERATOR","output_path":"operators/vector.yaml","dependency_hashes":[],"result_id":"VECTOR-001","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR","selection_authority":"ROOT","group_id":"1"}
        vector=common("VECTOR-001","OP-VECTOR","/root/OP-VECTOR")
        vector.update({"assignment_id":"A-VECTOR","attempt_id":"TRY-1","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR","G":"goal","NOT_G":["not goal"],"value_recipient":"owner","scope":"fixture","success_criteria":["criterion"],"requirements":["R-001"],"requirement_definitions":[{"id":"R-001","definition":"bound"}],"hard_constraints":["64 MiB"],"changeable_assumptions":["choice"],"current_state":"research","resources":"local","authority":"owner","owner_decisions":[{"id":"OD-1","decision_needed":"approve","why_owner_only":"owner","known_context":"fixture","answer_effect":"continue"}],"open_research_questions":["unknown"]})
        vap=self.base/"admit-vector-assignment.yaml"; vrp=self.base/"admit-vector-result.yaml"; put(vap,vector_assignment); put(vrp,vector)
        vector_accept=self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(vap)); self.assertEqual(vector_accept["status"],"ASSIGNED")
        vector_accept=self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(vap),"--result",str(vrp))
        approval={"schema_version":"question-space-v4","approval_id":"APP-1","run_id":"R1","input_snapshot":snapshot,"vector_result_id":"VECTOR-001","vector_sha256":vector_accept["content_hash"],"vector_revision":"1","decision":"APPROVE","approval_mode":"SIMULATED_TEST","approver_id":"ROOT-AS-SIMULATED_TEST_OWNER","authority_basis":"fixture","observation_provenance":"fixture-1","statement_path":"input/materials/001-admission-statement.md","statement_sha256":qs.sha(statement.read_bytes()),"created_at":"2026-10-21T00:00:00Z","fixture_only":"YES"}
        approval_path=self.base/"admit-approval.yaml"; put(approval_path,approval); approved=self.call("approve-vector","--root",str(workspace),"--run-id","R1","--approval",str(approval_path))
        rows=[]
        for number in range(1,11):
            role=f"P-{number:02d}"; profile={"role_id":role,"agent_id":"/root/"+role,"title":role,"competence":f"competence {number}","analysis_scope":f"scope {number}","unique_contribution":f"contribution {number}","boundary":f"boundary {number}","plausible_blindspots":f"blindspot {number}"}
            rows.append({**profile,"profile_sha256":qs.sha(qs.canon_yaml(profile))})
        perspective={"schema_version":"question-space-v4","id":"MAP-1","run_id":"R1","input_snapshot":snapshot,"vector_result_id":"VECTOR-001","vector_sha256":vector_accept["content_hash"],"approval_sha256":approved["approval_sha256"],"selection_authority":"ROOT","perspectives":rows}
        perspective_path=self.base/"admit-map.yaml"; put(perspective_path,perspective); map_out=self.call("publish-perspective-map","--root",str(workspace),"--run-id","R1","--perspective-map",str(perspective_path))
        frame_bindings=[]; all_initial=[]
        for number,row in enumerate(rows,1):
            role=row["role_id"]; result_id=f"FRAME-P-{number:02d}"; assignment={"assignment_id":f"A-{role}","attempt_id":"TRY-1","role_id":role,"agent_id":row["agent_id"],"input_snapshot":snapshot,"artifact_type":"FRAMING","output_path":f"positions/{role}.yaml","dependency_hashes":[],"result_id":result_id,"lifecycle_scope":"PREP","prep_stage":"PREP-2_PERSPECTIVES","goal_result_id":"VECTOR-001","vector_sha256":vector_accept["content_hash"],"approval_sha256":approved["approval_sha256"],"perspective_profile":{key:row[key] for key in ("role_id","agent_id","title","competence","analysis_scope","unique_contribution","boundary","plausible_blindspots")},"perspective_profile_hash":row["profile_sha256"],"role_instruction_paths":["references/roles/perspectives.md"],"allowed_reads":["references/invariants.md","references/preparation.md"],"assigned_ids":{},"selection_authority":"ROOT","group_id":"1"}
            ap=self.base/f"admit-{role}.assignment.yaml"; put(ap,assignment); self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(ap))
            context=self.base/f"admit-{role}.context.yaml"; context_out=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(ap),"--output",str(context))
            questions=[]
            for ix in range(1,11):
                qid=f"IQ-{role}-{ix:02d}"; all_initial.append((qid,result_id,role)); questions.append({"id":qid,"unknown":f"unknown {qid}","why_material":f"material {qid}","changeable_decision":f"decision {qid}","applicability":"goal","evidence_needed":f"evidence {qid}","g_refs":["G-1"],"r_refs":["R-001"],"not_g_refs":[],"origin_role":role})
            framing=common(result_id,role,row["agent_id"]); framing.update({"assignment_id":assignment["assignment_id"],"attempt_id":"TRY-1","artifact_type":"FRAMING","role_id":role,"lifecycle_scope":"PREP","prep_stage":"PREP-2_PERSPECTIVES","perspective_profile_hash":row["profile_sha256"],"vector_result_id":"VECTOR-001","vector_sha256":vector_accept["content_hash"],"approval_sha256":approved["approval_sha256"],"context_packet_sha256":context_out["sha256"],"blind_input":{"contains_bank":"NO","contains_foreign_p_results":"NO","contains_preferred_solution":"NO","allowed":["context packet","own assignment"],"protocol":"fixture"},"initial_questions":questions,"iceberg_position":{"position_id":"ICEPOS-"+role,"origin_role":role,"origin_assignment_id":assignment["assignment_id"],"input_snapshot":snapshot,"elements":[{"seed_id":"SEED-"+role,"candidate_view":"CURRENT_REALITY","element_kind":"ASSUMPTION","statement":"bounded blind assumption for "+role,"scope":"fixture goal","g_refs":["VECTOR-001"],"r_refs":["R-001"],"materiality":"changes a bounded decision","knowledge_status":"HYPOTHESIS","source_refs":[],"finite_check":{"check_kind":"OBSERVATION","procedure":"perform one bounded observation","discriminating_outcomes":["supports","refutes"]}}],"relations":[]}})
            rp=self.base/f"admit-{role}.result.yaml"; put(rp,framing); accepted=self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(ap),"--result",str(rp))
            frame_bindings.append({"result_id":result_id,"sha256":accepted["content_hash"],"role_id":role,"agent_id":row["agent_id"]})
        # Before PREP-4 admission no bank exists.  PREP-3 therefore receives
        # only explicitly assigned initial questions from immutable accepted
        # FRAMING records, with their exact source result/hash bindings.
        frame_hashes={item["result_id"]:item["sha256"] for item in frame_bindings}
        expansion_assignment={"assignment_id":"A-EXPAND","attempt_id":"TRY-1","role_id":"OP-DIVERGENCE","agent_id":"/root/OP-DIVERGENCE","input_snapshot":snapshot,"artifact_type":"OPERATOR","output_path":"operators/EXPAND-001.yaml","dependency_hashes":[frame_hashes["FRAME-P-01"],frame_hashes["FRAME-P-02"]],"dependency_result_ids":["FRAME-P-01","FRAME-P-02"],"result_id":"EXPAND-001","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","goal_result_id":"VECTOR-001","assigned_ids":{"questions":["IQ-P-01-01","IQ-P-02-01"],"requirements":["R-001"],"decisions":[],"evidence":[]},"selection_authority":"ROOT","group_id":"1"}
        expansion_assignment_path=self.base/"admit-expand.assignment.yaml"; put(expansion_assignment_path,expansion_assignment)
        self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(expansion_assignment_path))
        expansion_context_path=self.base/"admit-expand.context.yaml"; expansion_context=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(expansion_assignment_path),"--output",str(expansion_context_path))
        expansion_packet=qs.load_yaml(expansion_context_path)
        self.assertEqual(expansion_packet["packet_type"],"PREP_EXPANSION_CONTEXT")
        self.assertEqual(expansion_packet["bank"],"ABSENT")
        self.assertEqual([item["id"] for item in expansion_packet["questions"]],["IQ-P-01-01","IQ-P-02-01"])
        self.assertEqual([item["source_result_id"] for item in expansion_packet["initial_question_sources"]],["FRAME-P-01","FRAME-P-02"])
        self.assertEqual(expansion_packet["goal"]["requirement_definitions"],[{"id":"R-001","definition":"bound"}])
        self.assertEqual(expansion_packet["goal"]["hard_constraints"],["64 MiB"])
        self.assertEqual(expansion_packet["goal"]["resources"],"local")
        self.assertEqual(expansion_packet["goal"]["authority"],"owner")
        self.assertEqual(expansion_packet["dependency_projection_version"],qs.CONTEXT_PROJECTION_VERSION)
        projected={row["source"]["result_id"]:row for row in expansion_packet["accepted_dependencies"]}
        self.assertEqual(set(projected),{"FRAME-P-01","FRAME-P-02"})
        self.assertEqual([row["id"] for row in projected["FRAME-P-01"]["projection"]["view"]["initial_questions"]],["IQ-P-01-01"])
        self.assertEqual([row["id"] for row in projected["FRAME-P-02"]["projection"]["view"]["initial_questions"]],["IQ-P-02-01"])
        self.assertNotIn("document",projected["FRAME-P-01"]["projection"]["view"])
        self.assertNotIn("IQ-P-01-02",qs.canon_yaml(expansion_packet).decode("utf-8"))
        # The same assignment is immutable: asking for a fuller context cannot
        # overwrite the already-issued minimal packet.
        reread=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(expansion_assignment_path),"--output",str(self.base/"admit-expand-reread.context.yaml"),"--expand","FRAME-P-01",ok=False)
        self.assertEqual(reread["error"],"context_packet_conflict")
        # A readset revision is a new registered assignment. Its explicit,
        # hash-linked expansion may receive the complete selected source only.
        expanded_assignment={**expansion_assignment,"assignment_id":"A-EXPAND-FULL","result_id":"EXPAND-FULL","output_path":"operators/EXPAND-FULL.yaml","dependency_hashes":[frame_hashes["FRAME-P-01"]],"dependency_result_ids":["FRAME-P-01"],"assigned_ids":{"questions":["IQ-P-01-01"],"requirements":["R-001"],"decisions":[],"evidence":[]}}
        expanded_path=self.base/"admit-expand-full.assignment.yaml"; put(expanded_path,expanded_assignment)
        self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(expanded_path))
        expanded_context_path=self.base/"admit-expand-full.context.yaml"; self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(expanded_path),"--output",str(expanded_context_path),"--expand","FRAME-P-01")
        expanded_packet=qs.load_yaml(expanded_context_path)
        full_view=expanded_packet["accepted_dependencies"][0]["projection"]["view"]
        self.assertEqual(full_view["kind"],"FULL_LINKED_EXPANSION")
        self.assertEqual(len(full_view["document"]["initial_questions"]),10)
        unlinked_assignment={**expanded_assignment,"assignment_id":"A-EXPAND-UNLINKED","result_id":"EXPAND-UNLINKED","output_path":"operators/EXPAND-UNLINKED.yaml"}
        unlinked_path=self.base/"admit-expand-unlinked.assignment.yaml"; put(unlinked_path,unlinked_assignment)
        self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(unlinked_path))
        unlinked=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(unlinked_path),"--output",str(self.base/"admit-expand-unlinked.context.yaml"),"--expand","FRAME-P-02",ok=False)
        self.assertEqual(unlinked["error"],"context_expansion_not_linked")
        expansion_result=common("EXPAND-001","OP-DIVERGENCE","/root/OP-DIVERGENCE")
        expansion_result.update({"assignment_id":"A-EXPAND","attempt_id":"TRY-1","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","context_packet_sha256":expansion_context["sha256"]})
        expansion_result_path=self.base/"admit-expand.result.yaml"; put(expansion_result_path,expansion_result)
        self.assertEqual(self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(expansion_assignment_path),"--result",str(expansion_result_path))["status"],"ACCEPTED")
        bad_expansion={**expansion_assignment,"assignment_id":"A-EXPAND-BAD","result_id":"EXPAND-BAD","output_path":"operators/EXPAND-BAD.yaml","dependency_hashes":[],"dependency_result_ids":[]}
        bad_expansion_path=self.base/"admit-expand-bad.assignment.yaml"; put(bad_expansion_path,bad_expansion)
        self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(bad_expansion_path))
        bad_context=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(bad_expansion_path),"--output",str(self.base/"admit-expand-bad.context.yaml"),ok=False)
        self.assertEqual(bad_context["error"],"prep_expansion_source_dependency_missing")
        evidence_assignment={"assignment_id":"A-EVIDENCE","attempt_id":"TRY-1","role_id":"P-01","agent_id":"/root/P-01","input_snapshot":snapshot,"artifact_type":"EVIDENCE","output_path":"evidence/E-001.yaml","dependency_hashes":[],"result_id":"E-001","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","selection_authority":"ROOT","group_id":"1"}
        evidence=common("E-001","P-01","/root/P-01"); evidence.update({"assignment_id":"A-EVIDENCE","attempt_id":"TRY-1","artifact_type":"EVIDENCE","lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","tool_id":"FIXTURE-TOOL","tool_version":"1","policy_hash":"FIXTURE-POLICY","exposure_hash":"FIXTURE-EXPOSURE","freshness":"CURRENT-AS-DECLARED","changed":"UNCHANGED"})
        eap=self.base/"admit-evidence.assignment.yaml"; erp=self.base/"admit-evidence.result.yaml"; put(eap,evidence_assignment); put(erp,evidence); self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(eap)); self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(eap),"--result",str(erp))
        questions=[]
        # This structural fixture normalizes the 100 accepted initial unknowns
        # into three explicit research groups.  Every source remains listed;
        # none is padded, discarded, or invented.
        for index, group in enumerate((all_initial[0::3], all_initial[1::3], all_initial[2::3]),1):
            source_ids=[item[0] for item in group]
            source_results=sorted({item[1] for item in group})
            source_roles=sorted({item[2] for item in group})
            questions.append({"id":f"Q-{index:03d}","text":f"normalized research group {index}","source_offset":str(index),"unknown_key":f"normalized-group-{index}","mechanism":"researchable normalized mechanism","applicability":"goal","changeable_decision":f"decision group {index}","evidence_needed":f"evidence group {index}","lineage":{"operation":"SEMANTIC_MERGE","source_question_ids":source_ids,"source_result_ids":source_results,"source_roles":source_roles,"reason":"fixture normalization retains every distinct initial unknown with addressable provenance"},"g_refs":["G-1"],"r_refs":["R-001"],"not_g_refs":[]})
        bank={"schema_version":"bank-v1","questions":questions,"requirements":["R-001"]}; bank_path=self.base/"admit-bank.yaml"; put(bank_path,bank); bank_hash=qs.sha(bank_path.read_bytes())
        source_ids=["VECTOR_NO_DRIFT","PERSPECTIVES_DISTINCT_NECESSARY","EACH_R","EACH_MATERIAL_NOT_G","ALTERNATIVES","COMMON_UNPROVED_ASSUMPTIONS","INTERACTIONS","RESEARCHABILITY","CHANGEABLE_DECISIONS","CENTRAL_MECHANISM_SUFFICIENCY"]
        source_checks=[]
        for check_id in source_ids:
            row={"check_id":check_id,"outcome":"PASS","basis":f"Root basis {check_id}","evidence_refs":["E-001"],"targeted_repair_assignments":[]}
            if check_id=="PERSPECTIVES_DISTINCT_NECESSARY": row.update({"root_semantic_outcome":"PASS","perspective_assessments":[{"role_id":item["role_id"],"competence":item["competence"],"analysis_scope":item["analysis_scope"],"unique_contribution":item["unique_contribution"],"boundary":item["boundary"],"necessity":f"necessary {item['role_id']}","basis_refs":["E-001"]} for item in rows],"material_overlap_assessments":[]})
            if check_id=="COMMON_UNPROVED_ASSUMPTIONS": row.update({"root_semantic_outcome":"PASS","detected_assumptions":[],"none_with_basis":{"outcome":"NONE_WITH_BASIS","basis_refs":["E-001"]}})
            source_checks.append(row)
        admission={"schema_version":"prep-admission-v1","id":"ADMISSION-1","run_id":"R1","input_snapshot":snapshot,"input_mode":"GOAL_TO_PLAN","goal_sha256":qs.sha((run/"input/goal.md").read_bytes()),"vector_binding":{"result_id":"VECTOR-001","sha256":vector_accept["content_hash"]},"approval_binding":{"id":"APP-1","sha256":approved["approval_sha256"]},"perspective_map_binding":{"id":"MAP-1","sha256":map_out["perspective_map_sha256"]},"initial_set_bindings":frame_bindings,"expansion_bindings":[],"normalization_decisions":[{"id":"N-1","decision":"retain exact lineage"}],"bank_path":"input/bank.yaml","bank_sha256":bank_hash,"requirements_sha256":qs.sha(qs.canon_yaml(["R-001"])),"question_count":"3","size_assessment":{"classification":"BELOW_TARGET_WITH_RATIONALE","rationale":"fixture normalizes all one hundred initial unknowns into three addressable groups without padding"},"source_checks":source_checks,"uncovered_areas":[],"generation_performed":"YES","status":"ADMITTED"}
        admission_path=self.base/"admission.yaml"; put(admission_path,admission)
        broken={"id":"ADMISSION-BROKEN"}; broken_path=self.base/"admission-broken.yaml"; put(broken_path,broken)
        self.call("admit-bank","--root",str(workspace),"--run-id","R1","--bank",str(bank_path),"--admission",str(broken_path),"--test-fixture",ok=False)
        self.assertFalse((run/"input/bank.yaml").exists())
        admitted=self.call("admit-bank","--root",str(workspace),"--run-id","R1","--bank",str(bank_path),"--admission",str(admission_path),"--test-fixture")
        self.assertEqual(admitted["status"],"CORE_ADMITTED")
        admission_hash=admitted["prep_admission_sha256"]
        missing_core={"assignment_id":"A-CORE-MISSING","attempt_id":"TRY-1","role_id":"OP-INDULGENCE","agent_id":"/root/OP-INDULGENCE","input_snapshot":snapshot,"artifact_type":"OPERATOR","output_path":"operators/CORE-MISSING.yaml","dependency_hashes":[],"result_id":"CORE-MISSING"}
        missing_core_path=self.base/"core-missing.assignment.yaml"; put(missing_core_path,missing_core)
        self.assertEqual(self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(missing_core_path),ok=False)["error"],"new_mode_lifecycle_scope_required")
        core_assignment={**missing_core,"assignment_id":"A-CORE-META","result_id":"CORE-META","output_path":"operators/CORE-META.yaml","lifecycle_scope":"CORE","prep_admission_sha256":admission_hash,"selection_authority":"ROOT","group_id":"1","assigned_ids":{"questions":[],"requirements":[],"decisions":[],"evidence":[]}}
        core_assignment_path=self.base/"core-meta.assignment.yaml"; put(core_assignment_path,core_assignment)
        self.call("assign","--root",str(workspace),"--run-id","R1","--assignment",str(core_assignment_path))
        self.call("launch-intent","--root",str(workspace),"--run-id","R1","--assignment-id","A-CORE-META","--attempt-id","TRY-1","--state","READY_TO_DISPATCH")
        core_context_path=self.base/"core-meta.context.yaml"; self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(core_assignment_path),"--output",str(core_context_path))
        core_context=qs.load_yaml(core_context_path); self.assertEqual((core_context["lifecycle_scope"],core_context["prep_admission_sha256"]),("CORE",admission_hash))
        core_result=common("CORE-META","OP-INDULGENCE","/root/OP-INDULGENCE"); core_result.update({"assignment_id":"A-CORE-META","attempt_id":"TRY-1","artifact_type":"OPERATOR","lifecycle_scope":"CORE","prep_admission_sha256":admission_hash})
        wrong_result={**core_result,"prep_admission_sha256":"0"*64}; wrong_result_path=self.base/"core-meta-wrong.result.yaml"; put(wrong_result_path,wrong_result)
        self.assertEqual(self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(core_assignment_path),"--result",str(wrong_result_path),ok=False)["error"],"result_prep_admission_binding")
        core_result_path=self.base/"core-meta.result.yaml"; put(core_result_path,core_result); self.call("accept","--root",str(workspace),"--run-id","R1","--assignment",str(core_assignment_path),"--result",str(core_result_path))
        pair="A-CORE-META\0TRY-1"; assignment_state=qs.load_yaml(run/"state/assignments.yaml"); launch_state=qs.load_yaml(run/"state/launch-intents.yaml"); transaction=qs.load_yaml(run/"transactions/CORE-META.yaml")
        for row in (assignment_state["registered"][pair],launch_state["records"][pair],transaction,transaction["assignment"],transaction["entry"]): self.assertEqual((row["lifecycle_scope"],row["prep_admission_sha256"]),("CORE",admission_hash))
        launch_path=run/"state/launch-intents.yaml"; launch_raw=launch_path.read_bytes(); launch_bad=qs.load_yaml(launch_path); launch_bad["records"][pair].pop("prep_admission_sha256"); put(launch_path,launch_bad)
        launch_failure=self.call("validate","--root",str(workspace),"--run-id","R1","--phase","core",ok=False); self.assertIn("core_propagation:A-CORE-META:launch",launch_failure["errors"]); launch_path.write_bytes(launch_raw)
        intent_path=run/"intents"/(qs.sha(pair.encode()).upper()+".yaml"); intent_raw=intent_path.read_bytes(); intent_bad=qs.load_yaml(intent_path); intent_bad.pop("prep_admission_sha256"); put(intent_path,intent_bad)
        recover_failure=self.call("recover","--root",str(workspace),"--run-id","R1",ok=False); self.assertIn("core_propagation:A-CORE-META:intent",recover_failure["errors"][0]); intent_path.write_bytes(intent_raw)
        core_context_stored=run/"state/contexts/A-CORE-META--TRY-1.yaml"; core_context_raw=core_context_stored.read_bytes(); core_context_bad=qs.load_yaml(core_context_stored); core_context_bad["prep_admission_sha256"]="0"*64; put(core_context_stored,core_context_bad)
        context_failure=self.call("validate","--root",str(workspace),"--run-id","R1","--phase","core",ok=False); self.assertIn("core_propagation:A-CORE-META:context",context_failure["errors"]); core_context_stored.write_bytes(core_context_raw)
        transaction_path=run/"transactions/CORE-META.yaml"; transaction_raw=transaction_path.read_bytes(); transaction_bad=qs.load_yaml(transaction_path); transaction_bad.pop("lifecycle_scope"); put(transaction_path,transaction_bad)
        transaction_failure=self.call("validate","--root",str(workspace),"--run-id","R1","--phase","core",ok=False); self.assertIn("core_propagation:A-CORE-META:transaction",transaction_failure["errors"]); transaction_path.write_bytes(transaction_raw)
        source_path=run/"positions/P-01.yaml"; source_raw=source_path.read_bytes(); source_path.write_text("tampered source\n",encoding="utf-8")
        drift=self.call("context","--root",str(workspace),"--run-id","R1","--assignment",str(expanded_path),"--output",str(self.base/"admit-expand-drift.context.yaml"),ok=False); self.assertEqual(drift["error"],"immutable_result_tamper:FRAME-P-01"); source_path.write_bytes(source_raw)
        stored_context=run/"state/contexts/A-P-01--TRY-1.yaml"; original_context=stored_context.read_bytes(); stored_context.write_text("tampered context\n",encoding="utf-8")
        context_tamper=self.call("validate","--root",str(workspace),"--run-id","R1","--phase","core",ok=False)
        self.assertIn("prep:framing_context:FRAME-P-01:hash_mismatch",context_tamper["errors"])
        stored_context.write_bytes(original_context)
        after=(run/"state/current.yaml").read_bytes(); tx=qs.load_yaml(next((run/"transactions/admissions").glob("*.yaml")))
        expected=__import__("base64").b64decode(tx["expected_current"]["bytes_b64"])
        (run/"input/bank.yaml").unlink(); (run/"input/prep-admission.yaml").unlink(); (run/"state/current.yaml").write_bytes(expected)
        replay=self.call("recover","--root",str(workspace),"--run-id","R1")
        self.assertEqual(replay["status"],"RECOVERED"); self.assertEqual((run/"state/current.yaml").read_bytes(),after)
        # Reuse the admitted PREP-2 blind positions in the complete public
        # core -> audit -> GATE-6 -> release lifecycle.  The called fixture
        # exercises only public CLI operations and sets CORE bindings on every
        # post-admission assignment/result.
        saved_workspace,saved_run,saved_init=self.workspace,self.run,self.init
        self.workspace,self.run,self.init=workspace,run,init
        try:
            self.test_public_lifecycle_allows_renamed_administrative_gate6_review_path()
        finally:
            self.workspace,self.run,self.init=saved_workspace,saved_run,saved_init
        original_bank=(run/"input/bank.yaml").read_bytes(); (run/"input/bank.yaml").write_bytes(b"tampered\n")
        self.call("recover","--root",str(workspace),"--run-id","R1",ok=False); (run/"input/bank.yaml").write_bytes(original_bank)

if __name__ == "__main__": unittest.main(verbosity=2)
