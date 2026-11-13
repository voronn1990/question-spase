#!/usr/bin/env python3
"""Pure deterministic routing, dependency, and evidence-reuse validation.

The caller owns persistence, semantic approval, leases, and dispatch.  This
module consumes already parsed dictionaries and returns proposals and
addressable structural errors; it performs no I/O and launches nothing.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable


ROLES = frozenset(
    [*(f"P-{number:02d}" for number in range(1, 11))]
    + [
        "OP-VECTOR",
        "OP-PROJECTION",
        "OP-DIVERGENCE",
        "OP-REVERSE",
        "OP-INDULGENCE",
        "OP-SYNTHESIS",
        "PLAN-AUTHOR",
        "FINAL-AUDITOR",
    ]
)
GROUPS = frozenset({"1", "2", "3"})
EVIDENCE_KEY_FIELDS = (
    "evidence_id",
    "content_hash",
    "input_snapshot",
    "tool_id",
    "tool_version",
    "policy_hash",
    "exposure_hash",
    "freshness",
)
INVALID_FRESHNESS = frozenset({"", "UNKNOWN", "EXPIRED"})
TERMINAL_ATTEMPT_STATES = frozenset({"ACCEPTED", "REJECTED", "BLOCKED", "STALE", "SUPERSEDED"})
ADVISER_ROLE = "ADVISER"


def ordinary_adviser_assignment(assignment: dict[str, Any]) -> bool:
    adviser = assignment.get("adviser")
    return (
        assignment.get("role_id") == ADVISER_ROLE
        and assignment.get("artifact_type") == "EVIDENCE"
        and assignment.get("selection_authority") == "ROOT"
        and assignment.get("lifecycle_scope") in {"PREP", "CORE"}
        and isinstance(assignment.get("logical_occurrence_id"), str)
        and bool(assignment["logical_occurrence_id"])
        and isinstance(adviser, dict)
        and set(adviser) == {"address", "recipient_assignment_id", "work_kind"}
        and all(isinstance(value, str) and value.strip() for value in adviser.values())
        and adviser["work_kind"] in {"ROUTING_ONLY", "SUBSTANTIVE"}
        and not assignment.get("allowed_reads")
        and not assignment.get("role_instruction_paths")
    )


def _empty(routing_status: str) -> dict[str, Any]:
    return {
        "routing_status": routing_status,
        "errors": [],
        "proposals": [],
        "task_states": [],
        "ready_groups": [],
        "stale_ids": [],
        "blockers": [],
        "reuse": [],
    }


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    return list(value)


def _mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return value


def _prefix_conflict(left: str, right: str) -> bool:
    left_parts = tuple(part for part in left.replace("\\", "/").split("/") if part not in {"", "."})
    right_parts = tuple(part for part in right.replace("\\", "/").split("/") if part not in {"", "."})
    if not left_parts or not right_parts:
        return left == right
    limit = min(len(left_parts), len(right_parts))
    return left_parts[:limit] == right_parts[:limit]


def dispatch_resource_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_paths = _strings(left.get("sole_writer_paths", [])) or []
    right_paths = _strings(right.get("sole_writer_paths", [])) or []
    if any(_prefix_conflict(a, b) for a in left_paths for b in right_paths):
        return True
    left_resources = set(_strings(left.get("resource_keys", [])) or [])
    right_resources = set(_strings(right.get("resource_keys", [])) or [])
    return bool(left_resources & right_resources)


def _closure(graph: dict[str, set[str]], starts: Iterable[str]) -> set[str]:
    reached = set(starts)
    queue = deque(sorted(reached))
    while queue:
        source = queue.popleft()
        for target in sorted(graph.get(source, set())):
            if target not in reached:
                reached.add(target)
                queue.append(target)
    return reached


def _cycle_nodes(graph: dict[str, set[str]]) -> set[str]:
    """Return nodes in, or blocked by, a cycle using deterministic Kahn reduction."""
    nodes = set(graph)
    for targets in graph.values():
        nodes.update(targets)
    indegree = {node: 0 for node in nodes}
    for targets in graph.values():
        for target in targets:
            indegree[target] += 1
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    removed: set[str] = set()
    while queue:
        node = queue.popleft()
        removed.add(node)
        for target in sorted(graph.get(node, set())):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return nodes - removed


def _normal_assignments(
    assignments: Any,
    errors: list[str],
    verified_completed_unknown: Any = None,
) -> dict[str, dict[str, Any]]:
    supplied = _mapping(assignments)
    if supplied is None:
        errors.append("assignments:not_mapping")
        return {}
    if "registered" in supplied:
        registry = _mapping(supplied.get("registered"))
        current_attempts = _mapping(supplied.get("current_attempts", {}))
        if registry is None:
            errors.append("assignments:registered:not_mapping")
            return {}
        if current_attempts is None:
            errors.append("assignments:current_attempts:not_mapping")
            current_attempts = {}
    else:
        registry = supplied
        current_attempts = {}

    if verified_completed_unknown is None:
        completed_unknown: dict[str, Any] = {}
    else:
        parsed_completed_unknown = _mapping(verified_completed_unknown)
        completed_unknown = parsed_completed_unknown or {}
        if parsed_completed_unknown is None:
            errors.append("verified_completed_unknown:not_mapping")

    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    group_errors: dict[str, list[str]] = defaultdict(list)
    for registry_key in sorted(registry):
        entry = registry[registry_key]
        if not isinstance(entry, dict) or not isinstance(entry.get("assignment"), dict):
            errors.append(f"assignment_registry:{registry_key}:invalid_entry")
            continue
        assignment = entry["assignment"]
        assignment_id = assignment.get("assignment_id")
        attempt_id = assignment.get("attempt_id")
        if not isinstance(assignment_id, str) or not assignment_id:
            errors.append(f"assignment_registry:{registry_key}:missing_assignment_id")
            continue
        if not isinstance(attempt_id, str) or not attempt_id:
            errors.append(f"assignment_registry:{registry_key}:missing_attempt_id")
            continue
        if attempt_id in grouped[assignment_id]:
            message = f"assignment:{assignment_id}:attempt:{attempt_id}:duplicate_registry_entry"
            errors.append(message)
            group_errors[assignment_id].append(message)
            continue
        grouped[assignment_id][attempt_id] = {
            "assignment": assignment,
            "status": entry.get("status", "ASSIGNED"),
            "launch_state": entry.get("launch_state", assignment.get("launch_state", "NOT_LAUNCHED")),
        }

    normalized: dict[str, dict[str, Any]] = {}
    for assignment_id in sorted(grouped):
        attempts = grouped[assignment_id]
        selected_attempt = current_attempts.get(assignment_id)
        if len(attempts) == 1:
            only_attempt = next(iter(attempts))
            if selected_attempt is None:
                selected_attempt = only_attempt
            elif selected_attempt != only_attempt:
                errors.append(f"assignment:{assignment_id}:current_attempt:{selected_attempt}:unknown")
                continue
        elif not isinstance(selected_attempt, str) or not selected_attempt:
            errors.append(f"assignment:{assignment_id}:multiple_attempts_require_explicit_current")
            continue
        if selected_attempt not in attempts:
            errors.append(f"assignment:{assignment_id}:current_attempt:{selected_attempt}:unknown")
            continue

        history_errors = group_errors[assignment_id]
        if len(attempts) > 1:
            visited: set[str] = set()
            cursor: str | None = selected_attempt
            while cursor is not None:
                if cursor in visited:
                    history_errors.append(f"assignment:{assignment_id}:supersession_cycle:{cursor}")
                    break
                visited.add(cursor)
                predecessor = attempts[cursor]["assignment"].get("supersedes_attempt_id")
                if predecessor is None:
                    cursor = None
                elif not isinstance(predecessor, str) or not predecessor:
                    history_errors.append(f"assignment:{assignment_id}:attempt:{cursor}:invalid_supersedes_attempt_id")
                    break
                elif predecessor not in attempts:
                    history_errors.append(f"assignment:{assignment_id}:attempt:{cursor}:unknown_supersedes:{predecessor}")
                    break
                else:
                    cursor = predecessor
            missing_from_chain = sorted(set(attempts) - visited)
            if missing_from_chain:
                history_errors.append(
                    f"assignment:{assignment_id}:supersession_chain_incomplete:{','.join(missing_from_chain)}"
                )
            for attempt_id in sorted(set(attempts) - {selected_attempt}):
                historical = attempts[attempt_id]
                status = historical["status"]
                launch_state = historical["launch_state"]
                if status not in TERMINAL_ATTEMPT_STATES:
                    history_errors.append(
                        f"assignment:{assignment_id}:attempt:{attempt_id}:historical_status:{status}:not_terminal"
                    )
                pair = assignment_id + "\0" + attempt_id
                completed_result_id = completed_unknown.get(pair)
                completed_exactly = (
                    status == "ACCEPTED"
                    and isinstance(completed_result_id, str)
                    and completed_result_id == historical["assignment"].get("result_id")
                )
                if launch_state == "DISPATCH_UNKNOWN" and not completed_exactly:
                    history_errors.append(
                        f"assignment:{assignment_id}:attempt:{attempt_id}:dispatch_unknown_unresolved"
                    )
        selected = attempts[selected_attempt]
        if history_errors:
            errors.extend(history_errors)
            selected = {**selected, "history_errors": sorted(set(history_errors))}
        normalized[assignment_id] = selected

    for pair, result_id in sorted(completed_unknown.items()):
        proof_assignment_id, separator, proof_attempt_id = pair.partition("\0") if isinstance(pair, str) else ("", "", "")
        wrapped = grouped.get(proof_assignment_id, {}).get(proof_attempt_id) if separator else None
        assignment = wrapped.get("assignment") if isinstance(wrapped, dict) else None
        bound_pair = (
            assignment.get("assignment_id") + "\0" + assignment.get("attempt_id")
            if isinstance(assignment, dict)
            and isinstance(assignment.get("assignment_id"), str)
            and isinstance(assignment.get("attempt_id"), str)
            else None
        )
        if (
            not isinstance(pair, str)
            or "\0" not in pair
            or not isinstance(result_id, str)
            or not isinstance(assignment, dict)
            or wrapped.get("status") != "ACCEPTED"
            or wrapped.get("launch_state", assignment.get("launch_state", "NOT_LAUNCHED")) != "DISPATCH_UNKNOWN"
            or bound_pair != pair
            or assignment.get("result_id") != result_id
        ):
            errors.append("verified_completed_unknown:binding_mismatch")

    for assignment_id in sorted(set(current_attempts) - set(grouped)):
        errors.append(f"assignments:current_attempts:{assignment_id}:unknown_assignment")
    return normalized


def _accepted(accepted_results: Any, accepted_docs: Any, errors: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    results = _mapping(accepted_results)
    docs = _mapping(accepted_docs)
    if results is None:
        errors.append("accepted_results:not_mapping")
        results = {}
    if docs is None:
        errors.append("accepted_docs:not_mapping")
        docs = {}
    clean_results: dict[str, dict[str, Any]] = {}
    clean_docs: dict[str, dict[str, Any]] = {}
    for result_id in sorted(results):
        entry = results[result_id]
        if not isinstance(entry, dict):
            errors.append(f"accepted_result:{result_id}:invalid_entry")
        else:
            clean_results[result_id] = entry
    for result_id in sorted(docs):
        doc = docs[result_id]
        if result_id not in clean_results:
            errors.append(f"accepted_doc:{result_id}:not_in_registry")
        if not isinstance(doc, dict):
            errors.append(f"accepted_doc:{result_id}:invalid_document")
        else:
            clean_docs[result_id] = doc
    return clean_results, clean_docs


def _approved_contract(
    bank: Any,
    approved_map: dict[str, Any],
    normalized: dict[str, dict[str, Any]],
    snapshot: str,
    errors: list[str],
    assignment_errors: dict[str, set[str]],
    derived_question_ids: set[str],
    selected_question_claims: Any,
    accepted_results: dict[str, dict[str, Any]],
    question_gap_proofs: dict[str, dict[str, str]],
    question_primary_owners: dict[str, str] | None,
    question_retired_claims: set[tuple[str, str, str]],
) -> None:
    allowed = _strings(approved_map.get("allowed_roles"))
    if allowed is None or set(allowed) != set(ROLES) or len(allowed) != len(ROLES):
        errors.append("approved_map:allowed_roles:must_equal_exact_18_roles")
        allowed_set = set()
    else:
        allowed_set = set(allowed)

    competence = _mapping(approved_map.get("role_competence"))
    if competence is None:
        errors.append("approved_map:role_competence:not_mapping")
        competence = {}
    competence_sets: dict[str, set[str]] = {}
    for role_id in sorted(competence):
        tags = _strings(competence[role_id])
        if role_id not in ROLES:
            errors.append(f"approved_map:role_competence:{role_id}:unknown_role")
        if tags is None:
            errors.append(f"approved_map:role_competence:{role_id}:invalid_tags")
        else:
            competence_sets[role_id] = set(tags)

    primary_claims: dict[str, list[str]] = defaultdict(list)
    mandatory_agents = {wrapped["assignment"].get("agent_id") for wrapped in normalized.values()
                        if wrapped["assignment"].get("role_id") in ROLES}
    for assignment_id, wrapped in normalized.items():
        assignment = wrapped["assignment"]
        role_id = assignment.get("role_id")
        auxiliary = (question_primary_owners is not None and ordinary_adviser_assignment(assignment)
                     and assignment.get("agent_id") not in mandatory_agents)
        if role_id not in allowed_set and not auxiliary:
            message = f"assignment:{assignment_id}:role:{role_id}:not_approved_exact_role"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
        if assignment.get("input_snapshot") != snapshot:
            message = f"assignment:{assignment_id}:input_snapshot:mismatch"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
        if assignment.get("selection_authority") != "ROOT":
            message = f"assignment:{assignment_id}:selection_authority:not_ROOT"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
        assigned_ids = assignment.get("assigned_ids", {})
        if not isinstance(assigned_ids, dict):
            message = f"assignment:{assignment_id}:assigned_ids:not_mapping"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
            assigned_ids = {}
        questions = _strings(assigned_ids.get("questions", []))
        if questions is None:
            message = f"assignment:{assignment_id}:assigned_ids:questions:invalid"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
            questions = []
        if assignment.get("artifact_type") in {"PRIMARY", "ANSWER"}:
            for question_id in questions:
                if (question_id,assignment_id,assignment.get("attempt_id")) in question_retired_claims:
                    continue
                if question_primary_owners is not None and question_primary_owners.get(question_id)!=assignment_id:
                    work=assignment.get("question_work")
                    if (question_id in question_primary_owners and isinstance(work,dict)
                        and isinstance(work.get("support"),list) and question_id in work["support"]):
                        continue
                primary_claims[question_id].append(assignment_id)
        group_id = str(assignment.get("group_id", "1"))
        if group_id not in GROUPS:
            message = f"assignment:{assignment_id}:group_id:{group_id}:outside_1_2_3"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
        for field in ("sole_writer_paths", "resource_keys", "dependency_result_ids"):
            if field in assignment and _strings(assignment[field]) is None:
                message = f"assignment:{assignment_id}:{field}:invalid_string_list"
                errors.append(message)
                assignment_errors[assignment_id].add(message)

    bank_map = _mapping(bank)
    if bank_map is None:
        errors.append("bank:not_mapping")
        question_ids: list[str] = []
    else:
        raw_questions = bank_map.get("questions")
        if not isinstance(raw_questions, list):
            errors.append("bank:questions:not_list")
            question_ids = []
        else:
            question_ids = []
            for index, question in enumerate(raw_questions):
                question_id = question.get("id") if isinstance(question, dict) else None
                if not isinstance(question_id, str) or not question_id:
                    errors.append(f"bank:questions:{index}:missing_id")
                else:
                    question_ids.append(question_id)
            if len(question_ids) != len(set(question_ids)):
                errors.append("bank:questions:duplicate_id")
        requirements = bank_map.get("requirements")
        if _strings(requirements) is None:
            errors.append("bank:requirements:invalid_string_list")

    selected_claims = _mapping(selected_question_claims)
    if selected_claims is None:
        errors.append("selected_question_claims:not_mapping")
        selected_claims = {}
    routes = _mapping(approved_map.get("question_routes"))
    if routes is None:
        errors.append("approved_map:question_routes:not_mapping")
        routes = {}
    routable_question_ids=set(question_ids)|derived_question_ids
    retry_questions={question for question,_,_ in question_retired_claims}
    for question_id in sorted(routable_question_ids | set(routes) | set(primary_claims) | set(selected_claims)):
        claims = sorted(primary_claims.get(question_id, []))
        selected_claim = selected_claims.get(question_id)
        if question_id in question_gap_proofs:
            if (question_id not in routable_question_ids or claims or selected_claim is not None
                or routes.get(question_id)!={"question_gap":question_gap_proofs[question_id]}):
                errors.append(f"question:{question_id}:uninvestigated_route_binding")
            continue
        if selected_claim is not None:
            if not isinstance(selected_claim, dict) or set(selected_claim) != {"assignment_id", "attempt_id", "result_id"}:
                errors.append(f"selected_question_claim:{question_id}:invalid_contract")
                claims = []
            else:
                selected_assignment_id = selected_claim.get("assignment_id")
                selected_attempt_id = selected_claim.get("attempt_id")
                selected_result_id = selected_claim.get("result_id")
                wrapped = normalized.get(selected_assignment_id) if isinstance(selected_assignment_id, str) else None
                assignment = wrapped.get("assignment") if isinstance(wrapped, dict) else None
                result_entry = accepted_results.get(selected_result_id) if isinstance(selected_result_id, str) else None
                assigned_ids = assignment.get("assigned_ids") if isinstance(assignment, dict) else None
                selected_questions = _strings(assigned_ids.get("questions", [])) if isinstance(assigned_ids, dict) else None
                if (
                    not isinstance(assignment, dict)
                    or assignment.get("attempt_id") != selected_attempt_id
                    or assignment.get("result_id") != selected_result_id
                    or assignment.get("artifact_type") not in {"PRIMARY", "ANSWER"}
                    or selected_questions is None
                    or question_id not in selected_questions
                    or not isinstance(result_entry, dict)
                    or result_entry.get("assignment_id") != selected_assignment_id
                    or result_entry.get("attempt_id") != selected_attempt_id
                    or result_entry.get("artifact_type") != assignment.get("artifact_type")
                    or result_entry.get("input_snapshot") != snapshot
                ):
                    errors.append(f"selected_question_claim:{question_id}:binding_mismatch")
                    claims = []
                else:
                    claims = [selected_assignment_id]
        if question_primary_owners is not None:
            owner=question_primary_owners.get(question_id)
            work=normalized[claims[0]]["assignment"].get("question_work") if len(claims)==1 else None
            registered_retry=(owner is None and question_id in retry_questions and len(claims)==1
                              and isinstance(work,dict) and question_id in (_strings(work.get("lead")) or []))
            if claims!=[owner] and not registered_retry:
                errors.append(f"question:{question_id}:ledger_primary_owner_binding")
        if len(claims) != 1:
            errors.append(f"question:{question_id}:primary_assignment_count:{len(claims)}")
            for assignment_id in claims:
                assignment_errors[assignment_id].add(f"question:{question_id}:duplicate_or_missing_primary")
        route = routes.get(question_id)
        if question_id not in routable_question_ids:
            errors.append(f"approved_map:question_routes:{question_id}:not_in_bank")
        if not isinstance(route, dict):
            errors.append(f"approved_map:question_routes:{question_id}:missing_or_invalid")
            continue
        primary_assignment_id = route.get("primary_assignment_id")
        required_tags = _strings(route.get("required_tags"))
        if not isinstance(primary_assignment_id, str) or not primary_assignment_id:
            errors.append(f"approved_map:question_routes:{question_id}:missing_primary_assignment_id")
            continue
        if claims != [primary_assignment_id]:
            errors.append(f"approved_map:question_routes:{question_id}:primary_binding_mismatch")
            assignment_errors[primary_assignment_id].add(f"question:{question_id}:route_binding_mismatch")
        wrapped = normalized.get(primary_assignment_id)
        if wrapped is None:
            errors.append(f"approved_map:question_routes:{question_id}:unknown_assignment:{primary_assignment_id}")
            continue
        role_id = wrapped["assignment"].get("role_id")
        if required_tags is None or not required_tags:
            errors.append(f"approved_map:question_routes:{question_id}:missing_required_tags")
            assignment_errors[primary_assignment_id].add(f"question:{question_id}:missing_required_tags")
        else:
            missing = sorted(set(required_tags) - competence_sets.get(str(role_id), set()))
            if missing:
                message = f"question:{question_id}:role:{role_id}:missing_competence:{','.join(missing)}"
                errors.append(message)
                assignment_errors[primary_assignment_id].add(message)

    reviews = _mapping(approved_map.get("critical_reviews", {}))
    if reviews is None:
        errors.append("approved_map:critical_reviews:not_mapping")
        reviews = {}
    for decision_id in sorted(reviews):
        contract = reviews[decision_id]
        if not isinstance(contract, dict):
            errors.append(f"critical_review:{decision_id}:invalid_contract")
            continue
        author_role = contract.get("author_role")
        if author_role not in ROLES:
            errors.append(f"critical_review:{decision_id}:invalid_author_role")
        seen_roles: list[str] = []
        seen_checks: list[str] = []
        for kind in ("subject", "system"):
            review = contract.get(kind)
            if not isinstance(review, dict):
                errors.append(f"critical_review:{decision_id}:{kind}:missing")
                continue
            assignment_id = review.get("assignment_id")
            role_id = review.get("role_id")
            check_id = review.get("check_id")
            required_tags = _strings(review.get("required_tags"))
            if not isinstance(assignment_id, str) or assignment_id not in normalized:
                errors.append(f"critical_review:{decision_id}:{kind}:unknown_assignment:{assignment_id}")
                continue
            actual = normalized[assignment_id]["assignment"]
            fields_match = (
                actual.get("artifact_type") == "REVIEW"
                and actual.get("role_id") == role_id
                and actual.get("review_kind") == kind.upper()
                and actual.get("review_target_id") == decision_id
                and actual.get("review_check_id") == check_id
            )
            if not fields_match:
                message = f"critical_review:{decision_id}:{kind}:assignment_binding_mismatch:{assignment_id}"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
            if role_id == author_role:
                message = f"critical_review:{decision_id}:{kind}:author_is_reviewer:{role_id}"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
            if role_id not in ROLES:
                errors.append(f"critical_review:{decision_id}:{kind}:invalid_role:{role_id}")
            if not isinstance(check_id, str) or not check_id:
                errors.append(f"critical_review:{decision_id}:{kind}:missing_check_id")
            if required_tags is None or not required_tags:
                message = f"critical_review:{decision_id}:{kind}:missing_required_tags"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
            else:
                missing = sorted(set(required_tags) - competence_sets.get(str(role_id), set()))
                if missing:
                    message = f"critical_review:{decision_id}:{kind}:role:{role_id}:missing_competence:{','.join(missing)}"
                    errors.append(message)
                    assignment_errors[assignment_id].add(message)
            if isinstance(role_id, str):
                seen_roles.append(role_id)
            if isinstance(check_id, str):
                seen_checks.append(check_id)
        if len(seen_roles) == 2 and len(set(seen_roles)) != 2:
            errors.append(f"critical_review:{decision_id}:reviewers_not_distinct")
            for kind in ("subject", "system"):
                review = contract.get(kind, {})
                if isinstance(review, dict) and isinstance(review.get("assignment_id"), str):
                    assignment_errors[review["assignment_id"]].add(f"critical_review:{decision_id}:reviewers_not_distinct")
        if len(seen_checks) == 2 and len(set(seen_checks)) != 2:
            errors.append(f"critical_review:{decision_id}:check_ids_not_distinct")


def _evidence_contracts(
    approved_map: dict[str, Any],
    normalized: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    snapshot: str,
    errors: list[str],
    assignment_errors: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    approved_raw = _mapping(approved_map.get("evidence_applicability", {}))
    if approved_raw is None:
        errors.append("approved_map:evidence_applicability:not_mapping")
        approved_raw = {}
    approved: dict[str, set[str]] = {}
    for evidence_id in sorted(approved_raw):
        consumers = _strings(approved_raw[evidence_id])
        if consumers is None:
            errors.append(f"approved_map:evidence_applicability:{evidence_id}:invalid_consumers")
        else:
            approved[evidence_id] = set(consumers)

    evidence: dict[str, dict[str, str]] = {}
    evidence_result: dict[str, str] = {}
    result_to_evidence: dict[str, str] = {}
    for result_id in sorted(results):
        entry = results[result_id]
        doc = docs.get(result_id)
        if entry.get("artifact_type") != "EVIDENCE" and (not isinstance(doc, dict) or doc.get("artifact_type") != "EVIDENCE"):
            continue
        if not isinstance(doc, dict):
            errors.append(f"evidence_result:{result_id}:missing_document")
            continue
        evidence_id = doc.get("id")
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append(f"evidence_result:{result_id}:missing_evidence_id")
            continue
        if evidence_id in evidence:
            errors.append(f"evidence:{evidence_id}:duplicate_accepted_result")
            continue
        key: dict[str, str] = {"evidence_id": evidence_id}
        raw_values = {
            "content_hash": entry.get("content_hash"),
            "input_snapshot": entry.get("input_snapshot", doc.get("input_snapshot")),
            "tool_id": doc.get("tool_id"),
            "tool_version": doc.get("tool_version"),
            "policy_hash": doc.get("policy_hash"),
            "exposure_hash": doc.get("exposure_hash", doc.get("exposure_key")),
            "freshness": doc.get("freshness"),
        }
        missing = [field for field, value in raw_values.items() if not isinstance(value, str) or not value]
        if missing:
            errors.append(f"evidence:{evidence_id}:incomplete_exact_key:{','.join(missing)}")
        else:
            key.update({field: str(value) for field, value in raw_values.items()})
            valid = True
            if key["input_snapshot"] != snapshot:
                errors.append(f"evidence:{evidence_id}:input_snapshot:mismatch")
                valid = False
            if key["freshness"].strip().upper() in INVALID_FRESHNESS:
                errors.append(f"evidence:{evidence_id}:freshness:{key['freshness']}:not_current")
                valid = False
            if valid:
                evidence[evidence_id] = key
                evidence_result[evidence_id] = result_id
                result_to_evidence[result_id] = evidence_id

    consumers_seen: dict[str, set[str]] = defaultdict(set)
    for assignment_id in sorted(normalized):
        assignment = normalized[assignment_id]["assignment"]
        raw_contracts = assignment.get("evidence_applicability", [])
        if not isinstance(raw_contracts, list):
            message = f"assignment:{assignment_id}:evidence_applicability:not_list"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
            continue
        assigned = assignment.get("assigned_ids", {})
        assigned_evidence = set(_strings(assigned.get("evidence", [])) or []) if isinstance(assigned, dict) else set()
        declared: set[str] = set()
        for index, contract in enumerate(raw_contracts):
            if not isinstance(contract, dict):
                message = f"assignment:{assignment_id}:evidence_applicability:{index}:not_mapping"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                continue
            evidence_id = contract.get("evidence_id")
            if isinstance(evidence_id, str):
                declared.add(evidence_id)
            missing = [field for field in EVIDENCE_KEY_FIELDS if not isinstance(contract.get(field), str) or not contract.get(field)]
            consumers = _strings(contract.get("consumer_ids"))
            if missing or consumers is None or not consumers:
                message = f"assignment:{assignment_id}:evidence_applicability:{index}:incomplete_exact_contract"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                continue
            assert isinstance(evidence_id, str)
            current = evidence.get(evidence_id)
            if current is None:
                message = f"assignment:{assignment_id}:evidence:{evidence_id}:not_reusable"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                continue
            contract_valid = True
            mismatched = [field for field in EVIDENCE_KEY_FIELDS if contract[field] != current[field]]
            if mismatched:
                message = f"assignment:{assignment_id}:evidence:{evidence_id}:exact_key_mismatch:{','.join(mismatched)}"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                contract_valid = False
            unapproved = sorted(set(consumers) - approved.get(evidence_id, set()))
            if unapproved:
                message = f"assignment:{assignment_id}:evidence:{evidence_id}:unapproved_consumers:{','.join(unapproved)}"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                contract_valid = False
            local_consumers: set[str] = {str(assignment.get("result_id", ""))}
            if isinstance(assigned, dict):
                for values in assigned.values():
                    if isinstance(values, list):
                        local_consumers.update(item for item in values if isinstance(item, str))
            unrelated = sorted(set(consumers) - local_consumers)
            if unrelated:
                message = f"assignment:{assignment_id}:evidence:{evidence_id}:consumer_not_owned:{','.join(unrelated)}"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                contract_valid = False
            if contract_valid:
                consumers_seen[evidence_id].update(consumers)
        for evidence_id in sorted(assigned_evidence - declared):
            message = f"assignment:{assignment_id}:evidence:{evidence_id}:missing_exact_applicability"
            errors.append(message)
            assignment_errors[assignment_id].add(message)

    reuse = [
        {
            **evidence[evidence_id],
            "result_id": evidence_result[evidence_id],
            "consumer_ids": sorted(consumers_seen.get(evidence_id, set())),
            "reused": len(consumers_seen.get(evidence_id, set())) > 1,
        }
        for evidence_id in sorted(evidence)
    ]
    return reuse, evidence_result, result_to_evidence


def _semantic_stale(
    normalized: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    changed: list[str],
    evidence_result: dict[str, str],
    result_to_evidence: dict[str, str],
    errors: list[str],
    persisted_stale_result_ids: list[str] | None = None,
) -> set[str]:
    graph: dict[str, set[str]] = defaultdict(set)
    doc_id_to_result: dict[str, str] = {}
    for result_id in sorted(docs):
        doc = docs[result_id]
        doc_id = doc.get("id")
        if isinstance(doc_id, str):
            if doc_id in doc_id_to_result and doc_id_to_result[doc_id] != result_id:
                errors.append(f"accepted_docs:id:{doc_id}:duplicate")
            else:
                doc_id_to_result[doc_id] = result_id
        # Semantic relationships do not invalidate consumers.  Accept the
        # current execution/provenance graphs and the legacy execution field.
        for field in ("dependency_edges", "evidence_provenance_edges", "execution_edges"):
            edges = doc.get(field, [])
            if not isinstance(edges, list):
                errors.append(f"accepted_doc:{result_id}:{field}:not_list")
                continue
            for index, edge in enumerate(edges):
                if not isinstance(edge, dict) or not isinstance(edge.get("from_id"), str) or not isinstance(edge.get("to_id"), str):
                    errors.append(f"accepted_doc:{result_id}:{field}:{index}:invalid")
                    continue
                graph[edge["from_id"]].add(edge["to_id"])

    starts: set[str] = set(persisted_stale_result_ids or [])
    for evidence_id in changed:
        semantic_id = result_to_evidence.get(evidence_id, evidence_id)
        if semantic_id not in evidence_result:
            errors.append(f"changed_evidence:{evidence_id}:not_accepted")
            continue
        starts.add(semantic_id)
        starts.add(evidence_result[semantic_id])
    stale = _closure(graph, starts)

    result_by_assignment = {
        assignment_id: assignment["assignment"].get("result_id")
        for assignment_id, assignment in normalized.items()
    }
    changed_flag = True
    while changed_flag:
        changed_flag = False
        for assignment_id in sorted(normalized):
            assignment = normalized[assignment_id]["assignment"]
            result_id = result_by_assignment.get(assignment_id)
            assigned = assignment.get("assigned_ids", {})
            semantic_ids: set[str] = set()
            if isinstance(assigned, dict):
                for values in assigned.values():
                    if isinstance(values, list):
                        semantic_ids.update(item for item in values if isinstance(item, str))
            dependencies = set(_strings(assignment.get("dependency_result_ids", [])) or [])
            if isinstance(result_id, str) and result_id not in stale and ((semantic_ids & stale) or (dependencies & stale)):
                stale.add(result_id)
                changed_flag = True
        for doc_id, result_id in sorted(doc_id_to_result.items()):
            if doc_id in stale and result_id not in stale:
                stale.add(result_id)
                changed_flag = True
        expanded = _closure(graph, stale)
        if not expanded.issubset(stale):
            stale.update(expanded)
            changed_flag = True
    return stale


def validate_orchestration(
    bank: dict[str, Any],
    approved_map: dict[str, Any] | None,
    assignments: dict[str, Any],
    accepted_results: dict[str, Any],
    accepted_docs: dict[str, Any],
    changed_evidence_ids: list[str],
    verified_completed_unknown: dict[str, str] | None = None,
    selected_question_claims: dict[str, dict[str, str]] | None = None,
    persisted_stale_result_ids: list[str] | None = None,
    question_gap_proofs: dict[str, dict[str, str]] | None = None,
    question_primary_owners: dict[str, str] | None = None,
    question_retired_claims: set[tuple[str, str, str]] | None = None,
    *,
    effect_task_states: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and classify an approved orchestration snapshot.

    ``approved_map is None`` returns ``routing_status=NOT_CONFIGURED`` with
    empty routing lists.  It keeps legacy recovery usable without treating
    absence of semantic ROOT approval as validated automation.
    """
    if approved_map is None:
        return _empty("NOT_CONFIGURED")

    output = _empty("INVALID")
    errors: list[str] = output["errors"]
    if not isinstance(approved_map, dict):
        errors.append("approved_map:not_mapping")
        return output
    snapshot = approved_map.get("input_snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        errors.append("approved_map:input_snapshot:missing")
        snapshot = ""
    if not isinstance(changed_evidence_ids, list) or any(not isinstance(item, str) or not item for item in changed_evidence_ids):
        errors.append("changed_evidence_ids:invalid_string_list")
        changed: list[str] = []
    else:
        changed = list(changed_evidence_ids)
        if len(changed) != len(set(changed)):
            errors.append("changed_evidence_ids:duplicate")

    assignment_errors: dict[str, set[str]] = defaultdict(set)
    normalized = _normal_assignments(assignments, errors, verified_completed_unknown)
    for assignment_id, wrapped in normalized.items():
        assignment_errors[assignment_id].update(wrapped.get("history_errors", []))
    results, docs = _accepted(accepted_results, accepted_docs, errors)
    derived_question_ids={
        record["q_id"]
        for doc in docs.values() if isinstance(doc,dict)
        for record in doc.get("records",[]) if isinstance(record,dict)
        and record.get("record_kind")=="DERIVED_Q"
        and isinstance(record.get("q_id"),str) and record["q_id"]
    }
    _approved_contract(
        bank,
        approved_map,
        normalized,
        snapshot,
        errors,
        assignment_errors,
        derived_question_ids,
        selected_question_claims if selected_question_claims is not None else {},
        results,
        question_gap_proofs or {},
        question_primary_owners,
        question_retired_claims or set(),
    )
    for result_id in sorted(results):
        entry = results[result_id]
        if entry.get("input_snapshot") != snapshot:
            errors.append(f"accepted_result:{result_id}:input_snapshot:mismatch")

    reuse, evidence_result, result_to_evidence = _evidence_contracts(
        approved_map, normalized, results, docs, snapshot, errors, assignment_errors
    )
    output["reuse"] = reuse
    stale = _semantic_stale(
        normalized, results, docs, changed, evidence_result, result_to_evidence, errors, persisted_stale_result_ids
    )

    producers: dict[str, str] = {}
    dependency_graph: dict[str, set[str]] = defaultdict(set)
    dependencies_by_assignment: dict[str, list[str]] = {}
    for assignment_id in sorted(normalized):
        assignment = normalized[assignment_id]["assignment"]
        result_id = assignment.get("result_id")
        if not isinstance(result_id, str) or not result_id:
            message = f"assignment:{assignment_id}:missing_result_id"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
            continue
        if result_id in producers:
            message = f"result:{result_id}:duplicate_producers:{producers[result_id]},{assignment_id}"
            errors.append(message)
            assignment_errors[assignment_id].add(message)
            assignment_errors[producers[result_id]].add(message)
        else:
            producers[result_id] = assignment_id

    for assignment_id in sorted(normalized):
        assignment = normalized[assignment_id]["assignment"]
        result_id = assignment.get("result_id")
        dependencies = _strings(assignment.get("dependency_result_ids", []))
        if dependencies is None:
            dependencies = []
        dependencies_by_assignment[assignment_id] = dependencies
        if not isinstance(result_id, str):
            continue
        dependency_graph.setdefault(result_id, set())
        hashes = _strings(assignment.get("dependency_hashes", [])) or []
        for dependency in dependencies:
            if dependency not in results and dependency not in producers:
                message = f"dependency:{assignment_id}:{dependency}:unknown_endpoint"
                errors.append(message)
                assignment_errors[assignment_id].add(message)
                continue
            dependency_graph[dependency].add(result_id)
            if dependency in results:
                content_hash = results[dependency].get("content_hash")
                if not isinstance(content_hash, str) or content_hash not in hashes:
                    message = f"dependency:{assignment_id}:{dependency}:missing_exact_hash"
                    errors.append(message)
                    assignment_errors[assignment_id].add(message)
        extra_hashes = sorted(set(hashes) - {str(results[dep].get("content_hash")) for dep in dependencies if dep in results})
        if extra_hashes:
            message = f"dependency:{assignment_id}:unbound_hashes:{','.join(extra_hashes)}"
            errors.append(message)
            assignment_errors[assignment_id].add(message)

    cycles = _cycle_nodes(dependency_graph)
    if cycles:
        errors.append(f"dependency_graph:cycle:{','.join(sorted(cycles))}")
        for result_id in cycles:
            if result_id in producers:
                assignment_errors[producers[result_id]].add("dependency_graph:cycle")

    # The stale closure includes semantic nodes for auditability and accepted
    # registry result IDs for the durable controller.
    for assignment_id, reasons in assignment_errors.items():
        wrapped = normalized.get(assignment_id)
        if reasons and wrapped is not None and wrapped["status"] == "ACCEPTED":
            result_id = wrapped["assignment"].get("result_id")
            if isinstance(result_id, str) and result_id in results:
                stale.add(result_id)
    stale.update(_closure(dependency_graph, stale))
    output["stale_ids"] = sorted(stale)

    states: dict[str, str] = {}
    blockers: dict[str, set[str]] = defaultdict(set)
    effect_tasks = effect_task_states or {}
    unresolved = set(normalized)
    for _ in range(len(normalized) + 1):
        progressed = False
        for assignment_id in sorted(list(unresolved)):
            wrapped = normalized[assignment_id]
            assignment = wrapped["assignment"]
            result_id = assignment.get("result_id")
            dependency_ids = dependencies_by_assignment.get(assignment_id, [])
            effect_task = effect_tasks.get((assignment_id, assignment["attempt_id"]))
            if isinstance(result_id, str) and result_id in stale:
                states[assignment_id] = "STALE"
                blockers[assignment_id].update(assignment_errors.get(assignment_id, set()))
                blockers[assignment_id].add("stale_result_or_dependency")
            elif assignment_errors.get(assignment_id):
                states[assignment_id] = "INVALID"
                blockers[assignment_id].update(assignment_errors[assignment_id])
            elif effect_task and effect_task["reason"] != "effect_resource_occupied":
                states[assignment_id] = effect_task["state"]
                blockers[assignment_id].add(effect_task["reason"])
            else:
                planned_dependencies = [producers[dep] for dep in dependency_ids if dep not in results and dep in producers]
                if any(dep_assignment not in states for dep_assignment in planned_dependencies):
                    continue
                unavailable = [
                    dep
                    for dep in dependency_ids
                    if dep not in results
                    or dep in stale
                ]
                if unavailable:
                    states[assignment_id] = "WAITING"
                    blockers[assignment_id].update(f"dependency_not_current:{dep}" for dep in sorted(unavailable))
                elif wrapped["status"] == "ACCEPTED":
                    if result_id not in results:
                        states[assignment_id] = "INVALID"
                        blockers[assignment_id].add("accepted_status_without_registry_result")
                        errors.append(f"assignment:{assignment_id}:accepted_status_without_registry_result")
                    else:
                        states[assignment_id] = "ACCEPTED"
                else:
                    launch_state = wrapped["launch_state"]
                    if launch_state == "DISPATCH_UNKNOWN":
                        states[assignment_id] = "DISPATCH_UNKNOWN"
                        blockers[assignment_id].add("dispatch_outcome_unknown_no_replay")
                    elif launch_state == "RUNNING" or wrapped["status"] == "RUNNING":
                        states[assignment_id] = "RUNNING"
                        blockers[assignment_id].add("attempt_running")
                    elif launch_state == "READY_TO_DISPATCH":
                        states[assignment_id] = "READY"
                    elif launch_state == "NOT_LAUNCHED":
                        states[assignment_id] = "NOT_LAUNCHED"
                    else:
                        states[assignment_id] = "INVALID"
                        message = f"assignment:{assignment_id}:launch_state:{launch_state}:invalid"
                        errors.append(message)
                        blockers[assignment_id].add(message)
            unresolved.remove(assignment_id)
            progressed = True
        if not progressed:
            break
    for assignment_id in sorted(unresolved):
        states[assignment_id] = "INVALID"
        blockers[assignment_id].add("dependency_graph:unresolved_cycle")

    eligible: list[str] = []
    selected: list[str] = []
    serialized_after: dict[str, list[str]] = defaultdict(list)
    for assignment_id in sorted(normalized):
        if states.get(assignment_id) not in {"NOT_LAUNCHED", "READY"}:
            continue
        assignment = normalized[assignment_id]["assignment"]
        effect_task = effect_tasks.get((assignment_id, assignment["attempt_id"]))
        if effect_task and effect_task["reason"] == "effect_resource_occupied":
            states[assignment_id] = "WAITING"
            blockers[assignment_id].update(
                f"effect_resource_occupied:{holder}" for holder in effect_task["resource_holder_assignment_ids"]
            )
            continue
        eligible.append(assignment_id)
        conflicts = [
            earlier
            for earlier in selected
            if dispatch_resource_conflict(assignment, normalized[earlier]["assignment"])
        ]
        if conflicts:
            serialized_after[assignment_id].extend(conflicts)
            blockers[assignment_id].update(f"serialized_conflict:{earlier}" for earlier in conflicts)
            if states[assignment_id] == "READY":
                states[assignment_id] = "WAITING"
        else:
            selected.append(assignment_id)

    proposals: list[dict[str, Any]] = []
    for assignment_id in eligible:
        assignment = normalized[assignment_id]["assignment"]
        proposals.append(
            {
                "assignment_id": assignment_id,
                "attempt_id": assignment.get("attempt_id"),
                "role_id": assignment.get("role_id"),
                "group_id": str(assignment.get("group_id", "1")),
                "result_id": assignment.get("result_id"),
                "state": states[assignment_id],
                "dependency_result_ids": sorted(dependencies_by_assignment.get(assignment_id, [])),
                "dependency_hashes": sorted(_strings(assignment.get("dependency_hashes", [])) or []),
                "sole_writer_paths": sorted(_strings(assignment.get("sole_writer_paths", [])) or []),
                "resource_keys": sorted(_strings(assignment.get("resource_keys", [])) or []),
                "reasons": ["root_approved_exact_route", "dependencies_current"],
                "serialization_after": sorted(serialized_after.get(assignment_id, [])),
            }
        )
    output["proposals"] = proposals

    output["task_states"] = [
        {
            "assignment_id": assignment_id,
            "attempt_id": normalized[assignment_id]["assignment"].get("attempt_id"),
            "result_id": normalized[assignment_id]["assignment"].get("result_id"),
            "state": states[assignment_id],
            "blockers": sorted(blockers.get(assignment_id, set())),
            "stale_ids": sorted(
                set(dependencies_by_assignment.get(assignment_id, [])) & stale
                | ({str(normalized[assignment_id]["assignment"].get("result_id"))} & stale)
            ),
        }
        for assignment_id in sorted(normalized)
    ]
    output["blockers"] = [
        {"assignment_id": assignment_id, "reasons": sorted(reasons)}
        for assignment_id, reasons in sorted(blockers.items())
        if reasons
    ]
    ready_by_group: dict[str, list[str]] = defaultdict(list)
    for assignment_id in selected:
        if states.get(assignment_id) == "READY":
            group_id = str(normalized[assignment_id]["assignment"].get("group_id", "1"))
            if group_id in GROUPS:
                ready_by_group[group_id].append(assignment_id)
    output["ready_groups"] = [
        {"group_id": group_id, "assignment_ids": sorted(ready_by_group[group_id])}
        for group_id in sorted(ready_by_group)
    ]
    output["errors"] = sorted(set(errors))
    output["routing_status"] = "INVALID" if output["errors"] else "VALIDATED"
    return output


__all__ = ["validate_orchestration"]
