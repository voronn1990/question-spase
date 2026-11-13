"""Ordinary CLI controls for captured commands and durable result publication."""

import json
import shutil
import subprocess
import sys
import unittest

import test_qs


CRASH_SCRIPT = """
import importlib.util
import os
import pathlib
import sys

source, boundary, *argv = sys.argv[1:]
spec = importlib.util.spec_from_file_location("qs_fault_subject", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
atomic = module.atomic

def fault(path, raw):
    if boundary == "before_marker" and path.parent.name == "transactions":
        os._exit(70)
    if boundary == "after_marker" and path.parent.name == "positions":
        os._exit(71)
    if boundary == "before_launch_index" and path.name == "launch-intents.yaml":
        os._exit(73)
    if boundary == "before_context_revision_packet" and "state" in path.parts and path.name == "00000002.yaml":
        os._exit(74)
    atomic(path, raw)
    if boundary == "before_ack" and path.name == "events.yaml":
        os._exit(72)

module.atomic = fault
raise SystemExit(module.main(argv))
"""


def build_portable_fixture(fixture, *, transitive=False):
    """Public C01 two-task source reused for directory and archive controls."""
    def command(operation, *options):
        return (operation, "--root", str(fixture.workspace), "--run-id", fixture.run.name, *options)

    evidence=fixture.common("E-LATE","P-04","/root/evidence")
    evidence["changed"]="UNCHANGED"
    if transitive:
        source = "input/materials/Данные проверки.txt"
        material = fixture.run / source
        material.parent.mkdir()
        material.write_bytes("проверяемые данные\n".encode("utf-8"))
        evidence["source_refs"] = [source]
    accepted=fixture.register_and_accept("late","P-04","EVIDENCE","evidence/E-LATE.yaml",evidence,"/root/evidence")
    vector=fixture.common("VECTOR-PORT","OP-VECTOR","/root/vector")
    vector.update({"G":"bounded goal","NOT_G":["outside scope"]})
    fixture.register_and_accept("vector-port","OP-VECTOR","OPERATOR","operators/VECTOR-PORT.yaml",vector,"/root/vector")
    early=fixture.common("E-EARLY","P-04","/root/evidence")
    early_assignment={"assignment_id":"A-EARLY","attempt_id":"TRY-1","role_id":"P-04",
                      "agent_id":early["agent_id"],"input_snapshot":fixture.init["input_snapshot"],
                      "artifact_type":"EVIDENCE","output_path":"evidence/E-EARLY.yaml",
                      "result_id":"E-EARLY","goal_result_id":"VECTOR-PORT",
                      "dependency_result_ids":["E-LATE"],"dependency_hashes":[accepted["content_hash"]]}
    early_path=fixture.base/"early-assignment.yaml"
    test_qs.put(early_path,early_assignment)
    fixture.call(*command("assign","--assignment",str(early_path)))
    fixture.call(*command("context","--assignment",str(early_path),"--output",str(fixture.base/"early-bounded.yaml")))
    expanded=fixture.base/"early-expanded.yaml"
    expanded_receipt=fixture.call(*command("context","--assignment",str(early_path),"--output",str(expanded),"--expand","E-LATE"))
    early.update({"changed":"UNCHANGED","context_packet_sha256":expanded_receipt["sha256"],
                  **{field:early_assignment[field] for field in ("assignment_id","attempt_id","artifact_type")}})
    result=fixture.base/"early-result.yaml"
    test_qs.put(result,early)
    early_accepted=fixture.call(*command("commit","--assignment",str(early_path),"--result",str(result)))
    plan=fixture.common("PLAN-L","PLAN-AUTHOR","/root/planner")
    plan.update({"plan_version":"fixture", "identity_result":{"stage_id":"STAGE-L"},
                 "dependency_edges":[{"from_id":"E-LATE","to_id":"PLAN-L"}],
                 "local_execution_graph":{"entry":"START","exit":"END","edges":[["T-EARLY","T-LATE"]]},
                 "tasks":[{"id":"T-EARLY","checks":["C-EARLY"],"e_refs":["E-EARLY"],
                           "fixture_action":{"operation":"ASSERT_EVIDENCE_UNCHANGED",
                                             "result_id":"E-EARLY","sha256":early_accepted["content_hash"]}},
                          {"id":"T-LATE","checks":["C-LATE"],"e_refs":["E-LATE"],
                           "dependencies":["T-EARLY"]}]})
    plan_commit = fixture.register_and_accept("plan","PLAN-AUTHOR","PLAN","plan/PLAN-L.yaml",plan,"/root/planner")
    if transitive:
        review = fixture.common("REVIEW-LATE", "P-05", "/root/reviewer")
        review.update({
            "target_result_id": "E-LATE", "target_hash": accepted["content_hash"],
            "outcome": "PASS", "review_kind": "SUBJECT", "check_provenance": "source hash and scope",
            "risk_coverage": "the late evidence contains the approved fixture observation",
        })
        fixture.register_and_accept("review-late", "P-05", "REVIEW", "reviews/REVIEW-LATE.yaml", review, review["agent_id"])
    return accepted, early_accepted, expanded, plan_commit


class CommandAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_qs.QsTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def command(self, operation, *options):
        return (operation, "--root", str(self.fixture.workspace),
                "--run-id", self.fixture.run.name, *options)

    def pending(self, *, goal_id=None, dependencies=(), occurrence=None, label="CUT", kind="PRIMARY", register=True):
        fixture = self.fixture
        result_id="RESULT-"+label
        relative=("answers/source-outcomes/" if kind=="SOURCE_OUTCOMES" else "positions/")+result_id+".yaml"
        assignment = {
            "assignment_id": "A-"+label, "attempt_id": "TRY-1", "role_id": "P-01",
            "agent_id": "/root/cut", "input_snapshot": fixture.init["input_snapshot"],
            "artifact_type": kind, "output_path": relative,
            "dependency_hashes": [], "result_id": result_id,
        }
        if goal_id is not None:
            assignment["goal_result_id"] = goal_id
        assignment["dependency_result_ids"] = list(dependencies)
        if occurrence is not None:
            assignment["logical_occurrence_id"] = occurrence
            assignment["launch_state"] = "READY_TO_DISPATCH"
        result = fixture.common(result_id, "P-01", "/root/cut")
        if kind=="SOURCE_OUTCOMES": result["records"]=[]
        result.update({key: assignment[key] for key in ("assignment_id", "attempt_id", "artifact_type")})
        ap, rp = fixture.base / (label+"-assignment.yaml"), fixture.base / (label+"-result.yaml")
        test_qs.put(ap, assignment)
        test_qs.put(rp, result)
        if register: fixture.call(*self.command("assign", "--assignment", str(ap)))
        return ap, rp

    def crash_at(self, boundary, exit_code, marker, published):
        ap, rp = self.pending()
        fixture = self.fixture
        invocation = self.command("commit", "--assignment", str(ap), "--result", str(rp))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", CRASH_SCRIPT, str(test_qs.QS), boundary, *invocation],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, exit_code, proc.stdout + proc.stderr)
        transaction = fixture.run / "transactions/RESULT-CUT.yaml"
        output = fixture.run / "positions/RESULT-CUT.yaml"
        self.assertEqual(transaction.is_file(), marker)
        self.assertEqual(output.is_file(), published)
        first = fixture.call(*self.command("recover"))
        projections = [fixture.run / "state" / name for name in ("assignments.yaml", "events.yaml")]
        stamps = [path.stat().st_mtime_ns for path in projections]
        second = fixture.call(*self.command("recover"))
        self.assertEqual([path.stat().st_mtime_ns for path in projections], stamps)
        expected = ["RESULT-CUT"] if marker else []
        self.assertEqual(first["accepted_result_ids"], expected)
        self.assertEqual(second["accepted_result_ids"], expected)
        self.assertEqual(output.is_file(), marker)
        if marker:
            self.assertEqual(output.read_bytes(), rp.read_bytes())
            prior = transaction.read_bytes()
            self.assertEqual(fixture.call(*invocation)["status"], "IDEMPOTENT")
            self.assertEqual(transaction.read_bytes(), prior)

    def test_crash_before_durable_marker(self):
        self.crash_at("before_marker", 70, False, False)

    def test_crash_after_marker_before_result_projection(self):
        self.crash_at("after_marker", 71, True, False)

    def test_crash_after_projection_before_ack(self):
        self.crash_at("before_ack", 72, True, True)

    def test_assignment_marker_restores_missing_launch_and_attempt_indexes(self):
        fixture=self.fixture
        ap,_=self.pending(register=False)
        invocation=self.command("assign","--assignment",str(ap))
        crash=subprocess.run([sys.executable,"-B","-c",CRASH_SCRIPT,str(test_qs.QS),"before_launch_index",*invocation],
                             text=True,capture_output=True,check=False)
        self.assertEqual(crash.returncode,73,crash.stdout+crash.stderr)
        self.assertEqual(len(list((fixture.run/"intents").glob("*.yaml"))),1)
        self.assertEqual(fixture.call(*self.command("recover"))["pending_assignments"],["A-CUT"])
        state=test_qs.qs.load_yaml(fixture.run/"state/assignments.yaml")
        self.assertEqual(state["current_attempts"]["A-CUT"],"TRY-1")
        self.assertEqual(fixture.call(*invocation)["status"],"IDEMPOTENT")

    def test_launch_transition_marker_recovers_unknown_before_projection(self):
        fixture=self.fixture
        self.pending()
        invocation=self.command("launch-intent","--assignment-id","A-CUT","--attempt-id","TRY-1","--state","DISPATCH_UNKNOWN")
        crash=subprocess.run([sys.executable,"-B","-c",CRASH_SCRIPT,str(test_qs.QS),"before_launch_index",*invocation],
                             text=True,capture_output=True,check=False)
        self.assertEqual(crash.returncode,73,crash.stdout+crash.stderr)
        self.assertEqual(len(list((fixture.run/"transactions/launch").rglob("*.yaml"))),1)
        fixture.call(*self.command("recover"))
        state=test_qs.qs.load_yaml(fixture.run/"state/assignments.yaml")
        self.assertEqual(state["registered"]["A-CUT\0TRY-1"]["launch_state"],"DISPATCH_UNKNOWN")
        self.assertEqual(fixture.call(*invocation)["launch_state"],"DISPATCH_UNKNOWN")

    def test_modern_assign_replay_rejects_forged_receipt(self):
        fixture=self.fixture
        goal=fixture.base/"goal.md"
        goal.write_text("GOAL assignment replay control")
        root=fixture.base/"goal-workspace"
        initial=fixture.call("init","--root",str(root),"--run-id","GOAL1",
                             "--mode","GOAL_TO_PLAN","--goal",str(goal),"--test-fixture")
        run=root/"runs/GOAL1"
        assignment={"assignment_id":"A-VECTOR","attempt_id":"TRY-1","role_id":"OP-VECTOR",
                    "agent_id":"/test/vector","input_snapshot":initial["input_snapshot"],
                    "artifact_type":"OPERATOR","output_path":"operators/vector.yaml",
                    "dependency_hashes":[],"result_id":"VECTOR-001",
                    "lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR"}
        source=fixture.base/"vector-assignment.yaml"
        test_qs.put(source,assignment)
        argv=("assign","--root",str(root),"--run-id","GOAL1","--assignment",str(source))
        receipt=fixture.call(*argv)
        self.assertEqual(fixture.call(*argv),receipt)
        intent=next((run/"intents").glob("*.yaml"))
        saved=test_qs.qs.load_yaml(intent)
        for field,value in (("status","FORGED"),("scope","FORGED"),("assignment_id","OTHER")):
            with self.subTest(field=field):
                test_qs.put(intent,{**saved,"receipt":{**receipt,field:value}})
                paths=[intent,run/"state/current.yaml",run/"state/assignments.yaml",
                       run/"state/launch-intents.yaml",run/"state/events.yaml"]
                before=[path.read_bytes() for path in paths]
                self.assertEqual(fixture.call(*argv,ok=False)["error"],"assignment_intent_receipt_binding")
                self.assertEqual([path.read_bytes() for path in paths],before)
        test_qs.put(intent,saved)
        self.assertEqual(fixture.call(*argv),receipt)

    def test_launch_rejects_unjournaled_ready_projection_and_claim(self):
        fixture=self.fixture
        ap,_=self.pending(occurrence="OCC-LOCAL-A",register=False)
        assignment=test_qs.qs.load_yaml(ap)
        assignment["launch_state"]="NOT_LAUNCHED"
        test_qs.put(ap,assignment)
        fixture.call(*self.command("assign","--assignment",str(ap)))
        pair="A-CUT\0TRY-1"
        launch_path=fixture.run/"state/launch-intents.yaml"
        index_path=fixture.run/"state/assignments.yaml"
        original=(launch_path.read_bytes(),index_path.read_bytes())
        launches=test_qs.qs.load_yaml(launch_path)
        launches["records"][pair]["state"]="READY_TO_DISPATCH"
        test_qs.put(launch_path,launches)
        state=test_qs.qs.load_yaml(index_path)
        state["registered"][pair]["launch_state"]="READY_TO_DISPATCH"
        test_qs.put(index_path,state)
        forged=(launch_path.read_bytes(),index_path.read_bytes())
        invocation=self.command("launch-intent","--assignment-id","A-CUT","--attempt-id","TRY-1",
                                "--state","READY_TO_DISPATCH")
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"launch_transition_projection_conflict")
        self.assertIn("launch_transition_projection_conflict",fixture.call(*self.command("recover"),ok=False)["errors"][0])
        request,_=self.request("claim-unlogged","/root/cut",test_qs.qs.sha(ap.read_bytes()),
                               assignment_id="A-CUT",attempt_id="TRY-1",logical_occurrence_id="OCC-LOCAL-A")
        self.assertEqual(fixture.call(*self.command("claim-effect","--request",str(request)),ok=False)["error"],
                         "launch_transition_projection_conflict")
        self.assertEqual((launch_path.read_bytes(),index_path.read_bytes()),forged)
        launch_path.write_bytes(original[0])
        index_path.write_bytes(original[1])
        receipt=fixture.call(*invocation)
        self.assertEqual(fixture.call(*invocation),receipt)
        marker=next((fixture.run/"transactions/launch").rglob("*.yaml"))
        saved=test_qs.qs.load_yaml(marker)
        altered={**saved,"receipt":{**saved["receipt"],"scope":"FORGED"}}
        altered.pop("record_sha256")
        altered["record_sha256"]=test_qs.qs.sha(test_qs.qs.canon_yaml(altered))
        test_qs.put(marker,altered)
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"launch_transition_receipt_binding")
        self.assertIn("launch_transition_receipt_binding",fixture.call(*self.command("recover"),ok=False)["errors"][0])

    def test_seal_replay_requires_exact_archive(self):
        fixture=self.fixture

        class Checked(Exception):
            pass

        def check(phase,case):
            if phase!="sealed":
                return
            archive=case.run/"releases/REL-2.yaml"
            current=case.run/"state/current.yaml"
            release=case.run/"release.yaml"
            argv=("seal","--root",str(case.workspace),"--run-id",case.run.name,
                  "--release-id","REL-2","--phase","finalize")
            expected=case.call(*argv)
            self.assertEqual(expected["status"],"SEALED")
            raw=archive.read_bytes()
            protected=(current.read_bytes(),release.read_bytes())
            archive.unlink()
            self.assertEqual(case.call(*argv,ok=False)["error"],"sealed_release_archive_missing")
            self.assertEqual((current.read_bytes(),release.read_bytes()),protected)
            self.assertFalse(archive.exists())
            archive.write_bytes(raw)
            self.assertEqual(case.call(*argv),expected)
            raise Checked()

        fixture._core_propagation_hook=check
        with self.assertRaises(Checked):
            fixture.test_public_lifecycle_allows_renamed_administrative_gate6_review_path()

    def test_context_protected_destination_and_alias(self):
        fixture = self.fixture
        vector = fixture.common("VECTOR-CUT", "OP-VECTOR", "/root/vector")
        vector.update({"G": "addressable goal", "NOT_G": ["fixture-only goal"]})
        fixture.register_and_accept("vector", "OP-VECTOR", "OPERATOR",
                                    "operators/VECTOR-CUT.yaml", vector, "/root/vector")
        ap, _ = self.pending(goal_id="VECTOR-CUT")
        protected = fixture.run / "input/snapshot.yaml"
        before = protected.read_bytes()
        assignment_before=ap.read_bytes()
        alias = fixture.base / "context-alias.yaml"
        alias.symlink_to(protected)
        for destination in (protected, alias, ap, fixture.base / "new/../context.yaml"):
            response = fixture.call(
                *self.command("context", "--assignment", str(ap), "--output", str(destination)),
                ok=False,
            )
            self.assertIn("context_output", response["error"])
        self.assertEqual(protected.read_bytes(), before)
        self.assertEqual(ap.read_bytes(), assignment_before)
        packet = fixture.base / "caller-context.yaml"
        fixture.call(*self.command("context", "--assignment", str(ap), "--output", str(packet)))
        self.assertTrue(packet.is_file())
        stored=fixture.run/"state/contexts/A-CUT--TRY-1.yaml"
        original=stored.read_bytes()
        historical=fixture.base/"historical-context.yaml"
        invocation=self.command("context","--assignment",str(ap),"--output",str(historical),"--historical")
        self.assertEqual(fixture.call(*invocation)["sha256"],test_qs.qs.sha(original))
        self.assertEqual(historical.read_bytes(),original)
        modified=test_qs.qs.load_yaml(stored)
        modified["blind"]="YES"
        modified["readset"]=["forged"]
        test_qs.put(stored,modified)
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"historical_context_proof_mismatch")
        self.assertEqual(historical.read_bytes(),original)
        stored.write_bytes(original)
        (fixture.run/"transactions/contexts/A-CUT--TRY-1.yaml").unlink()
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"historical_context_proof_missing")
        self.assertEqual(historical.read_bytes(),original)

    def request(self, key, actor, authority_hash, **fields):
        cut = self.fixture.call(*self.command("command-cut"))
        document = {"key": key, "actor": actor, "authority_sha256": authority_hash,
                    **{field: cut[field] for field in ("run_id", "input_snapshot", "expected_revision")},
                    **fields}
        path = self.fixture.base / (key + "-request.yaml")
        test_qs.put(path, document)
        return path, document

    def test_authenticated_commit_replays_original_receipt_after_fresh_cut(self):
        fixture = self.fixture
        ap, rp = self.pending()
        request, obj = self.request("request-a", "/root/cut", test_qs.qs.sha(ap.read_bytes()),
                                    result_sha256=test_qs.qs.sha(rp.read_bytes()))
        invocation = self.command("commit", "--assignment", str(ap), "--result", str(rp), "--request", str(request))
        first = fixture.call(*invocation)
        self.assertEqual(first["status"], "ACCEPTED")
        self.assertNotEqual(fixture.call(*self.command("command-cut"))["expected_revision"],obj["expected_revision"])
        self.assertEqual(fixture.call(*invocation),first)
        for fields, error in [
            ({"key":"request-b"}, "command_request_revision_conflict"),
            ({"actor":"/root/other"}, "command_request_authority_binding"),
            ({"authority_sha256":"0"*64}, "command_request_authority_binding"),
            ({"expected_revision":"0"*64}, "command_request_conflict"),
        ]:
            test_qs.put(request,{**obj,**fields})
            self.assertEqual(fixture.call(*invocation,ok=False)["error"],error)
        test_qs.put(request,obj)
        transaction=fixture.run/"transactions/RESULT-CUT.yaml"
        marker=test_qs.qs.load_yaml(transaction)
        marker["receipt"]["status"]="FORGED"
        test_qs.put(transaction,marker)
        altered=transaction.read_bytes()
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"command_request_conflict")
        self.assertEqual(fixture.call(*self.command("recover"),ok=False)["errors"],["invalid_transactions"])
        self.assertEqual(transaction.read_bytes(),altered)

    def test_commit_publishes_captured_result_when_origin_changes(self):
        fixture=self.fixture
        ap,rp=self.pending()
        original=rp.read_bytes()
        hook=r"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(sys.argv[1]).parent))
import qs
capture=qs.captured_argument
origin=Path(sys.argv[2])

def replace_after_capture(path,*args,**kwargs):
    captured=capture(path,*args,**kwargs)
    if path==origin:
        origin.write_bytes(b"mutated after capture")
    return captured

qs.captured_argument=replace_after_capture
raise SystemExit(qs.main(sys.argv[3:]))
"""
        invocation=self.command("commit","--assignment",str(ap),"--result",str(rp))
        proc=subprocess.run(
            [sys.executable,"-B","-c",hook,str(test_qs.QS),str(rp),*invocation],
            text=True,capture_output=True,check=False)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["content_hash"],test_qs.qs.sha(original))
        self.assertEqual(rp.read_bytes(),b"mutated after capture")
        self.assertEqual((fixture.run/"positions/RESULT-CUT.yaml").read_bytes(),original)

    def test_request_key_cannot_commit_a_different_result_at_fresh_cut(self):
        fixture=self.fixture
        for label in ("CUT","OTHER"):
            ap,rp=self.pending(label=label)
            request,_=self.request("same-key","/root/cut",test_qs.qs.sha(ap.read_bytes()),
                                   result_sha256=test_qs.qs.sha(rp.read_bytes()))
            invocation=self.command("commit","--assignment",str(ap),"--result",str(rp),"--request",str(request))
            if label=="CUT":
                self.assertEqual(fixture.call(*invocation)["status"],"ACCEPTED")
            else:
                self.assertEqual(fixture.call(*invocation,ok=False)["error"],"command_request_key_conflict")
                self.assertFalse((fixture.run/"positions/RESULT-OTHER.yaml").exists())

    def test_selected_result_recovery_after_projection_before_selector(self):
        fixture=self.fixture
        ap,rp=self.pending(kind="SOURCE_OUTCOMES")
        invocation=self.command("commit","--assignment",str(ap),"--result",str(rp))
        crashed=subprocess.run([sys.executable,"-B","-c",CRASH_SCRIPT,str(test_qs.QS),"before_ack",*invocation],
                               text=True,capture_output=True,check=False)
        self.assertEqual(crashed.returncode,72,crashed.stdout+crashed.stderr)
        before=test_qs.qs.load_yaml(fixture.run/"state/current.yaml")
        self.assertNotIn("current_source_outcomes",before)
        self.assertEqual(fixture.call(*self.command("recover"))["current_source_outcomes"]["result_id"],"RESULT-CUT")
        after=test_qs.qs.load_yaml(fixture.run/"state/current.yaml")
        self.assertEqual(after["current_source_outcomes"]["result_id"],"RESULT-CUT")
        self.assertEqual(fixture.call(*self.command("recover"))["current_source_outcomes"],after["current_source_outcomes"])

    def test_pending_and_disposed_intake_block_direct_stale_commit(self):
        fixture=self.fixture
        base=fixture.common("BASE-A","P-03","/root/base-a")
        fixture.register_and_accept("base-a","P-03","PRIMARY","positions/BASE-A.yaml",base,"/root/base-a")
        ap,rp=self.pending(dependencies=("BASE-A",))
        evidence=fixture.common("E-CHANGE","P-04","/root/evidence")
        evidence["changed"]="CHANGED"
        evidence["command_intake_scope"]=["BASE-A"]
        accepted=fixture.register_and_accept("e-change","P-04","EVIDENCE","evidence/E-CHANGE.yaml",evidence,"/root/evidence")
        path,_=self.request("intake-a","/root/evidence",accepted["content_hash"],
                            authority_result_id="E-CHANGE",evidence_result_id="E-CHANGE")
        intake=fixture.call(*self.command("evidence-intake","--request",str(path)))
        self.assertEqual(intake["status"],"EVIDENCE_PENDING")
        invocation=self.command("commit","--assignment",str(ap),"--result",str(rp))
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"assignment_evidence_pending")
        context=self.command("context","--assignment",str(ap),"--output",str(fixture.base/"stale.yaml"))
        self.assertEqual(fixture.call(*context,ok=False)["error"],"assignment_evidence_pending")
        unaffected_ap,unaffected_rp=self.pending(label="BRANCH-B")
        unaffected=self.command("commit","--assignment",str(unaffected_ap),"--result",str(unaffected_rp))
        self.assertEqual(fixture.call(*unaffected)["status"],"ACCEPTED")
        review=fixture.common("REV-CHANGE","P-05","/root/reviewer")
        review.update({"target_result_id":"E-CHANGE","target_hash":accepted["content_hash"],"outcome":"PASS",
                       "evidence_disposition":"NEW","reviewed_affected_result_ids":["BASE-A"]})
        reviewed=fixture.register_and_accept("rev-change","P-05","REVIEW","reviews/REV-CHANGE.yaml",review,"/root/reviewer")
        rp,_=self.request("disposition-a","/root/reviewer",reviewed["content_hash"],
                          authority_result_id="REV-CHANGE",evidence_result_id="E-CHANGE")
        receipt=fixture.call(*self.command("evidence-disposition","--request",str(rp)))
        self.assertEqual(receipt["disposition"],"NEW")
        self.assertEqual(fixture.call(*invocation,ok=False)["error"],"assignment_evidence_stale")
        self.assertEqual(fixture.call(*context,ok=False)["error"],"assignment_evidence_stale")
        self.assertEqual(fixture.call(*self.command("evidence-intake","--request",str(path))),intake)
        fixture.call(*self.command("resume","--changed","E-CHANGE"))
        self.assertEqual(fixture.call(*self.command("evidence-intake","--request",str(path))),intake)

    def test_terminal_failure_allows_exact_successor(self):
        fixture=self.fixture
        ap,_=self.pending(occurrence="OCC-LOCAL-A")
        binding={"assignment_id":"A-CUT","attempt_id":"TRY-1","logical_occurrence_id":"OCC-LOCAL-A"}
        evidence=fixture.common("E-FINISHED","P-05","/root/observer")
        evidence.update({"knowledge_status":"OBSERVED","changed":"UNCHANGED"})
        evidence["terminal_author_failure"]={**binding,"assignment_sha256":test_qs.qs.sha(ap.read_bytes()),
            "author_finished":"YES","external_dispatch":"NOT_SENT","reason":"finished without candidate"}
        accepted=fixture.register_and_accept("e-finished","P-05","EVIDENCE","evidence/E-FINISHED.yaml",evidence,"/root/observer")
        path,_=self.request("terminal-a","/root/observer",accepted["content_hash"],
                            authority_result_id="E-FINISHED",**binding)
        invocation=self.command("terminal-author-failure","--request",str(path))
        terminal=fixture.call(*invocation)
        self.assertEqual(terminal["status"],"AUTHOR_FAILURE_TERMINAL")
        self.assertNotIn("RESULT-CUT",fixture.call(*self.command("recover"))["accepted_result_ids"])
        assignment=test_qs.qs.load_yaml(ap)
        assignment.update({"attempt_id":"TRY-2","supersedes_attempt_id":"TRY-1",
                           "result_id":"RESULT-NEW","output_path":"positions/RESULT-NEW.yaml"})
        test_qs.put(ap,assignment)
        self.assertEqual(fixture.call(*self.command("assign","--assignment",str(ap)))["status"],"ASSIGNED")
        self.assertEqual(fixture.call(*invocation),terminal)

    def test_effect_unknown_blocks_substitute_key_and_terminal(self):
        fixture=self.fixture
        ap,_=self.pending(occurrence="OCC-LOCAL-A")
        digest=test_qs.qs.sha(ap.read_bytes())
        binding={"assignment_id":"A-CUT","attempt_id":"TRY-1","logical_occurrence_id":"OCC-LOCAL-A"}
        path,_=self.request("claim-a","/root/cut",digest,**binding)
        first=fixture.call(*self.command("claim-effect","--request",str(path)))
        self.assertEqual(first["effect_state"],"UNKNOWN")
        changed,_=self.request("claim-b","/root/cut",digest,**binding)
        self.assertEqual(fixture.call(*self.command("claim-effect","--request",str(changed)),ok=False)["error"],
                         "effect_occurrence_fenced")
        evidence=fixture.common("E-UNKNOWN","P-05","/root/observer")
        evidence.update({"knowledge_status":"OBSERVED","changed":"UNCHANGED"})
        evidence["terminal_author_failure"]={**binding,"assignment_sha256":digest,"author_finished":"YES",
                                               "external_dispatch":"NOT_SENT","reason":"late unconfirmed evidence"}
        accepted=fixture.register_and_accept("e-unknown","P-05","EVIDENCE","evidence/E-UNKNOWN.yaml",evidence,"/root/observer")
        terminal,_=self.request("terminal-b","/root/observer",accepted["content_hash"],
                                authority_result_id="E-UNKNOWN",**binding)
        self.assertEqual(fixture.call(*self.command("terminal-author-failure","--request",str(terminal)),ok=False)["error"],
                         "terminal_effect_unknown_or_sent")
        self.assertEqual(fixture.call(*self.command("claim-effect","--request",str(path))),first)
        alias,_=self.pending(label="ALIAS",occurrence="OCC-LOCAL-A")
        alias_binding={**binding,"assignment_id":"A-ALIAS"}
        alias_request,_=self.request("claim-alias","/root/cut",test_qs.qs.sha(alias.read_bytes()),**alias_binding)
        self.assertEqual(fixture.call(*self.command("claim-effect","--request",str(alias_request)),ok=False)["error"],
                         "effect_occurrence_fenced")
        independent,_=self.pending(label="B",occurrence="OCC-LOCAL-B")
        other_request,_=self.request("claim-independent","/root/cut",test_qs.qs.sha(independent.read_bytes()),
                                     **{**binding,"assignment_id":"A-B","logical_occurrence_id":"OCC-LOCAL-B"})
        self.assertEqual(fixture.call(*self.command("claim-effect","--request",str(other_request)))["status"],"EFFECT_CLAIMED")

    def test_context_dependency_uses_authenticated_capture(self):
        fixture=self.fixture
        vector=fixture.common("VECTOR-CUT","OP-VECTOR","/root/vector")
        vector.update({"G":"bounded goal","NOT_G":["outside scope"]})
        fixture.register_and_accept("vector-cut","OP-VECTOR","OPERATOR","operators/VECTOR-CUT.yaml",vector,"/root/vector")
        dependency=fixture.common("DEP-CUT","P-02","/root/dependency")
        dependency["addressed_q_ids"]=["Q-001"]
        receipt=fixture.register_and_accept("dep-cut","P-02","PRIMARY","positions/DEP-CUT.yaml",dependency,"/root/dependency")
        source=fixture.run/"positions/DEP-CUT.yaml"
        original=source.read_bytes()
        ap,_=self.pending(goal_id="VECTOR-CUT", dependencies=("DEP-CUT",), register=False)
        assignment=test_qs.qs.load_yaml(ap)
        assignment["dependency_hashes"]=[receipt["content_hash"]]
        test_qs.put(ap,assignment)
        fixture.call(*self.command("assign","--assignment",str(ap)))
        output=fixture.base/"cut-context.yaml"
        invocation=self.command("context","--assignment",str(ap),"--output",str(output))

        # Only the linked dependency may be expanded. A second accepted
        # result in the same run does not grant read authority.
        denied=fixture.call(*invocation,"--expand","VECTOR-CUT",ok=False)
        self.assertEqual(denied["error"],"context_expansion_not_linked")
        self.assertFalse(output.exists())
        source.unlink()
        failed=fixture.call(*invocation,ok=False)
        self.assertIn("immutable_result",str(failed))
        self.assertFalse(output.exists())
        source.write_bytes(original)

        proc=test_qs.source_swap((*invocation,"--expand","DEP-CUT"),source,b"Q-001",b"Q-999")
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        packet=test_qs.qs.load_yaml(output)
        linked=packet["accepted_dependencies"][0]
        self.assertEqual(linked["source"]["sha256"],receipt["content_hash"])
        self.assertEqual(linked["projection"]["view"]["document"]["addressed_q_ids"],["Q-001"])
        self.assertIn(b"Q-999",source.read_bytes())

    def context_revision_fixture(self, *, occurrence=None, hashless=False):
        fixture=self.fixture
        vector=fixture.common("VECTOR-REV","OP-VECTOR","/root/vector")
        vector.update({"G":"bounded goal","NOT_G":["outside scope"]})
        fixture.register_and_accept("vector-rev","OP-VECTOR","OPERATOR","operators/VECTOR-REV.yaml",vector,"/root/vector")
        hashes={}
        for result_id in ("DEP-A","DEP-B"):
            result=fixture.common(result_id,"P-02","/root/source")
            result["addressed_q_ids"]=["Q-001"]
            receipt=fixture.register_and_accept(result_id,"P-02","PRIMARY","positions/"+result_id+".yaml",result,"/root/source")
            hashes[result_id]=receipt["content_hash"]
        ap,rp=self.pending(goal_id="VECTOR-REV",dependencies=() if hashless else tuple(hashes),
                           occurrence=occurrence,register=False)
        assignment=test_qs.qs.load_yaml(ap)
        if hashless:
            assignment["contradiction_result_ids"]=list(hashes)
        else:
            assignment["dependency_hashes"]=list(hashes.values())
        test_qs.put(ap,assignment)
        fixture.call(*self.command("assign","--assignment",str(ap)))
        return ap,rp,hashes

    def context_revision_command(self,assignment,output,*flags,ok=True):
        return self.fixture.call(*self.command(
            "context","--assignment",str(assignment),"--output",str(output),*flags,
        ),ok=ok)

    def test_context_revision_cumulative_history_and_fresh_commit(self):
        fixture=self.fixture
        ap,rp,hashes=self.context_revision_fixture()
        bounded=fixture.base/"bounded.yaml"
        expanded=fixture.base/"expanded.yaml"
        cumulative=fixture.base/"cumulative.yaml"
        base_receipt=self.context_revision_command(ap,bounded)
        base_raw=bounded.read_bytes()
        base_packet=fixture.run/"state/contexts/A-CUT--TRY-1.yaml"
        base_proof=fixture.run/"transactions/contexts/A-CUT--TRY-1.yaml"
        base_proof_raw=base_proof.read_bytes()
        self.assertNotIn(b"FULL_LINKED_EXPANSION",base_raw)

        denied=self.context_revision_command(ap,expanded,"--expand","VECTOR-REV",ok=False)
        self.assertEqual(denied["error"],"context_expansion_not_linked")
        self.assertFalse(expanded.exists())
        conflict=self.context_revision_command(ap,bounded,"--expand","DEP-A",ok=False)
        self.assertEqual(conflict["error"],"context_output_conflict")
        self.assertEqual(bounded.read_bytes(),base_raw)
        revision=self.context_revision_command(ap,expanded,"--expand","DEP-A")
        self.assertNotEqual(revision["sha256"],base_receipt["sha256"])
        self.assertEqual(self.context_revision_command(ap,expanded,"--expand","DEP-A"),revision)
        current=self.context_revision_command(ap,cumulative,"--expand","DEP-B")
        bounded_doc=test_qs.qs.load_yaml(bounded)
        cumulative_doc=test_qs.qs.load_yaml(cumulative)
        for key in bounded_doc.keys()-{"accepted_dependencies"}:
            self.assertEqual(cumulative_doc[key],bounded_doc[key])
        self.assertEqual({row["source"]["result_id"]:row["source"]["sha256"]
                          for row in cumulative_doc["accepted_dependencies"]
                          if row["projection"]["view"]["kind"]=="FULL_LINKED_EXPANSION"},hashes)
        self.assertEqual((bounded.read_bytes(),base_packet.read_bytes(),base_proof.read_bytes()),
                         (base_raw,base_raw,base_proof_raw))

        invocation=self.command("commit","--assignment",str(ap),"--result",str(rp))
        doc=test_qs.qs.load_yaml(rp)
        for historical in (base_receipt,revision):
            test_qs.put(rp,{**doc,"context_packet_sha256":historical["sha256"]})
            self.assertEqual(fixture.call(*invocation,ok=False)["error"],"result_context_binding")
        test_qs.put(rp,{**doc,"context_packet_sha256":current["sha256"]})
        self.assertEqual(fixture.call(*invocation)["status"],"ACCEPTED")
        self.assertEqual(fixture.call(*self.command("recover"))["status"],"RECOVERED")
        self.assertEqual(fixture.call(*invocation)["status"],"IDEMPOTENT")
        for source,receipt in ((bounded,base_receipt),(expanded,revision),(cumulative,current)):
            historical=fixture.base/("history-"+source.name)
            replay=self.context_revision_command(ap,historical,"--historical","--packet-sha256",receipt["sha256"])
            self.assertEqual(replay["status"],"CONTEXT_HISTORICAL")
            self.assertEqual(historical.read_bytes(),source.read_bytes())
        unknown=self.context_revision_command(ap,fixture.base/"unknown.yaml","--historical",
                                              "--packet-sha256","f"*64,ok=False)
        self.assertEqual(unknown["error"],"context_packet_hash_unknown")

    def test_context_revision_requires_intact_pair_and_exact_tail_reissue(self):
        fixture=self.fixture
        ap,rp,_=self.context_revision_fixture()
        first=self.context_revision_command(ap,fixture.base/"bounded.yaml")
        output=fixture.base/"expanded.yaml"
        invocation=self.command("context","--assignment",str(ap),"--output",str(output),"--expand","DEP-A")
        crashed=subprocess.run(
            [sys.executable,"-B","-c",CRASH_SCRIPT,str(test_qs.QS),"before_context_revision_packet",*invocation],
            text=True,capture_output=True,check=False,
        )
        self.assertEqual(crashed.returncode,74,crashed.stdout+crashed.stderr)
        self.assertFalse(output.exists())
        proof,=list((fixture.run/"transactions/contexts/revisions").rglob("*.yaml"))
        proof_raw=proof.read_bytes()
        proof_doc=test_qs.qs.load_yaml(proof)
        packet=fixture.run/proof_doc["packet_path"]
        self.assertFalse(packet.exists())
        historical=fixture.base/"historical.yaml"
        self.assertEqual(self.context_revision_command(ap,historical,"--historical",ok=False)["error"],
                         "context_revision_incomplete")
        other=self.context_revision_command(ap,output,"--expand","DEP-B",ok=False)
        self.assertEqual(other["error"],"context_revision_incomplete")
        self.assertFalse(output.exists())
        result=test_qs.qs.load_yaml(rp)
        test_qs.put(rp,{**result,"context_packet_sha256":first["sha256"]})
        self.assertEqual(fixture.call(*self.command("commit","--assignment",str(ap),"--result",str(rp)),ok=False)["error"],
                         "context_revision_incomplete")
        exact=fixture.call(*invocation)
        self.assertEqual(exact["sha256"],proof_doc["packet_sha256"])
        self.assertEqual(packet.read_bytes(),output.read_bytes())
        self.assertEqual(proof.read_bytes(),proof_raw)

        test_qs.put(proof,{**proof_doc,"previous_packet_sha256":"0"*64})
        self.assertEqual(self.context_revision_command(ap,historical,"--historical",ok=False)["error"],
                         "context_revision_proof")
        proof.write_bytes(proof_raw)
        packet.write_bytes(b"tampered context\n")
        self.assertEqual(self.context_revision_command(ap,historical,"--historical",ok=False)["error"],
                         "context_revision_packet_drift")
        packet.write_bytes(output.read_bytes())
        proof.unlink()
        self.assertEqual(self.context_revision_command(ap,historical,"--historical",ok=False)["error"],
                         "context_revision_incomplete")
        self.assertFalse(historical.exists())

    def test_context_revision_refuses_lost_committed_tail(self):
        fixture=self.fixture
        ap,rp,_=self.context_revision_fixture()
        first=self.context_revision_command(ap,fixture.base/"bounded.yaml")
        revised=self.context_revision_command(ap,fixture.base/"revised.yaml","--expand","DEP-A")
        document=test_qs.qs.load_yaml(rp)
        test_qs.put(rp,{**document,"context_packet_sha256":revised["sha256"]})
        commit=self.command("commit","--assignment",str(ap),"--result",str(rp))
        crashed=subprocess.run(
            [sys.executable,"-B","-c",CRASH_SCRIPT,str(test_qs.QS),"after_marker",*commit],
            text=True,capture_output=True,check=False,
        )
        self.assertEqual(crashed.returncode,71,crashed.stdout+crashed.stderr)
        transaction=fixture.run/"transactions/RESULT-CUT.yaml"
        result=fixture.run/"positions/RESULT-CUT.yaml"
        self.assertTrue(transaction.is_file())
        self.assertFalse(result.exists())
        proof,=list((fixture.run/"transactions/contexts/revisions").rglob("*.yaml"))
        packet=fixture.run/test_qs.qs.load_yaml(proof)["packet_path"]
        original=[(path,path.read_bytes()) for path in (proof,packet)]
        for published in (False,True):
            self.assertEqual(result.is_file(),published)
            for path,_ in original:
                path.unlink()
            for flags in ((),("--historical",),("--historical","--packet-sha256",first["sha256"])):
                denied=fixture.base/"denied.yaml"
                self.assertEqual(self.context_revision_command(ap,denied,*flags,ok=False)["error"],
                                 "context_revision_result_binding")
                self.assertFalse(denied.exists())
            for path,raw in original:
                path.write_bytes(raw)
            if not published:
                self.assertEqual(fixture.call(*self.command("recover"))["status"],"RECOVERED")
        self.assertEqual(self.context_revision_command(ap,fixture.base/"exact-old.yaml","--historical",
                                                      "--packet-sha256",first["sha256"])["sha256"],first["sha256"])

    def test_context_revision_refuses_hashless_pending_and_withdrawn_source(self):
        fixture=self.fixture
        ap,_,_=self.context_revision_fixture(hashless=True)
        original=fixture.base/"base.yaml"
        latest=fixture.base/"latest.yaml"
        self.context_revision_command(ap,original)
        self.context_revision_command(ap,latest,"--expand","DEP-A")
        evidence=fixture.common("CHANGE-REV","P-04","/root/evidence")
        evidence.update({"changed":"CHANGED","command_intake_scope":["DEP-A"]})
        accepted=fixture.register_and_accept("change-rev","P-04","EVIDENCE","evidence/CHANGE-REV.yaml",evidence,"/root/evidence")
        request,_=self.request("intake-rev","/root/evidence",accepted["content_hash"],
                               authority_result_id="CHANGE-REV",evidence_result_id="CHANGE-REV")
        fixture.call(*self.command("evidence-intake","--request",str(request)))
        output=fixture.base/"cannot-expand.yaml"
        self.assertEqual(self.context_revision_command(ap,output,"--expand","DEP-B",ok=False)["error"],
                         "assignment_evidence_pending")
        review=fixture.common("REVIEW-REV","P-05","/root/review")
        review.update({"target_result_id":"CHANGE-REV","target_hash":accepted["content_hash"],"outcome":"PASS",
                       "evidence_disposition":"NEW","reviewed_affected_result_ids":["DEP-A"]})
        reviewed=fixture.register_and_accept("review-rev","P-05","REVIEW","reviews/REVIEW-REV.yaml",review,"/root/review")
        disposition,_=self.request("disposition-rev","/root/review",reviewed["content_hash"],
                                   authority_result_id="REVIEW-REV",evidence_result_id="CHANGE-REV")
        fixture.call(*self.command("evidence-disposition","--request",str(disposition)))
        self.assertEqual(self.context_revision_command(ap,output,"--expand","DEP-B",ok=False)["error"],
                         "assignment_evidence_stale")
        self.assertFalse(output.exists())

    def test_context_revision_dispatch_fence_and_exact_claim_hash(self):
        fixture=self.fixture
        ap,_,_=self.context_revision_fixture(occurrence="OCC-REV")
        self.context_revision_command(ap,fixture.base/"before-claim.yaml")
        revised=self.context_revision_command(ap,fixture.base/"revised.yaml","--expand","DEP-A")
        request,_=self.request("claim-rev","/root/cut",test_qs.qs.sha(ap.read_bytes()),
                               assignment_id="A-CUT",attempt_id="TRY-1",logical_occurrence_id="OCC-REV")
        invocation=self.command("claim-effect","--request",str(request),"--external-dispatch")
        receipt=fixture.call(*invocation)
        self.assertEqual((receipt["effect_state"],receipt["dispatch_action"],receipt["context_packet_sha256"]),
                         ("UNKNOWN","SEND_ONCE",revised["sha256"]))
        output=fixture.base/"after-claim.yaml"
        self.assertEqual(self.context_revision_command(ap,output,"--expand","DEP-B",ok=False)["error"],
                         "context_revision_dispatch_fenced")
        self.assertFalse(output.exists())
        self.assertEqual(fixture.call(*invocation)["dispatch_action"],"HOLD_RECONCILE")
        self.assertEqual(self.context_revision_command(ap,fixture.base/"read-only.yaml","--historical")["sha256"],
                         revised["sha256"])

        for suffix,digest in (("WRONG","0"*64),("EXACT",revised["sha256"])):
            result_id="OBS-REV-"+suffix
            observed=fixture.common(result_id,"P-05","/root/observer")
            observed.update({"knowledge_status":"OBSERVED","changed":"UNCHANGED"})
            observed["effect_reconciliation"]={
                "assignment_id":"A-CUT","attempt_id":"TRY-1","logical_occurrence_id":"OCC-REV",
                "assignment_sha256":test_qs.qs.sha(ap.read_bytes()),"claim_key":"claim-rev",
                "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"TEST_ONLY exact observation",
                "context_packet_sha256":digest,
            }
            proof=fixture.register_and_accept(result_id,"P-05","EVIDENCE","evidence/"+result_id+".yaml",observed,"/root/observer")
            reconcile,_=self.request("reconcile-"+suffix,"/root/observer",proof["content_hash"],
                                     authority_result_id=result_id,assignment_id="A-CUT",
                                     attempt_id="TRY-1",logical_occurrence_id="OCC-REV")
            command=self.command("reconcile-effect","--request",str(reconcile))
            if suffix=="WRONG":
                self.assertEqual(fixture.call(*command,ok=False)["error"],"effect_reconciliation_context_binding")
            else:
                response=fixture.call(*command)
                self.assertEqual((response["effect_state"],response["context_packet_sha256"]),
                                 ("RECIPIENT_CONFIRMED",revised["sha256"]))

        proof,=list((fixture.run/"transactions/contexts/revisions").rglob("*.yaml"))
        packet=fixture.run/test_qs.qs.load_yaml(proof)["packet_path"]
        packet.unlink()
        proof.unlink()
        replay=self.context_revision_command(ap,fixture.base/"cannot-fall-back.yaml","--historical",ok=False)
        self.assertEqual(replay["error"],"context_revision_claim_binding")

    def test_context_revision_refuses_native_dispatch_unknown(self):
        fixture=self.fixture
        ap,_,_=self.context_revision_fixture()
        self.context_revision_command(ap,fixture.base/"before-launch.yaml")
        fixture.call(*self.command("launch-intent","--assignment-id","A-CUT","--attempt-id","TRY-1",
                                   "--state","DISPATCH_UNKNOWN"))
        output=fixture.base/"after-launch.yaml"
        self.assertEqual(self.context_revision_command(ap,output,"--expand","DEP-A",ok=False)["error"],
                         "context_revision_dispatch_fenced")
        self.assertFalse(output.exists())

    def test_portable_clean_consumer_recomputes_required_relation(self):
        fixture=self.fixture
        accepted, early_accepted, expanded, _ = build_portable_fixture(fixture)
        output=fixture.base/"portable"
        export=fixture.call(*self.command("export-portable","--task-id","T-EARLY","--check-id","C-EARLY","--output",str(output)))
        self.assertEqual(export["status"],"PORTABLE_EXPORTED")

        def clean_check(package):
            process=subprocess.run([sys.executable,"-B",str(package/"scripts/qs.py"),
                                    "portable-verify","--package",str(package)],cwd=package,
                                   text=True,capture_output=True,check=False)
            return process.returncode,json.loads(process.stdout or process.stderr)

        code,receipt=clean_check(output)
        self.assertEqual(code,0,receipt)
        self.assertEqual((receipt["task_id"],receipt["check_id"]),("T-EARLY","C-EARLY"))
        manifest=test_qs.qs.load_yaml(output/"portable-manifest.yaml")
        context_paths=[target for key,target in manifest["original"]["relations"].items()
                       if key.startswith("E-EARLY:context:")]
        self.assertEqual(len(context_paths),1)
        exact_path=output/"subject"/fixture.run.name/context_paths[0]
        self.assertEqual(exact_path.read_bytes(),expanded.read_bytes())
        self.assertNotEqual(exact_path.read_bytes(),(fixture.base/"early-bounded.yaml").read_bytes())
        consumer=r"""
import argparse
import json
from pathlib import Path
import sys

package=Path.cwd()
sys.path.insert(0,str(package/"scripts"))
import qs

verified=qs.portable_verify(argparse.Namespace(package=str(package)))
manifest=qs.load_yaml(package/"portable-manifest.yaml")
original=manifest["original"]
run=package/"subject"/original["run_id"]
plan_path=run/"plan/PLAN-L.yaml"
assert qs.sha(plan_path.read_bytes())==manifest["files"]["subject/plan/PLAN-L.yaml"]
task=next(row for row in qs.load_yaml(plan_path)["tasks"] if row["id"]==verified["task_id"])
assert verified["check_id"] in task["checks"]
action=task["fixture_action"]
assert action["operation"]=="ASSERT_EVIDENCE_UNCHANGED"
result_id=original["relations"][task["id"]+":e_refs:"+action["result_id"]]
path=run/"evidence"/(result_id+".yaml")
observed=qs.sha(path.read_bytes())
assert observed==action["sha256"] and qs.load_yaml(path)["changed"]=="UNCHANGED"
print(json.dumps({"status":"EXECUTED_TEST_ONLY","task_id":task["id"],
                  "check_id":verified["check_id"],"observed_sha256":observed}))
"""
        executed=subprocess.run([sys.executable,"-I","-B","-c",consumer],
                                cwd=output,text=True,capture_output=True,check=False)
        self.assertEqual(executed.returncode,0,executed.stdout+executed.stderr)
        self.assertEqual(json.loads(executed.stdout),{"status":"EXECUTED_TEST_ONLY","task_id":"T-EARLY",
            "check_id":"C-EARLY","observed_sha256":early_accepted["content_hash"]})
        negative=fixture.base/"without-relation"
        shutil.copytree(output,negative)
        manifest=negative/"portable-manifest.yaml"
        value=test_qs.qs.load_yaml(manifest)
        value["original"]["relations"].pop("T-LATE:e_refs:E-LATE")
        value["files"].pop("subject/evidence/E-LATE.yaml")
        test_qs.put(manifest,value)
        code,receipt=clean_check(negative)
        self.assertEqual(code,2,receipt)
        self.assertEqual(receipt["error"],"portable_missing_relation:T-LATE:e_refs:E-LATE")

        origin=fixture.run/"evidence/E-LATE.yaml"
        original=origin.read_bytes()
        self.assertEqual(test_qs.qs.sha(original),accepted["content_hash"])
        drifted_output=fixture.base/"drifted-export"
        drifted_export=self.command("export-portable","--task-id","T-EARLY","--check-id","C-EARLY",
                                    "--output",str(drifted_output))
        try:
            proc=test_qs.source_swap(drifted_export,origin,b"\n",b"\n\n")
            self.assertEqual(proc.returncode,2,proc.stdout+proc.stderr)
            self.assertEqual(json.loads(proc.stdout or proc.stderr)["error"],"immutable_result_tamper:E-LATE")
            self.assertEqual(origin.read_bytes(),original.replace(b"\n",b"\n\n",1))
            self.assertFalse(drifted_output.exists())
        finally:
            origin.write_bytes(original)
        negative=fixture.base/"without-helper"
        shutil.copytree(output,negative)
        manifest=negative/"portable-manifest.yaml"
        value=test_qs.qs.load_yaml(manifest)
        value["files"].pop("schemas/plan-stage.yaml")
        (negative/"schemas/plan-stage.yaml").unlink()
        test_qs.put(manifest,value)
        code,receipt=clean_check(negative)
        self.assertEqual(code,2,receipt)
        self.assertEqual(receipt["error"],"portable_missing_helper:schemas/plan-stage.yaml")

        unrelated=fixture.common("E-OTHER","P-04","/root/evidence")
        unrelated["changed"]="UNCHANGED"
        fixture.register_and_accept("unrelated","P-04","EVIDENCE","evidence/E-OTHER.yaml",unrelated,"/root/evidence")
        disjoint=fixture.common("E-DISJOINT-CHANGE","P-04","/root/evidence")
        disjoint.update({"changed":"CHANGED","command_intake_scope":["E-OTHER"]})
        changed_other=fixture.register_and_accept("disjoint","P-04","EVIDENCE","evidence/E-DISJOINT-CHANGE.yaml",
                                                  disjoint,"/root/evidence")
        request,_=self.request("intake-disjoint","/root/evidence",changed_other["content_hash"],
                               authority_result_id="E-DISJOINT-CHANGE",evidence_result_id="E-DISJOINT-CHANGE")
        fixture.call(*self.command("evidence-intake","--request",str(request)))
        disjoint_output=fixture.base/"disjoint-export"
        self.assertEqual(fixture.call(*self.command("export-portable","--task-id","T-EARLY","--check-id","C-EARLY",
                                                   "--output",str(disjoint_output)))["status"],"PORTABLE_EXPORTED")
        self.assertEqual(clean_check(disjoint_output)[0],0)

        changed=fixture.common("E-CHANGE-PORTABLE","P-04","/root/evidence")
        changed.update({"changed":"CHANGED","command_intake_scope":["E-LATE"]})
        change=fixture.register_and_accept("change","P-04","EVIDENCE","evidence/E-CHANGE-PORTABLE.yaml",
                                          changed,"/root/evidence")
        request,_=self.request("intake-portable","/root/evidence",change["content_hash"],
                               authority_result_id="E-CHANGE-PORTABLE",evidence_result_id="E-CHANGE-PORTABLE")
        fixture.call(*self.command("evidence-intake","--request",str(request)))
        blocked=fixture.base/"blocked-export"
        export=self.command("export-portable","--task-id","T-EARLY","--check-id","C-EARLY","--output",str(blocked))
        self.assertEqual(fixture.call(*export,ok=False)["error"],"portable_pending:T-EARLY")
        self.assertFalse(blocked.exists())
        review=fixture.common("REVIEW-PORTABLE","P-05","/root/reviewer")
        review.update({"target_result_id":"E-CHANGE-PORTABLE","target_hash":change["content_hash"],"outcome":"PASS",
                       "evidence_disposition":"NEW","reviewed_affected_result_ids":["E-LATE"]})
        reviewed=fixture.register_and_accept("review-change","P-05","REVIEW","reviews/REVIEW-PORTABLE.yaml",
                                             review,"/root/reviewer")
        request,_=self.request("disposition-portable","/root/reviewer",reviewed["content_hash"],
                               authority_result_id="REVIEW-PORTABLE",evidence_result_id="E-CHANGE-PORTABLE")
        fixture.call(*self.command("evidence-disposition","--request",str(request)))
        self.assertEqual(fixture.call(*export,ok=False)["error"],"portable_stale:T-EARLY")
        self.assertFalse(blocked.exists())
        resumed=fixture.call(*self.command("resume"))
        self.assertIn("PLAN-L",resumed["stale_result_ids"])
        self.assertEqual(fixture.call(*export,ok=False)["error"],"portable_stale:T-EARLY")
        self.assertFalse(blocked.exists())

    def test_effect_sender_is_called_once_after_lock_release(self):
        fixture=self.fixture
        ap,_=self.pending(occurrence="OCC-LOCAL-A")
        binding={"assignment_id":"A-CUT","attempt_id":"TRY-1","logical_occurrence_id":"OCC-LOCAL-A"}
        digest=test_qs.qs.sha(ap.read_bytes())
        request,_=self.request("send-a","/root/cut",digest,**binding)
        receipt_path=fixture.base/"spy.txt"
        hook=r"""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(sys.argv[1]).parent))
import qs
original=qs.journal_command
spy=Path(sys.argv[2])

def send(receipt):
    run=Path(sys.argv[5])/"runs"/sys.argv[7]
    with qs.lock(run):
        with spy.open("a",encoding="utf-8") as stream:
            stream.write(receipt["logical_occurrence_id"]+"\n")
            stream.flush()
            os.fsync(stream.fileno())
    os._exit(79)

qs.journal_command=lambda args: original(args,effect_sender=send)
raise SystemExit(qs.main(sys.argv[3:]))
"""
        invocation=self.command("claim-effect","--request",str(request))
        spy_argv=[sys.executable,"-B","-c",hook,str(test_qs.QS),str(receipt_path)]
        occurrence_lock=fixture.run/"state/occurrences"/test_qs.qs.sha(b"OCC-LOCAL-A")
        with test_qs.qs.lock(occurrence_lock):
            denied=subprocess.run([*spy_argv,*invocation],text=True,capture_output=True,check=False)
        self.assertEqual(denied.returncode,2,denied.stdout+denied.stderr)
        self.assertEqual(json.loads(denied.stdout or denied.stderr)["error"],"coordinator_lock_busy")
        self.assertFalse(receipt_path.exists())
        sent=subprocess.run([*spy_argv,*invocation],text=True,capture_output=True,check=False)
        self.assertEqual(sent.returncode,79,sent.stdout+sent.stderr)
        self.assertEqual(receipt_path.read_text(),"OCC-LOCAL-A\n")
        replay=subprocess.run([*spy_argv,*invocation],text=True,capture_output=True,check=False)
        self.assertEqual(replay.returncode,0,replay.stdout+replay.stderr)
        self.assertEqual(json.loads(replay.stdout)["effect_state"],"UNKNOWN")
        self.assertEqual(receipt_path.read_text(),"OCC-LOCAL-A\n")
        substitute,_=self.request("substitute-a","/root/cut",digest,**binding)
        refused=subprocess.run([*spy_argv,*self.command("claim-effect","--request",str(substitute))],
                               text=True,capture_output=True,check=False)
        self.assertEqual(refused.returncode,2,refused.stdout+refused.stderr)
        self.assertEqual(json.loads(refused.stderr)["error"],"effect_occurrence_fenced")
        self.assertEqual(receipt_path.read_text(),"OCC-LOCAL-A\n")
        other,_=self.pending(label="B",occurrence="OCC-LOCAL-B")
        independent,_=self.request("send-b","/root/cut",test_qs.qs.sha(other.read_bytes()),
                                   assignment_id="A-B",attempt_id="TRY-1",logical_occurrence_id="OCC-LOCAL-B")
        second=subprocess.run([*spy_argv,*self.command("claim-effect","--request",str(independent))],
                              text=True,capture_output=True,check=False)
        self.assertEqual(second.returncode,79,second.stdout+second.stderr)
        self.assertEqual(receipt_path.read_text(),"OCC-LOCAL-A\nOCC-LOCAL-B\n")

        proof=fixture.common("E-RECIPIENT","P-04","/root/recipient")
        proof.update({"changed":"UNCHANGED","knowledge_status":"OBSERVED","effect_reconciliation":{
            **binding,"assignment_sha256":digest,"claim_key":"send-a",
            "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"local-spy-record"}})
        accepted=fixture.register_and_accept(
            "recipient","P-04","EVIDENCE","evidence/E-RECIPIENT.yaml",proof,"/root/recipient")
        reconciliation,_=self.request("reconcile-a","/root/recipient",accepted["content_hash"],
                                      **binding,authority_result_id="E-RECIPIENT")
        receipt=fixture.call(*self.command("reconcile-effect","--request",str(reconciliation)))
        self.assertEqual(receipt["effect_state"],"RECIPIENT_CONFIRMED")
        self.assertEqual(fixture.call(*self.command("reconcile-effect","--request",str(reconciliation))),receipt)
        self.assertEqual(receipt_path.read_text(),"OCC-LOCAL-A\nOCC-LOCAL-B\n")

    def resource_assignment(self, label, **resources):
        ap, rp = self.pending(label=label, occurrence="OCC-"+label, register=False)
        assignment = {**test_qs.qs.load_yaml(ap), **resources}
        test_qs.put(ap, assignment)
        self.fixture.call(*self.command("assign", "--assignment", str(ap)))
        return ap, rp, {key: assignment[key] for key in
                        ("assignment_id", "attempt_id", "logical_occurrence_id")}

    def resource_claim(self, label, assignment, binding):
        request, _ = self.request(label, "/root/cut", test_qs.qs.sha(assignment.read_bytes()), **binding)
        return self.command("claim-effect", "--request", str(request), "--external-dispatch")

    def resource_reconcile(self, label, binding, **observations):
        actor = "/root/observer"
        result = self.fixture.common(label, "P-05", actor)
        result.update(knowledge_status="OBSERVED", changed="UNCHANGED", **observations)
        accepted = self.fixture.register_and_accept(label, "P-05", "EVIDENCE", "evidence/"+label+".yaml",
                                                    result, actor)
        request, _ = self.request("reconcile-"+label, actor, accepted["content_hash"],
                                  authority_result_id=label, **binding)
        return self.command("reconcile-effect", "--request", str(request))

    def resource_journal_bytes(self):
        return [path.read_bytes() for path in sorted((self.fixture.run/"transactions/commands").glob("*.yaml"))]

    def test_shared_effect_resource_holds_through_ack_and_result_until_terminal(self):
        fixture = self.fixture
        vector = fixture.common("VECTOR-RESOURCE", "OP-VECTOR", "/root/vector")
        vector.update(G="Bounded resource fixture", NOT_G=["Outside scope"])
        fixture.register_and_accept("vector-resource", "OP-VECTOR", "OPERATOR",
                                    "operators/VECTOR-RESOURCE.yaml", vector, "/root/vector")
        a, result, a_binding = self.resource_assignment("HOLDER", resource_keys=["GPU-1", "GPU-1"],
                                                       goal_result_id="VECTOR-RESOURCE")
        b, _, b_binding = self.resource_assignment("WAITER", resource_keys=["GPU-1"])
        c, _, c_binding = self.resource_assignment("DISJOINT", resource_keys=["GPU-2"])
        log = fixture.base/"dispatch-spy.txt"
        script = """
from pathlib import Path
import sys
sys.path.insert(0,str(Path(sys.argv[1]).parent))
import qs
original=qs.journal_command
log=Path(sys.argv[2])
def send(receipt):
    run=Path(sys.argv[5])/"runs"/sys.argv[7]
    with qs.lock(run), log.open("a",encoding="utf-8") as stream:
        stream.write(receipt["logical_occurrence_id"]+"\\n")
qs.journal_command=lambda args: original(args,effect_sender=send)
raise SystemExit(qs.main(sys.argv[3:]))
"""

        def dispatch(invocation, expected_exit=0):
            proc = subprocess.run([sys.executable, "-B", "-c", script, str(test_qs.QS), str(log), *invocation],
                                  text=True, capture_output=True, timeout=20, check=False)
            self.assertEqual(proc.returncode, expected_exit, proc.stdout+proc.stderr)
            return json.loads(proc.stdout or proc.stderr)

        def held(key, effect_state):
            before = self.resource_journal_bytes()
            blocked = dispatch(self.resource_claim(key, b, b_binding), 2)
            self.assertEqual(blocked["error"], "effect_resource_occupied:OCC-HOLDER:claim-holder:"+effect_state)
            self.assertEqual(self.resource_journal_bytes(), before)

        first = self.resource_claim("claim-holder", a, a_binding)
        claim = dispatch(first)
        self.assertEqual((claim["dispatch_action"], claim["resource_keys"]), ("SEND_ONCE", ["GPU-1"]))
        held("unknown-hold", "UNKNOWN")
        self.assertEqual(log.read_text().splitlines(), ["OCC-HOLDER"])
        self.assertEqual(dispatch(first)["dispatch_action"], "HOLD_RECONCILE")
        self.assertEqual(dispatch(self.resource_claim("claim-disjoint", c, c_binding))["dispatch_action"], "SEND_ONCE")
        self.assertEqual(fixture.call(*self.command("recover"))["status"], "RECOVERED")
        held("recovered-hold", "UNKNOWN")
        context = fixture.call(*self.command("context", "--assignment", str(a),
                                            "--output", str(fixture.base/"holder-context.yaml")))
        binding = {**a_binding, "assignment_sha256": claim["assignment_sha256"], "claim_key": "claim-holder"}
        ack = self.resource_reconcile("ACK", a_binding, effect_reconciliation={
            **binding, "disposition": "RECIPIENT_CONFIRMED", "authoritative_receipt": "TEST_ONLY delivery"})
        receipt = fixture.call(*ack)
        self.assertEqual(receipt["effect_state"], "RECIPIENT_CONFIRMED")
        held("confirmed-hold", "RECIPIENT_CONFIRMED")
        test_qs.put(result, {**test_qs.qs.load_yaml(result), "context_packet_sha256": context["sha256"]})
        fixture.call(*self.command("commit", "--assignment", str(a), "--result", str(result)))
        held("result-hold", "RECIPIENT_CONFIRMED")

        terminal = {**binding, "recipient_agent_id": "/root/cut", "execution_state": "COMPLETED",
                    "authoritative_receipt": "TEST_ONLY terminal observation", "context_packet_sha256": context["sha256"]}
        packet_name = "A-HOLDER--TRY-1.yaml"
        packet_pair = [fixture.run/"state/contexts"/packet_name, fixture.run/"transactions/contexts"/packet_name]
        prior = [path.read_bytes() for path in packet_pair]
        for path in packet_pair:
            path.unlink()
        omitted = {key: value for key, value in terminal.items() if key != "context_packet_sha256"}
        missing_packet = self.resource_reconcile("MISSING-PACKET", a_binding, effect_terminal=omitted)
        before = self.resource_journal_bytes()
        self.assertEqual(fixture.call(*missing_packet, ok=False)["error"], "context_revision_result_binding")
        self.assertEqual(self.resource_journal_bytes(), before)
        held("context-loss-hold", "RECIPIENT_CONFIRMED")
        for path, raw in zip(packet_pair, prior):
            path.write_bytes(raw)
        for label, mismatch, error in (
            ("CLAIM", {"claim_key": "wrong-claim"}, "effect_terminal_proof"),
            ("ACTOR", {"recipient_agent_id": "/root/other"}, "effect_terminal_proof"),
            ("STATE", {"execution_state": "RUNNING"}, "effect_terminal_proof"),
            ("CONTEXT", {"context_packet_sha256": "0"*64}, "effect_terminal_context_binding"),
        ):
            with self.subTest(label=label):
                denied = self.resource_reconcile(label, a_binding, effect_terminal={**terminal, **mismatch})
                before = self.resource_journal_bytes()
                self.assertEqual(fixture.call(*denied, ok=False)["error"], error)
                self.assertEqual(self.resource_journal_bytes(), before)
        self.assertEqual(log.read_text().splitlines(), ["OCC-HOLDER", "OCC-DISJOINT"])
        release = self.resource_reconcile("DONE", a_binding, effect_terminal=terminal)
        released = fixture.call(*release)
        self.assertEqual((released["status"], released["claim_key"], released["context_packet_sha256"]),
                         ("EFFECT_RESOURCE_RELEASED", "claim-holder", context["sha256"]))
        before = self.resource_journal_bytes()
        self.assertEqual(fixture.call(*release), released)
        self.assertEqual(fixture.call(*ack), receipt)
        self.assertEqual(dispatch(first)["dispatch_action"], "HOLD_RECONCILE")
        self.assertEqual(self.resource_journal_bytes(), before)
        duplicate = self.resource_reconcile("AGAIN", a_binding, effect_terminal=terminal)
        self.assertEqual(fixture.call(*duplicate, ok=False)["error"], "effect_terminal_proof")
        self.assertEqual(dispatch(self.resource_claim("claim-waiter", b, b_binding))["dispatch_action"], "SEND_ONCE")
        self.assertEqual(log.read_text().splitlines(), ["OCC-HOLDER", "OCC-DISJOINT", "OCC-WAITER"])

    def test_effect_writer_prefix_legacy_scope_and_combined_release(self):
        fixture = self.fixture
        a, _, a_binding = self.resource_assignment("DIRECTORY", sole_writer_paths=["reports/./block"])
        b, _, b_binding = self.resource_assignment("CHILD", sole_writer_paths=["reports\\block/child.yaml"])
        c, _, c_binding = self.resource_assignment("SIBLING", sole_writer_paths=["reports/blockade/child.yaml"])
        claim = fixture.call(*self.resource_claim("claim-directory", a, a_binding))
        self.assertEqual(claim["sole_writer_paths"], ["reports/block"])
        journal_path, = (fixture.run/"transactions/commands").glob("*.yaml")
        row = test_qs.qs.load_yaml(journal_path)
        del row["receipt"]["resource_keys"], row["receipt"]["sole_writer_paths"], row["record_sha256"]
        row["record_sha256"] = test_qs.qs.sha(test_qs.qs.canon_yaml(row))
        test_qs.put(journal_path, row)
        refused = self.resource_claim("claim-child", b, b_binding)
        self.assertEqual(fixture.call(*refused, ok=False)["error"],
                         "effect_resource_occupied:OCC-DIRECTORY:claim-directory:UNKNOWN")
        pair = a_binding["assignment_id"]+"\0"+a_binding["attempt_id"]
        intent_path = fixture.run/"intents"/(test_qs.qs.sha(pair.encode()).upper()+".yaml")
        intent_raw = intent_path.read_bytes()
        intent = test_qs.qs.load_yaml(intent_path)
        test_qs.put(intent_path, {**intent, "assignment_bytes_b64": "AAAA"})
        before = self.resource_journal_bytes()
        self.assertEqual(fixture.call(*refused, ok=False)["error"], "effect_legacy_resource_scope:OCC-DIRECTORY")
        self.assertEqual(self.resource_journal_bytes(), before)
        intent_path.write_bytes(intent_raw)
        self.assertEqual(fixture.call(*self.resource_claim("claim-sibling", c, c_binding))["dispatch_action"],
                         "SEND_ONCE")
        binding = {**a_binding, "assignment_sha256": claim["assignment_sha256"], "claim_key": "claim-directory"}
        terminal = {**binding, "recipient_agent_id": "/root/cut", "execution_state": "FAILED",
                    "authoritative_receipt": "TEST_ONLY verified stopped after delivery"}
        no_ack = self.resource_reconcile("NO-ACK", a_binding, effect_terminal=terminal)
        self.assertEqual(fixture.call(*no_ack, ok=False)["error"], "effect_terminal_ack_required")
        both = self.resource_reconcile("BOTH", a_binding, effect_terminal=terminal, effect_reconciliation={
            **binding, "disposition": "RECIPIENT_CONFIRMED", "authoritative_receipt": "TEST_ONLY delivery"})
        receipt = fixture.call(*both)
        self.assertEqual((receipt["effect_state"], receipt["resource_terminal_state"]),
                         ("RECIPIENT_CONFIRMED", "FAILED"))
        self.assertEqual(fixture.call(*self.resource_claim("claim-child-after-release", b, b_binding))["dispatch_action"],
                         "SEND_ONCE")

    def test_effect_not_sent_releases_shared_resource(self):
        fixture = self.fixture
        a, _, a_binding = self.resource_assignment("UNSENT", resource_keys=["GPU-1"])
        b, _, b_binding = self.resource_assignment("NEXT", resource_keys=["GPU-1"])
        first = self.resource_claim("claim-unsent", a, a_binding)
        claim = fixture.call(*first)
        stopped = self.resource_reconcile("PROOF-UNSENT", a_binding, effect_reconciliation={
            **a_binding, "assignment_sha256": claim["assignment_sha256"], "claim_key": "claim-unsent",
            "disposition": "NOT_SENT", "authoritative_receipt": "TEST_ONLY provider observed no send"})
        self.assertEqual(fixture.call(*stopped)["effect_state"], "NOT_SENT")
        self.assertEqual(fixture.call(*self.resource_claim("claim-next", b, b_binding))["dispatch_action"], "SEND_ONCE")
        self.assertEqual(fixture.call(*first)["dispatch_action"], "HOLD_RECONCILE")

    def test_selected_source_answer_edge_invalidates_consumer(self):
        results = {
            "E-A": {"content_hash": "e-a"},
            "ANSWER-A": {"content_hash": "a", "dependency_hashes": ["e-a"]},
            "SOURCE": {"content_hash": "source", "dependency_hashes": []},
            "B": {"content_hash": "b", "dependency_hashes": []},
        }
        docs = {"SOURCE": {"artifact_type": "SOURCE_OUTCOMES",
                           "records": [{"answer_result_id": "ANSWER-A"}]}}
        self.assertEqual(test_qs.qs.stale_result_closure(results, docs, ["E-A"]),
                         {"E-A", "ANSWER-A", "SOURCE"})


if __name__ == "__main__":
    unittest.main()
