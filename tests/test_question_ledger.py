import sys
from dataclasses import replace
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from question_ledger import (
    Admission, Advice, AdviceAssignment, AdviceContext, Alias, Assignment,
    Contribution, Disposition, Gap, HiddenWork, Ledger, LedgerError, Operation,
    Procedure, Question, Ref, Transition, compare,
)


def ref(name):
    from hashlib import sha256
    return Ref(name, sha256(name.encode()).hexdigest())


def arm(mode="FAST", input_mode="BANK_TO_PLAN"):
    sources = [ref(name) for name in
               ("original", "inventory", "cutoff", "tools", "routing", "method", "roles", "audit")]
    return Ledger(Procedure(input_mode, *sources), mode, 2)


def question(qid, risk=1, *, parent=None, mandatory=False):
    return Question(qid, "decision " + qid, "scope", "evidence " + qid,
                    ref("source " + qid), (risk, 1, 1), mandatory, parent)


def reserve(ledger, qid, number=1):
    who = Assignment(ref("assignment " + qid), f"attempt {number}", "actor", "OP-DIVERGENCE")
    operation = Operation(f"operation {qid}-{number}", f"fence {number}", who)
    ledger.apply(Admission(qid, operation))
    ledger.apply(Transition(operation, "RESERVED", ref(f"reservation {qid}-{number}")))
    return operation


def transition(ledger, operation, status, outcome=None, gap=None):
    ledger.apply(Transition(operation, status, ref(status + operation.id), outcome, gap))


def complete(ledger, qid, outcome="RESOLVED", gap=None):
    operation = reserve(ledger, qid)
    transition(ledger, operation, "CONFIRMED_SUBSTANTIVE")
    transition(ledger, operation, "CLOSED_AFTER_WORK", outcome, gap)


class QuestionLedgerTest(unittest.TestCase):
    def test_same_work_both_input_modes_derived_gap_in_fast(self):
        for input_mode in ("GOAL_TO_PLAN", "BANK_TO_PLAN"):
            with self.subTest(input_mode=input_mode):
                fast = arm("FAST", input_mode)
                detailed = arm("DETAILED", input_mode)
                for ledger in (fast, detailed):
                    for qid, risk in zip("ABCD", (5, 4, 2, 1)):
                        ledger.apply(question(qid, risk))
                    complete(ledger, "A")
                    second = reserve(ledger, "B")
                    transition(ledger, second, "CONFIRMED_SUBSTANTIVE")
                    ledger.apply(question("E", 6, parent="B"))
                    transition(ledger, second, "CLOSED_AFTER_WORK", "RESOLVED")
                for qid in "CDE":
                    fast.apply(Gap(qid, "capacity exhausted", ref("gap " + qid)))
                complete(detailed, "E", "OPEN", "Request receipt for the changed key")
                complete(detailed, "C")
                detailed.apply(Gap("D", "capacity exhausted", ref("gap D")))
                self.assertEqual(compare(fast, detailed), "COUNT_PASS")
                self.assertEqual(fast.started, {"A", "B"})
                self.assertEqual(detailed.started, {"A", "B", "C", "E"})
                fast.apply(HiddenWork("scope", ref("off-ledger evidence read")))
                self.assertEqual(compare(fast, detailed), "HIDDEN_WORK")

    def test_unknown_holds_until_authoritative_no_start_then_releases(self):
        ledger = arm()
        for qid in "ABC":
            ledger.apply(question(qid))
        first = reserve(ledger, "A")
        transition(ledger, first, "UNKNOWN_START")
        complete(ledger, "B")
        self.assertEqual(ledger.count_blocker(), "UNKNOWN_START")
        self.assertEqual(ledger.started, {"B"})
        with self.assertRaisesRegex(LedgerError, "question_capacity"):
            reserve(ledger, "C")
        with self.assertRaisesRegex(LedgerError, "fence_mismatch"):
            transition(ledger, replace(first, fence="wrong"), "CANCELLED_BEFORE_START")
        transition(ledger, first, "CANCELLED_BEFORE_START")
        ledger.apply(Gap("A", "proved no substantive start", ref("no-start proof")))
        complete(ledger, "C")
        self.assertEqual(ledger.started, {"B", "C"})
        self.assertIsNone(ledger.count_blocker())

    def test_abort_retry_via_alias_cannot_inflate_count(self):
        ledger = arm()
        ledger.apply(question("A"))
        ledger.apply(question("B"))
        ledger.apply(Alias("A-alt", "A", ref("source"), ref("independent equivalence")))
        first = reserve(ledger, "A")
        transition(ledger, first, "CONFIRMED_SUBSTANTIVE")
        transition(ledger, first, "ABORTED_AFTER_START")
        complete(ledger, "B")
        self.assertEqual(ledger.count_blocker(), "INCOMPLETE")
        retry = reserve(ledger, "A-alt", 2)
        transition(ledger, retry, "CONFIRMED_SUBSTANTIVE")
        transition(ledger, retry, "CLOSED_AFTER_WORK", "RESOLVED")
        self.assertEqual(ledger.started, {"A", "B"})
        self.assertEqual(set(ledger.closed), {"A", "B"})

    def test_queue_ack_and_cancelled_last_slot_do_not_meet_detailed_budget(self):
        detailed = arm("DETAILED")
        for qid in "ABCD":
            detailed.apply(question(qid))
        for qid in "ABC":
            complete(detailed, qid)
        fourth = reserve(detailed, "D")
        self.assertEqual(detailed.started, {"A", "B", "C"})
        with self.assertRaisesRegex(LedgerError, "invalid_question_transition"):
            transition(detailed, fourth, "CLOSED_AFTER_WORK", "RESOLVED")
        transition(detailed, fourth, "CANCELLED_BEFORE_START")
        detailed.apply(Gap("D", "cancelled without replacement", ref("recipient absence")))
        self.assertEqual(detailed.count_blocker(), "UNSATISFIED_COUNT")

    def test_odd_mandatory_inventory_and_different_depth(self):
        fast, detailed = arm(), arm("DETAILED")
        for ledger in (fast, detailed):
            for qid in "ABCDE":
                ledger.apply(question(qid, mandatory=True))
        self.assertEqual(compare(fast, detailed), "UNPAIRED_MANDATORY_SCOPE")
        detailed.procedure = replace(detailed.procedure, methodology=ref("shorter depth"))
        self.assertEqual(compare(fast, detailed), "UNPAIRED_PROCEDURE")

    def test_advice_freshness_exposure_and_affected_freeze(self):
        ledger = arm()
        advice = Advice(ledger)
        context = AdviceContext("CORE", "BANK_TO_PLAN", "FAST", ledger.procedure.original,
                                ref("framing"), ref("graph"), ref("exposure"), "scope",
                                "Does the changed key require a different receipt?",
                                (ref("evidence"),), (ref("decision"),), ref("packet"))
        adviser = Assignment(ref("adviser assignment"), "1", "adviser", "ADVISER")
        assignment = AdviceAssignment(adviser, context, "OP-SYNTHESIS")
        with self.assertRaisesRegex(LedgerError, "context_stale"):
            advice.assign(assignment, replace(context, exposure=ref("revoked exposure")))
        advice.assign(assignment, context)
        contribution = Contribution(adviser, context.packet, ref("result"), "OBJECTION",
                                    True, "ROUTING_ONLY")
        with self.assertRaisesRegex(LedgerError, "context_stale"):
            advice.contribute(contribution, replace(context, graph=ref("new graph")))
        with self.assertRaisesRegex(LedgerError, "work_classification"):
            advice.contribute(replace(contribution, work_kind="SUBSTANTIVE"), context)
        advice.contribute(contribution, context)
        self.assertEqual(advice.freeze_blockers({"unrelated"}), ())
        self.assertEqual(advice.freeze_blockers({"scope"}), ("result",))
        owner = Assignment(ref("owner assignment"), "1", "owner", "OP-SYNTHESIS")
        disposition = Disposition(contribution.result, owner, "QUALIFIED",
                                  "Revisit after recipient evidence", ref("review"))
        with self.assertRaisesRegex(LedgerError, "disposition_authority"):
            advice.dispose(replace(disposition, authority=adviser))
        advice.dispose(disposition)
        self.assertEqual(advice.freeze_blockers({"scope"}), ())



class OrdinaryQuestionCommandTest(unittest.TestCase):
    """Exercise real CLI publication on a public TEST_ONLY BANK fixture."""

    def setUp(self):
        import json
        import subprocess
        import tempfile
        import qs

        self.qs=qs
        self.json=json
        self.subprocess=subprocess
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name)
        self.calls=0
        self.bank={"requirements":["R-1"],"questions":[
            {"id":qid,"text":"Observe "+qid,"source_offset":qid} for qid in "ABC"
        ]}
        bank=self.put("bank.yaml",self.bank)
        goal=self.base/"goal.txt"
        goal.write_text("Retain every obligation and distinguish an ACK from work.\n")
        vector=self.put("vector.yaml",{
            "id":"VECTOR-TEST","run_id":"SOURCE","input_snapshot":"c"*64,"revision":"1",
            "author_role":"OP-VECTOR","agent_id":"fixture-vector","artifact_type":"OPERATOR",
            "G":"Full bounded goal","NOT_G":["Narrowing"],"value_recipient":"fixture owner","scope":"fixture scope",
            "success_criteria":["check every requirement"],"requirements":["R-1"],
            "hard_constraints":["use fixture evidence"],"changeable_assumptions":["routing"],
            "current_state":"Fixture bank has the question Observe A","resources":"local","authority":"fixture owner",
            "owner_decisions":[],"open_research_questions":["Observe B"],
            "requirement_definitions":[{"id":"R-1","text":"Cover every source obligation",
                                        "acceptance_locator":"Observe C"}],
        })
        self.vector_sha=qs.sha(vector.read_bytes())
        approval=self.put("approval.yaml",{
            "approval_id":"APPROVAL-TEST","decision":"APPROVE","approval_mode":"SIMULATED_TEST",
            "approver_id":"ROOT-AS-SIMULATED_TEST_OWNER","authority_basis":"Fixture","observation_provenance":"TEST_ONLY",
            "run_id":"SOURCE","input_snapshot":"c"*64,"vector_result_id":"VECTOR-TEST",
            "vector_sha256":self.vector_sha,"vector_revision":"1","statement_sha256":qs.sha(goal.read_bytes()),
            "fixture_only":"YES",
        })
        self.approval_sha=qs.sha(approval.read_bytes())
        procedure=self.put("procedure.yaml",{field:"frozen "+field for field in qs.QUESTION_PROCEDURE_FIELDS})
        self.init_args=("--mode","BANK_TO_PLAN","--goal",str(goal),
            "--bank",str(bank),"--approved-vector",str(vector),"--vector-approval",str(approval),
            "--approval-statement",str(goal),"--test-fixture","--effort","FAST","--half-budget","2",
            "--question-procedure",str(procedure))
        self.snapshot=self.call("init",*self.init_args)["input_snapshot"]
        self.bank_sha=self.qs.sha(bank.read_bytes())

    def put(self,name,document):
        path=self.base/name
        path.write_bytes(self.qs.canon_yaml(document))
        return path

    def call(self,verb,*args,exit_code=0,run_id="ARM"):
        self.calls+=1
        self.assertLessEqual(self.calls,30)
        process=self.subprocess.run(
            [sys.executable,"-B",str(Path(self.qs.__file__)),verb,"--root",str(self.base),"--run-id",run_id,*args],
            text=True,capture_output=True,timeout=20,check=False,
        )
        self.assertEqual(process.returncode,exit_code,process.stdout+process.stderr)
        return self.json.loads(process.stdout or process.stderr)

    def assign(self,label,kind,role,agent,extra=None,*,exit_code=0):
        artifact_dir={"EVIDENCE":"evidence","REVIEW":"reviews","FRAMING":"positions","PRIMARY":"positions"}[kind]
        assignment={"assignment_id":"A-"+label,"attempt_id":"TRY-1","role_id":role,"agent_id":agent,
                    "input_snapshot":self.snapshot,"lifecycle_scope":"PREP","prep_stage":"PREP-4_ADMIT",
                    "artifact_type":kind,"output_path":artifact_dir+"/"+label+".yaml","selection_authority":"ROOT",
                    "dependency_hashes":[],"result_id":label,**(extra or {})}
        path=self.put(label+"-assignment.yaml",assignment)
        response=self.call("assign","--assignment",str(path),exit_code=exit_code)
        if exit_code:
            return response
        return assignment,path

    def finish(self,assignment,path,extra):
        label=assignment["result_id"]
        doc={"schema_version":self.qs.SCHEMA,"id":label,"run_id":"ARM","input_snapshot":self.snapshot,
             "author_role":assignment["role_id"],"agent_id":assignment["agent_id"],
             "knowledge_status":"OBSERVED","workflow_status":"ACCEPTED",
             "source_refs":[],"related_ids":[],"revision":"1","created_at":"2026-01-01T00:00:00Z",
             "updated_at":"2026-01-01T00:00:00Z","assignment_id":assignment["assignment_id"],
             "attempt_id":assignment["attempt_id"],"artifact_type":assignment["artifact_type"],
             "lifecycle_scope":"PREP","prep_stage":assignment["prep_stage"],**extra}
        result=self.put(label+"-result.yaml",doc)
        self.call("commit","--assignment",str(path),"--result",str(result))
        return self.qs.sha(result.read_bytes())

    def accept(self,label,kind,role,agent,extra):
        assignment,path=self.assign(label,kind,role,agent)
        return self.finish(assignment,path,extra)

    def context(self,assignment,path,*flags,exit_code=0):
        output=self.base/"runs"/"ARM"/"state"/"contexts"/(
            assignment["assignment_id"]+"--"+assignment["attempt_id"]+".yaml"
        )
        return self.call("context","--assignment",str(path),"--output",str(output),*flags,exit_code=exit_code),output

    def reviewer_framing(self,before_context=None):
        profiles=[]
        for number in range(1,11):
            role=f"P-{number:02d}"
            actor={1:"fixture-worker",2:"fixture-reviewer"}.get(number,f"fixture-{role}")
            profile={"role_id":role,"agent_id":actor,**{
                name:f"{role} {name}" for name in self.qs.PERSPECTIVE_PROFILE_FIELDS if name not in {"role_id","agent_id"}
            }}
            profiles.append({**profile,"profile_sha256":self.qs._perspective_profile_hash(profile)})
        map_path=self.put("perspectives.yaml",{
            "schema_version":self.qs.SCHEMA,"id":"MAP-ARM","run_id":"ARM","input_snapshot":self.snapshot,
            "vector_result_id":"VECTOR-TEST","vector_sha256":self.vector_sha,"approval_sha256":self.approval_sha,
            "selection_authority":"ROOT","perspectives":profiles,
        })
        self.call("publish-perspective-map","--perspective-map",str(map_path))
        profile={key:value for key,value in profiles[1].items() if key!="profile_sha256"}
        framing,path=self.assign("FRAME","FRAMING","P-02","fixture-reviewer",{
            "prep_stage":"PREP-2_PERSPECTIVES","perspective_profile":profile,
            "perspective_profile_hash":profiles[1]["profile_sha256"],"goal_result_id":"VECTOR-TEST",
            "vector_sha256":self.vector_sha,"approval_sha256":self.approval_sha,
            "role_instruction_paths":["references/roles/perspectives.md"],
        })
        if before_context:
            before_context(framing,path)
        context,output=self.context(framing,path)
        packet=self.qs.load_yaml(output)
        self.assertEqual(packet["blind"],"true")
        self.assertEqual(packet["bank"],"ABSENT")
        for leaked in ("Observe A","Observe B","Observe C","acceptance_locator"):
            self.assertNotIn(leaked,output.read_text())
        self.assertEqual(packet["goal"]["requirement_definitions"],[{"id":"R-1","text":"Cover every source obligation"}])
        return self.finish(framing,path,{
            "perspective_profile_hash":profiles[1]["profile_sha256"],"vector_result_id":"VECTOR-TEST",
            "vector_sha256":self.vector_sha,"approval_sha256":self.approval_sha,"context_packet_sha256":context["sha256"],
            "blind_input":{"contains_bank":"NO","contains_foreign_p_results":"NO",
                           "contains_preferred_solution":"NO","allowed":["own framing packet"]},
            "initial_questions":[],
            "iceberg_position":{"position_id":"ICEPOS-P-02","origin_role":"P-02",
                "origin_assignment_id":framing["assignment_id"],"input_snapshot":self.snapshot,
                "elements":[{"seed_id":"SEED-P-02","candidate_view":"CURRENT_REALITY","element_kind":"ASSUMPTION",
                    "statement":"Fixture observation may be incomplete","scope":"frozen fixture",
                    "g_refs":["VECTOR-TEST"],"r_refs":["R-1"],"materiality":"Changes applicability",
                    "knowledge_status":"HYPOTHESIS","source_refs":[],
                    "finite_check":{"check_kind":"OBSERVATION","procedure":"Observe the bound fixture",
                                    "discriminating_outcomes":["supports","refutes"]}}],"relations":[]},
        })

    def review(self,label,target,target_sha,framing_sha,extra=None,*,expand=True,additional=None,revise=False):
        dependencies={target:target_sha} if expand else {}
        dependencies.update(additional or {})
        assignment,path=self.assign(label,"REVIEW","P-02","fixture-reviewer",{
            "dependency_hashes":list(dependencies.values()),"dependency_result_ids":list(dependencies),
        })
        flags=tuple(flag for item in dependencies for flag in ("--expand",item))
        if revise:
            bounded,original=self.context(assignment,path)
            original_bytes=original.read_bytes()
            expanded=self.base/(label+"-expanded.yaml")
            context=self.call("context","--assignment",str(path),"--output",str(expanded),*flags)
            self.assertNotEqual(context["sha256"],bounded["sha256"])
            self.assertEqual(original.read_bytes(),original_bytes)
        else:
            context,_=self.context(assignment,path,*flags)
        document={
            "target_result_id":target,"target_hash":target_sha,"outcome":"PASS","review_kind":"SUBJECT",
            "blind_input_result_id":"FRAME","blind_input_hash":framing_sha,"context_packet_sha256":context["sha256"],
            "required_repair":[],"regression":[],"independence_limits":[],
            **{field:"Fixture reviewer checked exact "+field for field in (
                "reviewer_competence","nonauthor_declaration","check_id","check_provenance","risk_coverage",
                "procedure","risk_tested","claim_scope","counterexample_or_observation",
            )},
            **(extra or {}),
        }
        return self.finish(assignment,path,document)

    def request(self,key,actor,authority_sha256,**fields):
        cut=self.call("command-cut")
        return self.put(key+"-request.yaml",{
            "key":key,"actor":actor,"authority_sha256":authority_sha256,
            **{field:cut[field] for field in ("run_id","input_snapshot","expected_revision")},**fields,
        })

    def register_inventory(self,framing,*,excluded=None,revise=False):
        inventory={"source_sha256":self.bank_sha,"original_sha256":self.bank_sha,
                   "questions":[{"id":qid,"decision":"D-"+qid,"scope":"R-1","evidence_route":"E-"+qid,
                                 "priority":[str(9-index),"0","0"],"mandatory":"NO",
                                 **({"exclusion":"Explicit frozen fixture exclusion"} if qid==excluded else {})}
                                for index,qid in enumerate("ABC")],"aliases":[]}
        source=self.accept("INVENTORY","EVIDENCE","OP-DIVERGENCE","fixture-source",{"question_inventory":inventory})
        review=self.review("INVENTORY-REVIEW","INVENTORY",source,framing,revise=revise)
        request=self.request("register","fixture-reviewer",review,authority_result_id="INVENTORY-REVIEW")
        receipt=self.call("question-register","--request",str(request))
        return request,receipt

    def test_revised_inventory_review_registers_and_replays(self):
        request,receipt=self.register_inventory(self.reviewer_framing(),revise=True)
        self.assertEqual(self.call("recover")["status"],"RECOVERED")
        self.assertEqual(self.call("question-register","--request",str(request)),receipt)
        cut=self.call("command-cut")
        self.assertEqual(cut["question_start_count"],0)

    def test_bank_review_cannot_see_a_target_before_committing_its_frame(self):
        target=self.accept("SENSITIVE","EVIDENCE","OP-DIVERGENCE","fixture-source",{"observation":"Observe A"})
        review,path=self.assign("EARLY-REVIEW","REVIEW","P-02","fixture-reviewer",{
            "dependency_hashes":[target],"dependency_result_ids":["SENSITIVE"],
        })

        def before_frame(_assignment,_path):
            denied,output=self.context(review,path,"--expand","SENSITIVE",exit_code=2)
            self.assertEqual(denied["error"],"bank_research_requires_accepted_blind_framing")
            self.assertFalse(output.exists())

        self.reviewer_framing(before_context=before_frame)
        accepted,output=self.context(review,path,"--expand","SENSITIVE")
        self.assertEqual(accepted["status"],"CONTEXT_READY")
        self.assertIn("Observe A",output.read_text())

    def test_bank_raw_readset_requires_prior_accepted_framing(self):
        reader,path=self.assign("RAW-BANK","EVIDENCE","P-02","fixture-reviewer",{
            "allowed_reads":["input/bank.yaml"],
        })

        def before_frame(_assignment,_path):
            denied,output=self.context(reader,path,exit_code=2)
            self.assertEqual(denied["error"],"bank_research_requires_accepted_blind_framing")
            self.assertFalse(output.exists())

        self.reviewer_framing(before_context=before_frame)
        receipt,output=self.context(reader,path)
        self.assertEqual(receipt["status"],"CONTEXT_READY")
        packet=self.qs.load_yaml(output)
        self.assertEqual((packet["questions"],packet["accepted_dependencies"]),([],[]))
        self.assertEqual(packet["readset"][0]["sha256"],self.bank_sha)
        self.assertIn("Observe A",packet["readset"][0]["content"])
        frame=self.qs.load_yaml(self.base/"FRAME-assignment.yaml")
        denied=self.assign("FRAME-LATE","FRAMING","P-02","fixture-reviewer",{
            **frame,"assignment_id":"A-FRAME-LATE","result_id":"FRAME-LATE","output_path":"positions/FRAME-LATE.yaml",
        },exit_code=2)
        self.assertEqual(denied["error"],"bank_framing_actor_already_exposed")

    def test_current_inventory_gap_proof_is_required_at_comparison_cut(self):
        self.register_inventory(self.reviewer_framing(),excluded="C")
        self.call("init",*self.init_args,run_id="PAIR")
        rejected=self.call("question-compare","--other-run-id","PAIR",exit_code=2)
        self.assertEqual(rejected["error"],"question_comparison_arm_required")
        self.assign("REVISED-INVENTORY-REVIEW","REVIEW","P-02","fixture-reviewer",{
            "assignment_id":"A-INVENTORY-REVIEW","attempt_id":"TRY-2","supersedes_attempt_id":"TRY-1",
        })
        rejected=self.call("question-compare","--other-run-id","PAIR",exit_code=2)
        self.assertEqual(rejected["error"],"assignment_attempt_superseded")

    def test_claim_cannot_send_unaccounted_work_or_stale_adviser(self):
        self.register_inventory(self.reviewer_framing())
        worker,worker_path=self.assign("UNACCOUNTED","PRIMARY","P-01","fixture-worker",{
            "logical_occurrence_id":"O-UNACCOUNTED","launch_state":"READY_TO_DISPATCH",
            "assigned_ids":{"questions":["A"]},
        })
        request=self.request("unaccounted","fixture-worker",self.qs.sha(worker_path.read_bytes()),**self.occurrence(worker))
        run=self.base/"runs"/"ARM"
        before=self.qs.command_journal(run)
        denied=self.call("claim-effect","--request",str(request),"--external-dispatch",exit_code=2)
        self.assertEqual(denied["error"],"question_substantive_context_unreserved")
        self.assertEqual(self.qs.command_journal(run),before)

        self.accept("RECIPIENT","EVIDENCE","P-02","fixture-reviewer",{})
        adviser,path=self.assign("ADVICE","EVIDENCE","ADVISER","fixture-fresh",self.adviser_assignment("A-RECIPIENT"))
        facts=self.occurrence(adviser)
        authority=self.qs.sha(path.read_bytes())
        issue=self.request("issue","fixture-fresh",authority,**facts)
        self.call("adviser-assign","--request",str(issue))
        self.assign("RECIPIENT-RETRY","EVIDENCE","P-02","fixture-reviewer",{
            "assignment_id":"A-RECIPIENT","attempt_id":"TRY-2","supersedes_attempt_id":"TRY-1",
        })
        claim=self.request("claim","fixture-fresh",authority,**facts)
        before=self.qs.command_journal(self.base/"runs"/"ARM")
        denied=self.call("claim-effect","--request",str(claim),"--external-dispatch",exit_code=2)
        self.assertEqual(denied["error"],"adviser_context_unissued_or_stale")
        self.assertEqual(self.qs.command_journal(self.base/"runs"/"ARM"),before)

    def test_reservation_is_journaled_once_and_ack_does_not_count(self):
        self.register_inventory(self.reviewer_framing())
        original=self.base/"FRAME-assignment.yaml"
        self.assertEqual(self.call("assign","--assignment",str(original))["status"],"ASSIGNED")
        duplicate=self.put("duplicate-framing.yaml",{
            **self.qs.load_yaml(original),"assignment_id":"A-DUPLICATE-FRAME","result_id":"DUPLICATE-FRAME",
            "output_path":"positions/DUPLICATE-FRAME.yaml",
        })
        self.assertEqual(self.call("assign","--assignment",str(duplicate),exit_code=2)["error"],
                         "bank_framing_actor_already_exposed")
        occurrence={"assignment_id":"A-WORK","attempt_id":"TRY-1","logical_occurrence_id":"O-WORK"}
        _,path=self.assign("WORK","PRIMARY","P-01","fixture-worker",{
            "logical_occurrence_id":"O-WORK","launch_state":"READY_TO_DISPATCH",
            "assigned_ids":{"questions":["A","B"]},"question_work":{"lead":["A","B"],"support":[]},
        })
        work_hash=self.qs.sha(path.read_bytes())
        claim=self.request("claim","fixture-worker",work_hash,**occurrence)
        flags=("--request",str(claim),"--external-dispatch")
        self.assertEqual(self.call("claim-effect",*flags)["dispatch_action"],"SEND_ONCE")
        self.assertEqual(self.call("claim-effect",*flags)["dispatch_action"],"HOLD_RECONCILE")
        self.assertEqual(self.call("command-cut")["question_start_count"],0)
        proof=self.accept("PROOF","EVIDENCE","OP-DIVERGENCE","fixture-observer",{
            "effect_reconciliation":{**occurrence,"assignment_sha256":work_hash,"claim_key":"claim",
             "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"TEST_ONLY receipt",
             "recipient_agent_id":"fixture-worker",
             "question_starts":[{"question_id":"A","substantive_action":"TEST_ONLY first authorized observation"}]},
        })
        reconcile=self.request("reconcile","fixture-observer",proof,authority_result_id="PROOF",**occurrence)
        self.call("reconcile-effect","--request",str(reconcile))
        cut=self.call("command-cut")
        self.assertEqual((cut["question_start_count"],cut["question_close_count"]), (1,0))
        self.assertEqual(cut["question_count_blocker"],"UNKNOWN_START")
        self.assertEqual(self.call("recover")["status"],"RECOVERED")
        self.assertEqual(self.call("command-cut")["question_start_count"],1)

    def test_two_actual_starts_and_exact_independent_review_close_two_units(self):
        framing=self.reviewer_framing()
        self.register_inventory(framing)
        assignment,path=self.assign("LEAD","PRIMARY","P-01","fixture-worker",{
            "logical_occurrence_id":"O-LEAD","launch_state":"READY_TO_DISPATCH",
            "assigned_ids":{"questions":["A","B"]},"question_work":{"lead":["A","B"],"support":[]},
        })
        facts=self.occurrence(assignment)
        authority=self.qs.sha(path.read_bytes())
        claim=self.request("claim","fixture-worker",authority,**facts)
        self.call("claim-effect","--request",str(claim),"--external-dispatch")
        context,_=self.context(assignment,path)
        receipt=self.accept("STARTS","EVIDENCE","OP-DIVERGENCE","fixture-observer",{
            "effect_reconciliation":{**facts,"assignment_sha256":authority,"claim_key":"claim",
                "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"TEST_ONLY two observed actions",
                "recipient_agent_id":"fixture-worker",
                "question_starts":[{"question_id":qid,"substantive_action":"Observed independent fixture "+qid}
                                   for qid in "AB"]},
        })
        observed=self.request("observed","fixture-observer",receipt,authority_result_id="STARTS",**facts)
        self.call("reconcile-effect","--request",str(observed))
        answer=self.finish(assignment,path,{"context_packet_sha256":context["sha256"],
                                           "addressed_q_ids":["A","B"]})
        procedure=self.qs.load_yaml(self.base/"procedure.yaml")
        close=[]
        for qid in "AB":
            close.append({"question_id":qid,"operation_id":"claim:"+qid,"claim_key":"claim",
                          "methodology_sha256":self.qs.sha(self.qs.canon_yaml(procedure["methodology"])),
                          "review_and_audit_sha256":self.qs.sha(self.qs.canon_yaml(procedure["review_and_audit"])),
                          "decision":"D-"+qid,"evidence_route":"E-"+qid,"outcome":"RESOLVED",
                          "depth_observation":"Applied exact frozen procedure to "+qid})
        review=self.review("CLOSE","LEAD",answer,framing,{"question_close":close})
        request=self.request("close","fixture-reviewer",review,authority_result_id="CLOSE")
        self.call("question-close","--request",str(request))
        cut=self.call("command-cut")
        self.assertEqual((cut["question_start_count"],cut["question_close_count"]), (2,2))
        self.assertEqual(cut["question_count_blocker"],"UNDISPOSED_QUESTION")

    def adviser_assignment(self,recipient):
        return {"logical_occurrence_id":"O-ADVICE","launch_state":"READY_TO_DISPATCH",
                "adviser":{"address":"PREP-4_ADMIT","recipient_assignment_id":recipient,
                           "work_kind":"ROUTING_ONLY"}}

    def occurrence(self,assignment):
        return {field:assignment[field] for field in ("assignment_id","attempt_id","logical_occurrence_id")}

    def prep_adviser_holds(self,scopes):
        from argparse import Namespace

        def read_holds(_args,run):
            state,current,bank=self.qs.accepted(run)
            return self.qs._adviser_freeze_blockers(
                run,(state,current,bank,self.qs.command_journal(run)),"PREP",scopes,
            )
        return self.qs.locked_command(Namespace(root=str(self.base),run_id="ARM"),read_holds)

    def test_fresh_distinct_adviser_and_explicit_historical_rejection(self):
        framing=self.reviewer_framing()
        self.accept("RECIPIENT","EVIDENCE","P-02","fixture-reviewer",{})
        adviser,path=self.assign("ADVICE","EVIDENCE","ADVISER","fixture-fresh-adviser",
                                 self.adviser_assignment("A-RECIPIENT"))
        facts=self.occurrence(adviser)
        authority=self.qs.sha(path.read_bytes())
        request=self.request("issue","fixture-fresh-adviser",authority,**facts)
        issue=self.qs.load_yaml(request)
        forged=self.put("forged.yaml",{**issue,"authority_sha256":"f"*64})
        self.assertEqual(self.call("adviser-assign","--request",str(forged),exit_code=2)["error"],
                         "command_authority_binding")
        self.call("adviser-assign","--request",str(request))
        claim=self.request("claim","fixture-fresh-adviser",authority,**facts)
        self.assertEqual(self.call("claim-effect","--request",str(claim),"--external-dispatch")["dispatch_action"],"SEND_ONCE")
        context,output=self.context(adviser,path)
        packet=self.qs.load_yaml(output)
        self.assertEqual((packet["packet_type"],packet["role_id"],packet["evidence"],packet["decisions"]),
                         ("ADVISER_CONTEXT","ADVISER",[],[]))
        self.assertEqual(packet["framing"]["sha256"],self.vector_sha)
        receipt=self.accept("ADVICE-DISPATCH","EVIDENCE","OP-DIVERGENCE","fixture-observer",{
            "effect_reconciliation":{**facts,"assignment_sha256":authority,"claim_key":"claim",
                "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"TEST_ONLY exact observed adviser",
                "recipient_agent_id":"fixture-fresh-adviser"},
        })
        confirmed=self.request("confirm","fixture-observer",receipt,authority_result_id="ADVICE-DISPATCH",**facts)
        self.call("reconcile-effect","--request",str(confirmed))
        contribution=self.finish(adviser,path,{
            "context_packet_sha256":context["sha256"],
            "advice_contribution":{"kind":"OBJECTION","material":"YES","rationale":"Risk needs separate proof"},
        })
        submit=self.request("contribute","fixture-fresh-adviser",contribution,authority_result_id="ADVICE")
        self.call("adviser-contribute","--request",str(submit))
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),("adviser_disposition_required:ADVICE",))
        self.assertEqual(self.prep_adviser_holds({"PREP-3_EXPAND_NORMALIZE"}),())
        self.assign("RECIPIENT-RETRY","EVIDENCE","P-02","fixture-reviewer",{
            "assignment_id":"A-RECIPIENT","attempt_id":"TRY-2","supersedes_attempt_id":"TRY-1",
        })
        rejected,_=self.context(adviser,path,exit_code=2)
        self.assertEqual(rejected["error"],"adviser_context_unissued_or_stale")
        stale_review,stale_path=self.assign("STALE-FULL","REVIEW","P-02","fixture-reviewer",{
            "dependency_hashes":[contribution],"dependency_result_ids":["ADVICE"],
        })
        denied,_=self.context(stale_review,stale_path,"--expand","ADVICE",exit_code=2)
        self.assertEqual(denied["error"],"adviser_dependency_context_stale")
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),("adviser_disposition_required:ADVICE",))
        proof=self.review("RETIRE","ADVICE",contribution,framing,{
            "advice_disposition":{"status":"REJECTED","rationale":"Recipient changed; retain original objection",
                                  "retired_context_sha256":context["sha256"]},
        },expand=False)
        retire=self.request("retire","fixture-reviewer",proof,authority_result_id="RETIRE")
        self.call("adviser-dispose","--request",str(retire))
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),())

    def test_adviser_role_cannot_replace_required_role_or_expand_readset(self):
        self.reviewer_framing()
        self.assign("RECIPIENT","EVIDENCE","P-02","fixture-reviewer")
        config=self.adviser_assignment("A-RECIPIENT")
        self.assertEqual(self.assign("WRONG-KIND","PRIMARY","ADVISER","fixture-fresh",config,exit_code=2)["error"],
                         "assignment_role_or_type")
        self.assertEqual(self.assign("COLLISION","EVIDENCE","ADVISER","fixture-reviewer",config,exit_code=2)["error"],
                         "adviser_requires_distinct_actor")
        adviser,path=self.assign("EXCLUDED","EVIDENCE","ADVISER","fixture-fresh",{
            **config,"assigned_ids":{"evidence":["EXCLUDED-EVIDENCE"]},
        })
        denied,_=self.context(adviser,path,"--expand","EXCLUDED-EVIDENCE",exit_code=2)
        self.assertEqual(denied["error"],"adviser_context_no_expansion")
        denied,_=self.context(adviser,path,"--historical",exit_code=2)
        self.assertEqual(denied["error"],"adviser_historical_context_not_exposed")
        denied=self.call("graph-query","--assignment",str(path),"--duty","KNOWLEDGE","--ref","A",exit_code=2)
        self.assertEqual(denied["error"],"adviser_graph_requires_current_context")
        denied,_=self.context(adviser,path,exit_code=2)
        self.assertEqual(denied["error"],"adviser_assignment_receipt_required")
        self.assertEqual(self.assign("REUSE","EVIDENCE","P-01","fixture-fresh",exit_code=2)["error"],
                         "adviser_requires_distinct_actor")
        request=self.request("excluded","fixture-fresh",self.qs.sha(path.read_bytes()),**self.occurrence(adviser))
        self.assertEqual(self.call("adviser-assign","--request",str(request),exit_code=2)["error"],
                         "adviser_exposure_expansion")
        blind,path=self.assign("BLIND-RECIPIENT","EVIDENCE","ADVISER","fixture-fresh",
                               self.adviser_assignment("A-FRAME"))
        request=self.request("blind","fixture-fresh",self.qs.sha(path.read_bytes()),**self.occurrence(blind))
        self.assertEqual(self.call("adviser-assign","--request",str(request),exit_code=2)["error"],
                         "adviser_recipient_lifecycle")

    def nonperspective_disposition(self,*,decision_attempt="TRY-1"):
        framing=self.reviewer_framing()
        self.assign("RECIPIENT","EVIDENCE","OP-DIVERGENCE","fixture-operator")
        adviser,path=self.assign("ADVICE","EVIDENCE","ADVISER","fixture-adviser",self.adviser_assignment("A-RECIPIENT"))
        facts=self.occurrence(adviser)
        authority=self.qs.sha(path.read_bytes())
        issue=self.request("issue","fixture-adviser",authority,**facts)
        self.call("adviser-assign","--request",str(issue))
        claim=self.request("claim","fixture-adviser",authority,**facts)
        self.call("claim-effect","--request",str(claim),"--external-dispatch")
        context,_=self.context(adviser,path)
        dispatched=self.accept("OBSERVED","EVIDENCE","OP-PROJECTION","fixture-observer",{
            "effect_reconciliation":{**facts,"assignment_sha256":authority,"claim_key":"claim",
                "disposition":"RECIPIENT_CONFIRMED","authoritative_receipt":"TEST_ONLY observed operator adviser",
                "recipient_agent_id":"fixture-adviser"},
        })
        confirmation=self.request("confirm","fixture-observer",dispatched,authority_result_id="OBSERVED",**facts)
        self.call("reconcile-effect","--request",str(confirmation))
        contribution=self.finish(adviser,path,{
            "context_packet_sha256":context["sha256"],
            "advice_contribution":{"kind":"ALTERNATIVE","material":"YES","rationale":"Compare a second baseline"},
        })
        submit=self.request("contribute","fixture-adviser",contribution,authority_result_id="ADVICE")
        self.call("adviser-contribute","--request",str(submit))
        decision,path=self.assign("DECISION","EVIDENCE","OP-DIVERGENCE","fixture-operator",{
            "dependency_hashes":[contribution],"dependency_result_ids":["ADVICE"],
        })
        recipient_context,_=self.context(decision,path,"--expand","ADVICE")
        decision_sha=self.finish(decision,path,{
            "context_packet_sha256":recipient_context["sha256"],
            "advice_disposition":{"target_result_id":"ADVICE","target_sha256":contribution,
                "context_sha256":context["sha256"],"recipient_assignment_id":"A-RECIPIENT",
                "recipient_attempt_id":decision_attempt,"status":"QUALIFIED",
                "rationale":"Compare only when both baselines share the cutoff"},
        })
        if decision_attempt!="TRY-1":
            attempt,attempt_path=self.assign("MISMATCHED-REVIEW","REVIEW","P-02","fixture-reviewer",{
                "dependency_result_ids":["ADVICE","DECISION"],"dependency_hashes":[contribution,decision_sha],
            })
            denied,output=self.context(attempt,attempt_path,"--expand","DECISION","--expand","ADVICE",exit_code=2)
            self.assertEqual(denied["error"],"adviser_recipient_decision_binding")
            self.assertFalse(output.exists())
        else:
            attempt,attempt_path=self.assign("UNRELATED-REVIEW","REVIEW","P-02","fixture-reviewer",{
                "dependency_result_ids":["ADVICE","OBSERVED"],"dependency_hashes":[contribution,dispatched],
            })
            denied,output=self.context(attempt,attempt_path,"--expand","OBSERVED","--expand","ADVICE",exit_code=2)
            self.assertEqual(denied["error"],"adviser_dependency_recipient")
            self.assertFalse(output.exists())
        review=self.review("DECISION-REVIEW","DECISION",decision_sha,framing,
                           additional={"ADVICE":contribution} if decision_attempt=="TRY-1" else None)
        request=self.request("dispose","fixture-reviewer",review,authority_result_id="DECISION-REVIEW")
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),("adviser_disposition_required:ADVICE",))
        return request,decision_sha

    def test_operator_disposition_requires_own_decision_and_independent_review(self):
        request,decision_sha=self.nonperspective_disposition()
        receipt=self.call("adviser-dispose","--request",str(request))
        self.assertEqual((receipt["recipient_assignment_id"],receipt["recipient_attempt_id"],
                          receipt["decision_result_id"],receipt["decision_sha256"]),
                         ("A-RECIPIENT","TRY-1","DECISION",decision_sha))
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),())

    def test_operator_review_cannot_authorize_a_different_recipient_attempt(self):
        request,_=self.nonperspective_disposition(decision_attempt="WRONG-ATTEMPT")
        denied=self.call("adviser-dispose","--request",str(request),exit_code=2)
        self.assertEqual(denied["error"],"adviser_recipient_decision_binding")
        self.assertEqual(self.prep_adviser_holds({"PREP-4_ADMIT"}),("adviser_disposition_required:ADVICE",))


class AdviserPlanTransitionTest(unittest.TestCase):
    def test_context_requires_exact_advice_grant_for_prospective_plan_review(self):
        from unittest.mock import patch

        import qs
        from question_admission import Arm, prepare_question_receipt
        from test_architecture_revision_context import ArchitectureRevisionContextTest

        fixture=ArchitectureRevisionContextTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        model=fixture.fixture
        run=model.run
        stage_id=fixture.replacement("S-000")
        model.add_result("ADVICE-CORE","EVIDENCE","ADVISER","fixture-adviser","evidence/ADVICE-CORE.yaml")
        model.add_result("DISPOSITION-CORE","REVIEW","P-03","fixture-disposer","reviews/DISPOSITION-CORE.yaml")
        advice,proof=(Ref(rid,model.results[rid]["content_hash"]) for rid in ("ADVICE-CORE","DISPOSITION-CORE"))

        procedure=arm(input_mode="GOAL_TO_PLAN").procedure
        authority=Arm(procedure,"FAST",2)
        adviser=Assignment(ref("adviser-assignment"),"FIRST","fixture-adviser","ADVISER")
        recipient=Assignment(ref("recipient-assignment"),"FIRST","fixture-recipient","OP-SYNTHESIS")
        context=AdviceContext("CORE","GOAL_TO_PLAN","FAST",procedure.original,
                              ref("vector"),ref("graph"),ref("exposure"),"R-001","R-001",
                              (),(),ref("packet"))
        contribution=Contribution(adviser,context.packet,advice,"OBJECTION",True,"ROUTING_ONLY")
        disposition=Disposition(advice,recipient,"QUALIFIED","Retain only with a check",proof)
        journal=[{"operation":"adviser-dispose","receipt":prepare_question_receipt((),{},[
            (authority,None),(AdviceAssignment(adviser,context,recipient.role),context),
            (contribution,context),(disposition,None),
        ])}]

        stage_doc=model.docs[stage_id]
        stage_doc["advice_consumption"]=[{
            "advice_result_id":advice.id,"advice_sha256":advice.sha256,
            "disposition_result_id":proof.id,"disposition_sha256":proof.sha256,
            "context_sha256":context.packet.sha256,"scope":context.scope,
        }]
        model.sync(stage_id)
        stage=Ref(stage_id,model.results[stage_id]["content_hash"])
        stage_registered=fixture.state["registered"]["ASG-S-000\0ATT-2"]
        stage_registered["assignment"].update(dependency_result_ids=[advice.id,proof.id],
                                              dependency_hashes=[advice.sha256,proof.sha256])
        stage_packet={"accepted_dependencies":[
            {"source":{"result_id":source.id,"sha256":source.sha256},
             "projection":{"view":{"kind":"FULL_LINKED_EXPANSION"}}}
            for source in (advice,proof)
        ]}

        def accepted_result(_run,_view,result_id,kind,**_options):
            entry=model.results[result_id]
            self.assertEqual(entry["artifact_type"],kind)
            return Ref(result_id,entry["content_hash"]),model.docs[result_id],(
                stage_registered if result_id==stage.id else {}
            )

        def result_packet(_run,_view,registered,_document):
            return stage_packet if registered is stage_registered else {}

        qs.save_yaml(run/"state/assignments.yaml",fixture.state)
        qs.save_yaml(run/"state/current.yaml",fixture.current)
        assignment={"assignment_id":"ASG-CORE-REVIEW","attempt_id":"ATT-1","role_id":"P-02",
                    "agent_id":"fixture-independent-reviewer","input_snapshot":model.snapshot,
                    "artifact_type":"REVIEW","result_id":"REVIEW-R2","output_path":"reviews/REVIEW-R2.yaml",
                    "lifecycle_scope":"CORE","prep_admission_sha256":ref("admission").sha256,
                    "assigned_ids":{"requirements":["R-001"]},"goal_result_id":"VECTOR-001",
                    "dependency_result_ids":[source.id for source in (stage,advice,proof)],
                    "dependency_hashes":[source.sha256 for source in (stage,advice,proof)]}
        root=Path(fixture.tmp.name)
        assignment_path=root/"review-assignment.yaml"
        output=root/"review-packet.yaml"
        qs.save_yaml(assignment_path,assignment)

        def call(operation,*flags):
            with patch.object(qs,"emit") as emit:
                code=qs.main([operation,"--root",str(root),"--run-id",run.name,
                              "--assignment",str(assignment_path),*flags])
            self.assertEqual(emit.call_count,1)
            return code,emit.call_args.args[0]

        # Accepted result and context reads stand in for the external seed.
        # The command, advisory grant, PLAN lineage and packet writer are real.
        with (patch.object(qs,"_question_arm",return_value=authority),
              patch.object(qs,"command_journal",return_value=journal),
              patch.object(qs,"_question_current_result",side_effect=accepted_result),
              patch.object(qs,"_question_result_context",side_effect=result_packet)):
            code,receipt=call("assign")
            self.assertEqual(code,0,receipt)
            code,receipt=call("context","--output",str(output),"--expand",stage.id,"--expand",proof.id)
            self.assertEqual(code,2,receipt)
            self.assertEqual(receipt["error"],"plan_projection_current_attempt")
            self.assertFalse(output.exists())
            code,receipt=call("context","--output",str(output),
                              "--expand",stage.id,"--expand",advice.id,"--expand",proof.id)
            self.assertEqual(code,0,receipt)
        packet=qs.load_yaml(output)
        self.assertEqual(packet["plan_dependency_selection_state"],"REVISION_IN_PROGRESS")
        self.assertEqual({row["source"]["result_id"]:row["source"]["sha256"]
                          for row in packet["accepted_dependencies"]
                          if row["projection"]["view"]["kind"]=="FULL_LINKED_EXPANSION"},
                         {source.id:source.sha256 for source in (stage,advice,proof)})

    def test_reviewed_adoption_survives_selection_and_holds_on_withdrawn_basis(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        import qs
        from question_admission import Arm, prepare_question_receipt

        ledger=arm()
        adviser=Assignment(ref("adviser-assignment"),"FIRST","adviser","ADVISER")
        recipient=Assignment(ref("recipient-assignment"),"FIRST","recipient","OP-SYNTHESIS")
        basis=ref("E-BASIS")
        context=AdviceContext("CORE","BANK_TO_PLAN","FAST",ledger.procedure.original,
                              ref("vector"),ref("old-graph"),ref("exposure"),"R-A","R-A",
                              (basis,),(),ref("packet"))
        contribution=Contribution(adviser,context.packet,ref("E-ADVICE"),"OBJECTION",True,"ROUTING_ONLY")
        disposition=Disposition(contribution.result,recipient,"QUALIFIED","Retain only with a check",ref("REV-OLD"))
        events=[(Arm(ledger.procedure,"FAST",2),None),(AdviceAssignment(adviser,context,recipient.role),context),
                (contribution,context),(disposition,None)]
        journal=[{"receipt":prepare_question_receipt((),{},events)}]
        review=ref("REV-NEW")
        stage=ref("STAGE-A-NEW")
        claims=[{"advice_result_id":contribution.result.id,"advice_sha256":contribution.result.sha256,
                 "disposition_result_id":disposition.proof.id,"disposition_sha256":disposition.proof.sha256,
                 "context_sha256":context.packet.sha256,"scope":"R-A"}]
        docs={
            stage.id:{"r_refs":["R-A"],"advice_consumption":claims},
            "STAGE-A-OLD":{"r_refs":["R-A"]},"STAGE-B-NEW":{"r_refs":["R-B"]},
            review.id:{"target_result_id":stage.id,"target_hash":stage.sha256,
                       "related_ids":[contribution.result.id,disposition.proof.id]},
        }
        refs={item.id:item for item in (contribution.result,disposition.proof,basis,stage,review)}
        registry={review.id:{"artifact_type":"REVIEW","role_id":"P-02"}}
        registered={"assignment":{"dependency_result_ids":[contribution.result.id,disposition.proof.id],
                                  "dependency_hashes":[contribution.result.sha256,disposition.proof.sha256]}}
        review_assignment={"assignment":{"artifact_type":"REVIEW","role_id":"P-02","agent_id":"reviewer",
                                         "lifecycle_scope":"CORE","assigned_ids":{"requirements":["R-A"]}}}
        review_packet={"accepted_dependencies":[
            {"source":{"result_id":item.id,"sha256":item.sha256},
             "projection":{"view":{"kind":"FULL_LINKED_EXPANSION"}}}
            for item in (contribution.result,disposition.proof)
        ]}
        previous={"current_plan_projection":{"stages":[{"logical_stage_id":"A","result_id":"STAGE-A-OLD"}]}}
        proposal={"stages":[{"logical_stage_id":"A","result_id":stage.id}]}
        after={"current_plan_projection":proposal}
        state={"results":registry,"registered":{}}
        withdrawn=set()
        run=Path(".")

        def accepted_result(_run,_view,result_id,_kind,**_options):
            return refs[result_id],docs.get(result_id,{}),review_assignment if result_id==review.id else registered

        with ExitStack() as stack:
            for name,options in {
                "_question_arm":{"return_value":events[0][0]},
                "command_journal":{"return_value":journal},
                "_question_current_result":{"side_effect":accepted_result},
                "_question_result_context":{"return_value":review_packet},
                "_admitted_document":{"side_effect":lambda _run,_state,result_id,_kind: ({},docs[result_id])},
                "intake_affected_results":{"side_effect":lambda *_args: (set(),withdrawn)},
                "_adviser_disposition":{"side_effect":qs.QSError("adviser_disposition_context_stale")},
                "_question_review":{"return_value":(stage,docs[stage.id],registered)},
            }.items():
                stack.enter_context(patch.object(qs,name,**options))
            qs._require_plan_advice_resolution(run,state,previous,after,{},proposal)
            self.assertEqual(qs._adviser_freeze_blockers(run,(state,after,{},journal),"CORE"),())

            claims[0]["scope"]="R-B"
            with self.assertRaisesRegex(qs.QSError,"plan_selection_adviser_hold"):
                qs._require_plan_advice_resolution(run,state,previous,after,{},proposal)
            claims[0]["scope"]="R-A"
            registry.clear()
            with self.assertRaisesRegex(qs.QSError,"plan_selection_adviser_hold"):
                qs._require_plan_advice_resolution(run,state,previous,after,{},proposal)
            registry[review.id]={"artifact_type":"REVIEW","role_id":"P-02"}

            withdrawn.add(basis.id)
            self.assertEqual(qs._adviser_freeze_blockers(run,(state,after,{},journal),"CORE",{"R-A"}),
                             ("adviser_disposition_required:E-ADVICE",))
            disjoint={"stages":[*proposal["stages"],{"logical_stage_id":"B","result_id":"STAGE-B-NEW"}]}
            qs._require_plan_advice_resolution(run,state,after,{"current_plan_projection":disjoint},{},disjoint)


class QuestionCoverageConsumerTest(unittest.TestCase):
    def test_intake_retains_only_exact_open_gap_provenance(self):
        import tempfile
        from model_validation import validate_intake_candidate

        proof={"proof_id":"REVIEW","proof_sha256":"a"*64,"reason":"capacity exhausted"}
        record={"record_kind":"SOURCE_OUTCOME","original_q_ids":["Q-1"],"source_disposition":"KEPT",
                "answer_status":"OPEN","knowledge_state":"UNKNOWN","question_gap":proof}
        bank={"questions":[{"id":"Q-1"}]}
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)
            def evaluate(row,verified):
                return validate_intake_candidate(run,bank,{},{"id":"OUT","records":[row]},
                                                 "SOURCE_OUTCOMES",lambda _: {},verified)
            self.assertEqual(evaluate(record,{"Q-1":proof}),[])
            for row,verified in (
                (record,None),
                (record,{"Q-1":{**proof,"proof_sha256":"b"*64}}),
                ({**record,"answer_status":"ANSWERED"},{"Q-1":proof}),
                ({**record,"answer_result_id":"FAKE"},{"Q-1":proof}),
            ):
                self.assertIn("intake:SOURCE_OUTCOMES:OUT:record:0:question_gap_not_verified",evaluate(row,verified))

    def test_two_primary_owners_and_ten_distinct_perspectives_share_work(self):
        from orchestration import ROLES, validate_orchestration

        proof={"proof_id":"REVIEW","proof_sha256":"a"*64,"reason":"capacity exhausted"}
        bank={"requirements":["R-1"],"questions":[{"id":qid} for qid in ("A","B","C")]}
        assignments={}
        owners={"A":"P-01","B":"P-02"}
        for number in range(1,11):
            role=f"P-{number:02d}"
            qid="A" if number%2 else "B"
            assignments[role]={"assignment":{"assignment_id":role,"attempt_id":"TRY-1",
                "role_id":role,"agent_id":"fixture-"+role,"input_snapshot":"SNAP","artifact_type":"PRIMARY",
                "selection_authority":"ROOT","result_id":"RES-"+role,"assigned_ids":{"questions":[qid]},
                "question_work":{"lead":[qid] if role in owners.values() else [],
                                 "support":[] if role in owners.values() else [qid]}},"status":"ASSIGNED"}
        approved={"input_snapshot":"SNAP","allowed_roles":sorted(ROLES),
                  "role_competence":{role:["analysis"] for role in ROLES},
                  "question_routes":{"A":{"primary_assignment_id":"P-01","required_tags":["analysis"]},
                                     "B":{"primary_assignment_id":"P-02","required_tags":["analysis"]},
                                     "C":{"question_gap":proof}}}
        def route_errors():
            check=validate_orchestration(bank,approved,assignments,{}, {},[],
                                         question_gap_proofs={"C":proof},question_primary_owners=owners)
            return [error for error in check["errors"] if "question:" in error or "question_routes:" in error]
        self.assertEqual(route_errors(),[])
        assignment=assignments["P-03"]["assignment"]
        assignment["question_work"]={"lead":["A"],"support":[]}
        self.assertIn("question:A:primary_assignment_count:2",route_errors())
        assignment["assigned_ids"]["questions"]=["C"]
        assignment["question_work"]={"lead":[],"support":["C"]}
        self.assertIn("question:C:uninvestigated_route_binding",route_errors())


if __name__ == "__main__":
    unittest.main()
