import unittest
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from question_admission import Arm, prepare_question_receipt, replay_question_receipts
from question_ledger import (
    Admission, AdviceAssignment, AdviceContext, Assignment, Contribution,
    Disposition, LedgerError, Operation, Procedure, Question, Ref, Transition,
)


def ref(name):
    return Ref(name, "b" * 64)


def arm(mode):
    return Arm(Procedure("GOAL_TO_PLAN", *(ref(name) for name in (
        "original", "inventory", "cutoff", "tools", "rules", "depth", "roles", "review",
    ))), mode, 2)


def append(receipts, *rows):
    receipt = prepare_question_receipt(receipts, {"status": "COMMITTED"}, rows)
    receipts.append(receipt)


class JournalProjectionTest(unittest.TestCase):
    def test_capacity_rebuilds_from_prior_commits_including_unknown(self):
        for mode, expected in (("FAST", 2), ("DETAILED", 4)):
            with self.subTest(mode=mode):
                receipts = []
                append(receipts, (arm(mode), None))
                for index in range(5):
                    question = Question(str(index), str(index), "scope", "route",
                                        ref("inventory"), (0, 0, 0))
                    append(receipts, (question, None))
                assignment = Assignment(ref("assignment"), "attempt", "/root/worker", "role")
                for index in range(expected):
                    op = Operation(str(index), str(index), assignment)
                    append(receipts, (Admission(str(index), op), None),
                           (Transition(op, "RESERVED", ref("reservation")), None),
                           (Transition(op, "UNKNOWN_START", ref("timeout")), None))
                op = Operation("extra", "extra", assignment)
                with self.assertRaisesRegex(LedgerError, "question_capacity"):
                    append(receipts, (Admission(str(expected), op), None))
                summary = replay_question_receipts(receipts).summary()
                self.assertEqual(summary["started"], [])
                self.assertEqual(summary["count_blocker"], "UNKNOWN_START")

    def test_stale_advice_receipt_and_affected_freeze(self):
        receipts = []
        bound = arm("FAST")
        append(receipts, (bound, None))
        ctx = AdviceContext("PREP", "GOAL_TO_PLAN", "FAST", bound.procedure.original,
                            ref("framing"), ref("graph"), ref("exposure"), "area",
                            "question", (), (), ref("packet"))
        actor = Assignment(ref("adviser"), "attempt", "/root/adviser", "adviser")
        assignment = AdviceAssignment(actor, ctx, "recipient")
        with self.assertRaisesRegex(LedgerError, "advice_context_stale"):
            append(receipts, (assignment, replace(ctx, graph=ref("new-graph"))))
        append(receipts, (assignment, ctx))
        result = ref("contribution")
        contribution = Contribution(actor, ctx.packet, result, "OBJECTION", True, "ROUTING_ONLY")
        append(receipts, (contribution, ctx))
        check = lambda *_: True
        view = replay_question_receipts(receipts)
        self.assertEqual(view.freeze_blockers({"unrelated"}, check), ())
        self.assertEqual(view.freeze_blockers({"area"}, check), (result.id,))
        recipient = Assignment(ref("recipient"), "attempt", "/root/recipient", "recipient")
        append(receipts, (Disposition(result, recipient, "QUALIFIED", "scoped", ref("review")), None))
        view = replay_question_receipts(receipts)
        self.assertEqual(view.freeze_blockers({"area"}, check), ())
        self.assertEqual(view.freeze_blockers({"area"}, lambda *_: False), (result.id,))

    def test_replay_rejects_boolean_capacity(self):
        receipts = []
        append(receipts, (arm("FAST"), None))
        receipts[0]["question_events"][0]["row"]["half_budget"] = True
        with self.assertRaisesRegex(LedgerError, "question_integer_required"):
            replay_question_receipts(receipts)
