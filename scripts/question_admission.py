"""Project the question ledger from already authenticated command receipts."""

from dataclasses import dataclass, fields, is_dataclass
import re
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from question_ledger import (
    Admission, Advice, AdviceAssignment, AdviceContext, Alias, Contribution,
    Disposition, Gap, HiddenWork, Ledger, LedgerError, Procedure, Question,
    Transition, require,
)


@dataclass(frozen=True)
class Arm:
    procedure: Procedure
    effort_mode: str
    half_budget: int


EVENTS = {
    "arm": Arm,
    "question": Question,
    "alias": Alias,
    "admission": Admission,
    "transition": Transition,
    "gap": Gap,
    "hidden-work": HiddenWork,
    "adviser-assignment": AdviceAssignment,
    "adviser-contribution": Contribution,
    "adviser-disposition": Disposition,
}
KINDS = {row_type: kind for kind, row_type in EVENTS.items()}
FIELD = "question_events"


def _decode(annotation, raw):
    origin = get_origin(annotation)
    if origin is UnionType:
        member, null = get_args(annotation)
        require(null is type(None), "unsupported_question_field_type")
        return None if raw == "" else _decode(member, raw)
    if origin is tuple:
        require(type(raw) is list, "question_array_required")
        members = get_args(annotation)
        if members[-1] is Ellipsis:
            return tuple(_decode(members[0], item) for item in raw)
        require(len(raw) == len(members), "question_array_length")
        return tuple(_decode(member, item) for member, item in zip(members, raw))
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        require(type(raw) is dict and raw.keys() == hints.keys(), "question_record_fields")
        return annotation(**{key: _decode(member, raw[key]) for key, member in hints.items()})
    if annotation is bool:
        require(raw in ("YES", "NO"), "question_boolean_required")
        return raw == "YES"
    if annotation is int:
        require(type(raw) is str and re.fullmatch(r"0|-?[1-9][0-9]{0,11}", raw),
                "question_integer_required")
        return int(raw)
    require(type(raw) is annotation, "question_field_type")
    if annotation is str:
        require(bool(raw.strip()), "question_field_blank")
    return raw


def _wire(value):
    if is_dataclass(value):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if value is None:
        return ""
    if type(value) is bool:
        return "YES" if value else "NO"
    if type(value) is int:
        return str(value)
    return value


@dataclass
class QuestionProjection:
    ledger: Ledger | None = None
    advice: Advice | None = None

    def apply(self, row, *, current_context=None):
        if isinstance(row, Arm):
            require(self.ledger is None, "question_arm_already_bound")
            self.ledger = Ledger(row.procedure, row.effort_mode, row.half_budget)
            self.advice = Advice(self.ledger)
            return
        require(self.ledger is not None, "question_arm_missing")
        if isinstance(row, AdviceAssignment):
            self.advice.assign(row, current_context)
        elif isinstance(row, Contribution):
            self.advice.contribute(row, current_context)
        elif isinstance(row, Disposition):
            self.advice.dispose(row)
        else:
            self.ledger.apply(row)

    def summary(self):
        require(self.ledger is not None, "question_arm_missing")
        ledger = self.ledger
        return {
            "input_mode": ledger.procedure.input_mode,
            "effort_mode": ledger.mode,
            "capacity": ledger.capacity,
            "started": sorted(ledger.started),
            "closed": {key: _wire(row) for key, row in sorted(ledger.closed.items())},
            "attempts": {key: _wire(row) for key, row in sorted(ledger.attempts.items())},
            "aliases": {key: _wire(row) for key, row in sorted(ledger.aliases.items())},
            "gaps": {key: _wire(row) for key, row in sorted(ledger.gaps.items())},
            "hidden_work": [_wire(row) for row in ledger.hidden],
            "count_blocker": ledger.count_blocker(),
        }

    def freeze_blockers(self, scopes, is_current):
        """Require current authority for each material affected disposition."""
        if self.advice is None:
            return ()
        blockers = set(self.advice.freeze_blockers(scopes))
        for key, contribution in self.advice.contributions.items():
            assignment = self.advice.assignments[contribution.assignment]
            if (contribution.material and assignment.context.scope in scopes
                    and key not in blockers
                    and not is_current(assignment, contribution, self.advice.dispositions[key])):
                blockers.add(key)
        return tuple(sorted(blockers))


def _apply_event(projection, event):
    require(type(event) is dict and event.keys() == {"kind", "row", "authorized_context"},
            "question_event_fields")
    kind = event["kind"]
    require(type(kind) is str and kind in EVENTS, "unknown_question_event")
    row = _decode(EVENTS[kind], event["row"])
    raw_context = event["authorized_context"]
    if kind in {"adviser-assignment", "adviser-contribution"}:
        context = _decode(AdviceContext, raw_context)
    else:
        require(raw_context == "", "unexpected_adviser_context")
        context = None
    projection.apply(row, current_context=context)


def replay_question_receipts(receipts):
    projection = QuestionProjection()
    for receipt in receipts:
        if FIELD not in receipt:
            continue
        events = receipt[FIELD]
        require(type(events) is list and events, "question_events_required")
        for event in events:
            _apply_event(projection, event)
    return projection


def prepare_question_receipt(receipts, proposed, verified_events):
    """Build one journal receipt from lock-held, separately verified typed facts.

    verified_events contains (row, authorized_context) pairs. The caller derives
    them from captured authority and provider observations under the command cut.
    """
    require(FIELD not in proposed, "question_events_already_present")
    projection = replay_question_receipts(receipts)
    events = []
    for row, context in verified_events:
        kind = KINDS.get(type(row))
        if kind is None:
            raise LedgerError("unknown_question_event")
        event = {"kind": kind, "row": _wire(row), "authorized_context": _wire(context)}
        _apply_event(projection, event)
        events.append(event)
    require(bool(events), "question_events_required")
    return {**proposed, FIELD: events}
