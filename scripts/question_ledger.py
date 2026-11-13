"""Pure projections for T03. Input rows must come from verified admission."""

from dataclasses import dataclass, field
import re


class LedgerError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise LedgerError(reason)


@dataclass(frozen=True)
class Ref:
    id: str
    sha256: str

    def __post_init__(self):
        require(bool(self.id.strip()) and re.fullmatch(r"[0-9a-f]{64}", self.sha256),
                "exact_reference_required")


@dataclass(frozen=True)
class Procedure:
    input_mode: str
    original: Ref
    routing_inventory: Ref
    cutoff: Ref
    tools: Ref
    routing_rules: Ref
    methodology: Ref
    roles_and_phases: Ref
    review_and_audit: Ref


@dataclass(frozen=True)
class Question:
    id: str
    decision: str
    scope: str
    evidence_route: str
    source: Ref
    priority: tuple[int, int, int]
    mandatory: bool = False
    parent: str | None = None

    @property
    def identity(self):
        return self.decision, self.scope, self.evidence_route


@dataclass(frozen=True)
class Assignment:
    assignment: Ref
    attempt: str
    actor: str
    role: str


@dataclass(frozen=True)
class Operation:
    id: str
    fence: str
    assignment: Assignment


@dataclass(frozen=True)
class Admission:
    question_id: str
    operation: Operation


@dataclass(frozen=True)
class Transition:
    operation: Operation
    status: str
    proof: Ref
    close_outcome: str | None = None
    remaining_gap: str | None = None


@dataclass(frozen=True)
class Alias:
    id: str
    canonical_id: str
    source: Ref
    equivalence_review: Ref


@dataclass(frozen=True)
class Gap:
    question_id: str
    reason: str
    proof: Ref


@dataclass(frozen=True)
class HiddenWork:
    scope: str
    proof: Ref


@dataclass(frozen=True)
class Attempt:
    question_id: str
    operation: Operation
    status: str


NEXT = {
    "ADMITTED": {"RESERVED", "CANCELLED_BEFORE_START"},
    "RESERVED": {"UNKNOWN_START", "CONFIRMED_SUBSTANTIVE", "CANCELLED_BEFORE_START"},
    "UNKNOWN_START": {"CONFIRMED_SUBSTANTIVE", "CANCELLED_BEFORE_START"},
    "CONFIRMED_SUBSTANTIVE": {"ABORTED_AFTER_START", "CLOSED_AFTER_WORK"},
}
ACTIVE = {"ADMITTED", "RESERVED", "UNKNOWN_START", "CONFIRMED_SUBSTANTIVE"}


@dataclass
class Ledger:
    procedure: Procedure
    mode: str
    half_budget: int
    questions: dict = field(default_factory=dict, init=False)
    aliases: dict = field(default_factory=dict, init=False)
    attempts: dict = field(default_factory=dict, init=False)
    active: dict = field(default_factory=dict, init=False)
    started: set = field(default_factory=set, init=False)
    closed: dict = field(default_factory=dict, init=False)
    gaps: dict = field(default_factory=dict, init=False)
    hidden: list = field(default_factory=list, init=False)

    def __post_init__(self):
        require(self.procedure.input_mode in {"GOAL_TO_PLAN", "BANK_TO_PLAN"}, "input_mode")
        require(self.mode in {"FAST", "DETAILED"}, "effort_mode")
        require(type(self.half_budget) is int and self.half_budget > 0, "positive_half_budget")

    @property
    def capacity(self):
        return self.half_budget * (1 if self.mode == "FAST" else 2)

    def eligible(self):
        return sorted((q for q in self.questions.values()
                       if q.id not in self.closed and q.id not in self.active
                       and q.id not in self.gaps),
                      key=lambda q: (tuple(-value for value in q.priority), q.id))

    def apply(self, row):
        if isinstance(row, Question):
            self._register(row)
        elif isinstance(row, Alias):
            self._alias(row)
        elif isinstance(row, Admission):
            self._admit(row)
        elif isinstance(row, Transition):
            self._transition(row)
        elif isinstance(row, Gap):
            require(row.question_id in self.questions and row.question_id not in self.active
                    and row.question_id not in self.started and row.question_id not in self.gaps
                    and bool(row.reason.strip()),
                    "uninvestigated_gap_required")
            self.gaps[row.question_id] = row
        elif isinstance(row, HiddenWork):
            require(bool(row.scope.strip()), "hidden_work_scope")
            self.hidden.append(row)
        else:
            raise LedgerError("unknown_ledger_event")

    def _register(self, q):
        require(all(value.strip() for value in (q.id, *q.identity)), "incomplete_question_identity")
        require(len(q.priority) == 3 and all(type(value) is int for value in q.priority),
                "invalid_priority")
        require(q.id not in self.questions and q.id not in self.aliases, "repeated_question_id")
        require(all(q.identity != other.identity for other in self.questions.values()),
                "equivalent_question_needs_alias_review")
        if q.parent is not None:
            parent = self._canonical(q.parent)
            require(parent in self.started, "derived_question_needs_investigated_parent")
        self.questions[q.id] = q

    def _alias(self, row):
        require(bool(row.id.strip()) and row.id not in self.questions and row.id not in self.aliases
                and row.canonical_id in self.questions, "invalid_alias")
        self.aliases[row.id] = row

    def _canonical(self, qid):
        alias = self.aliases.get(qid)
        return alias.canonical_id if alias is not None else qid

    def _admit(self, row):
        qid = self._canonical(row.question_id)
        op = row.operation
        require(qid in self.questions and qid not in self.active
                and qid not in self.closed and qid not in self.gaps, "question_not_eligible")
        require(bool(op.id.strip()) and op.id not in self.attempts and bool(op.fence.strip()), "operation_identity_or_fence")
        if qid not in self.started:
            require(len(self.started | self.active.keys()) < self.capacity, "question_capacity")
            candidates = [q for q in self.eligible() if q.id not in self.started]
            require(candidates[0].id == qid, "question_priority")
        self.attempts[op.id] = Attempt(qid, op, "ADMITTED")
        self.active[qid] = op.id

    def _transition(self, row):
        attempt = self.attempts.get(row.operation.id)
        require(attempt is not None and attempt.operation == row.operation,
                "operation_assignment_or_fence_mismatch")
        require(row.status in NEXT.get(attempt.status, set()), "invalid_question_transition")
        qid = attempt.question_id
        if row.status == "CLOSED_AFTER_WORK":
            require(row.close_outcome in {"RESOLVED", "OPEN"}, "close_outcome_required")
            require(bool(row.remaining_gap and row.remaining_gap.strip()) == (row.close_outcome == "OPEN"),
                    "open_close_requires_actionable_gap")
        else:
            require(row.close_outcome is None and row.remaining_gap is None, "unexpected_close_fields")
        if row.status == "CONFIRMED_SUBSTANTIVE":
            self.started.add(qid)
        if row.status == "CLOSED_AFTER_WORK":
            self.closed[qid] = row
        if row.status not in ACTIVE:
            del self.active[qid]
        self.attempts[row.operation.id] = Attempt(qid, attempt.operation, row.status)

    def count_blocker(self):
        if self.hidden:
            return "HIDDEN_WORK"
        if any(row.status == "UNKNOWN_START" for row in self.attempts.values()):
            return "UNKNOWN_START"
        if self.active or self.started != self.closed.keys():
            return "INCOMPLETE"
        eligible=self.questions.keys()-self.exclusions().keys()
        mandatory={qid for qid in eligible if self.questions[qid].mandatory}
        if len(mandatory)>self.half_budget*2 or len(mandatory)%2 and mandatory==eligible:
            return "UNPAIRED_MANDATORY_SCOPE"
        if len(self.started) != self.capacity:
            return "UNSATISFIED_COUNT"
        if set(self.questions) != self.closed.keys() | self.gaps.keys():
            return "UNDISPOSED_QUESTION"
        return None

    def exclusions(self):
        return {qid:gap.reason for qid,gap in self.gaps.items() if gap.reason.startswith("EXCLUDED: ")}


def compare(fast, detailed):
    require((fast.mode, detailed.mode) == ("FAST", "DETAILED"), "wrong_pair_order")
    if fast.procedure != detailed.procedure or fast.half_budget != detailed.half_budget:
        return "UNPAIRED_PROCEDURE"
    originals = lambda ledger: {q.id: q for q in ledger.questions.values() if q.parent is None}
    if originals(fast) != originals(detailed):
        return "UNPAIRED_INVENTORY"
    aliases=lambda ledger: {key:(row.canonical_id,row.source) for key,row in ledger.aliases.items()}
    if aliases(fast)!=aliases(detailed) or fast.exclusions()!=detailed.exclusions():
        return "UNPAIRED_INVENTORY"
    eligible=detailed.questions.keys()-detailed.exclusions().keys()
    mandatory={qid for qid in eligible if detailed.questions[qid].mandatory}
    if len(mandatory) > detailed.capacity or (len(mandatory) % 2 == 1
                                              and mandatory == eligible):
        return "UNPAIRED_MANDATORY_SCOPE"
    for ledger in (fast, detailed):
        blocker = ledger.count_blocker()
        if blocker:
            return blocker
    if mandatory - detailed.closed.keys():
        return "UNPAIRED_MANDATORY_SCOPE"
    return "COUNT_PASS"


@dataclass(frozen=True)
class AdviceContext:
    lifecycle: str
    input_mode: str
    effort_mode: str
    original: Ref
    framing: Ref
    graph: Ref
    exposure: Ref
    scope: str
    question: str
    evidence: tuple[Ref, ...]
    decisions: tuple[Ref, ...]
    packet: Ref


@dataclass(frozen=True)
class AdviceAssignment:
    assignment: Assignment
    context: AdviceContext
    disposition_role: str


@dataclass(frozen=True)
class Contribution:
    assignment: Assignment
    context_packet: Ref
    result: Ref
    kind: str
    material: bool
    work_kind: str
    operation: Operation | None = None


@dataclass(frozen=True)
class Disposition:
    result: Ref
    authority: Assignment
    status: str
    rationale: str
    proof: Ref


@dataclass
class Advice:
    ledger: Ledger
    assignments: dict = field(default_factory=dict, init=False)
    contributions: dict = field(default_factory=dict, init=False)
    dispositions: dict = field(default_factory=dict, init=False)

    def assign(self, row, current):
        self._current(row, current)
        require(row.context.lifecycle in {"PREP", "CORE"}
                and row.context.original == self.ledger.procedure.original
                and row.context.input_mode == self.ledger.procedure.input_mode
                and row.context.effort_mode == self.ledger.mode
                and bool(row.context.scope.strip()) and bool(row.context.question.strip())
                and bool(row.disposition_role.strip()), "advice_context_scope")
        require(row.assignment not in self.assignments, "advice_assignment_exists")
        self.assignments[row.assignment] = row

    def contribute(self, row, current):
        assigned = self.assignments.get(row.assignment)
        self._current(assigned, current)
        require(row.context_packet == assigned.context.packet
                and row.kind in {"GAP", "ALTERNATIVE", "OBJECTION", "QUESTION"}
                and type(row.material) is bool and row.result.id not in self.contributions,
                "advice_provenance")
        require((row.work_kind == "SUBSTANTIVE" and row.operation is not None)
                or (row.work_kind == "ROUTING_ONLY" and row.operation is None),
                "advice_work_classification")
        if row.operation is not None:
            attempt = self.ledger.attempts.get(row.operation.id)
            require(attempt is not None and attempt.operation == row.operation
                    and attempt.status in {"CONFIRMED_SUBSTANTIVE", "CLOSED_AFTER_WORK"}
                    and (self.ledger.questions[attempt.question_id].scope == assigned.context.scope
                         or attempt.question_id == assigned.context.question),
                    "advice_substantive_work_unaccounted")
        self.contributions[row.result.id] = row

    def dispose(self, row):
        contribution = self.contributions.get(row.result.id)
        require(contribution is not None and contribution.result == row.result,
                "advice_disposition_result")
        assigned = self.assignments[contribution.assignment]
        require(row.authority.role == assigned.disposition_role
                and row.authority.actor != assigned.assignment.actor
                and row.status in {"ACCEPTED", "REJECTED", "QUALIFIED", "PENDING"}
                and bool(row.rationale.strip()), "advice_disposition_authority")
        self.dispositions[row.result.id] = row

    def freeze_blockers(self, affected_scopes):
        return tuple(sorted(result_id for result_id, row in self.contributions.items()
                            if row.material
                            and self.assignments[row.assignment].context.scope in affected_scopes
                            and (result_id not in self.dispositions
                                 or self.dispositions[result_id].status == "PENDING")))

    @staticmethod
    def _current(assigned, current):
        require(assigned is not None and assigned.context == current, "advice_context_stale")
