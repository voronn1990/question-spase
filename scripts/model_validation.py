#!/usr/bin/env python3
"""Addressable structural-model checks for Question Space v4.

The public entry point is deliberately pure with respect to package state: it
reads the supplied run through ``load_yaml`` and ``sha`` and returns stable,
addressed error strings.  A clean result means structural closure only; this
module cannot establish that claims, reviews, or audit findings are true.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Iterable
from command_admission import AdmissionError, capture_file
from plan_semantics import normalize_plan_order, resolve_plan_input


_HASH_LEN = 64
_STAGE_SECTIONS = {
    "1": "identity_result",
    "2": "local_execution_graph",
    "3": "inputs_contracts",
    "4": "tasks",
    "5": "outputs_handoff",
    "6": "acceptance_exit",
    "7": "resources_stop",
    "8": "errors_recovery_evidence",
}
_TASK_REQUIRED = {
    "id", "owner", "execution_status", "result", "procedure", "algorithms",
    "inputs", "dependencies", "outputs", "errors_recovery", "resources",
    "acceptance_criteria", "checks", "r_refs", "d_refs", "e_refs",
    "branch_guard", "writes", "serialized_with",
}
_TASK_STATUS = {
    "NOT_STARTED", "RUNNING", "ACCEPTED", "FAILED", "BLOCKED",
    "NEEDS_RECHECK", "NOT_APPLICABLE",
}
_CHECK_REQUIRED = {
    "id", "property", "task_id", "inputs", "preconditions", "environment",
    "procedure", "expected_observation", "negative_check", "boundary_check",
    "sensitivity_check", "checker",
}
_GRAPH_TYPES = ("E", "Q", "D", "REVIEW", "T", "C")
_ICEBERG_FEATURE = "DOUBLE_ICEBERG_V1"
_ARCHITECTURE_FEATURE = "ARCHITECTURE_GRAPH_V1"
_ICEBERG_VIEW_OWNERS = {
    "CURRENT_REALITY": "OP-PROJECTION",
    "DESIRED_REALITY": "OP-REVERSE",
}
_ICEBERG_ELEMENT_KINDS = {
    "CONDITION", "CAPABILITY", "OBSTACLE", "ASSUMPTION", "MECHANISM",
    "OBSERVATION", "UNKNOWN",
}
_ICEBERG_RELATIONS = {
    "NECESSARY_FOR", "SUFFICIENT_FOR_NOT_G", "RISK_FOR",
    "CAUSES_HYPOTHESIS", "CAUSAL_INFLUENCE", "CORRELATES_WITH",
    "MECHANISM_DEPENDS_ON", "ENABLES", "ALTERNATIVE_TO", "CONTRADICTS",
    "BYPASSES", "MITIGATES", "FEEDBACK_WITH",
}
_ICEBERG_COMBINATIONS = {
    "SINGLE", "AND", "OR", "SEQUENCE", "SHARED_FAILURE", "FEEDBACK",
}
_ICEBERG_KNOWLEDGE = {"OBSERVED", "DERIVED", "HYPOTHESIS", "UNKNOWN"}
_ICEBERG_FINITE_CHECKS = {
    "SOURCE", "OBSERVATION", "EXPERIMENT", "NOT_CURRENTLY_AVAILABLE",
}
_INPUT_KINDS = {"EXISTING", "PLANNED", "EXTERNAL"}
_OUTPUT_AVAILABILITY = {"UNCONDITIONAL", "SELECTED_BRANCH", "UNSELECTED_BRANCH"}
_ACCEPTANCE_STATUS = {"PLANNED", "ACCEPTED"}
_EXTERNAL_AVAILABILITY = {"AVAILABLE", "UNAVAILABLE"}
_CONDITION_KINDS = {
    "CENTRAL_UNCERTAINTY", "REPAIRABLE_DEFECT", "REQUIRED_EXTERNAL_INPUT",
    "RESIDUAL_CONDITION", "FUTURE_CHECK",
}
_OUTCOMES = {"PASS", "FAIL", "NOT_VERIFIED"}
_GATE_DECISIONS = {"PASS", "CONDITIONAL", "FAIL"}
_READINESS = {"READY", "NOT_READY", "UNVERIFIED", "BLOCKED"}
_READINESS_AXES = {"package", "runtime", "quality", "plan", "implementation", "effect"}
_VERDICTS = {"READY", "REPAIR_REQUIRED", "RESEARCH_REQUIRED", "BLOCKED"}
_ANSWER_STATUSES = {"ANSWERED", "OPEN", "NOT_APPLICABLE"}
_KNOWLEDGE_STATES = {"OBSERVED", "DERIVED", "HYPOTHESIS", "REFUTED", "UNKNOWN"}
_MEANINGLESS = {
    "", "pass", "passed", "ok", "accepted", "checked", "complete",
    "completed", "done", "none", "n/a", "na", "not applicable",
}


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _MEANINGLESS


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _hash(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != _HASH_LEN:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    answer: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            answer.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            answer.append(item["id"])
    return answer


def _requirement_ids(bank: Any) -> set[str]:
    if not isinstance(bank, dict) or not isinstance(bank.get("requirements"), list):
        return set()
    answer: set[str] = set()
    for item in bank["requirements"]:
        if isinstance(item, str) and item:
            answer.add(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            answer.add(item["id"])
    return answer


def _question_ids(bank: Any, docs: dict[str, dict[str, Any]]) -> set[str]:
    answer = {
        item["id"]
        for item in (bank.get("questions", []) if isinstance(bank, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }
    for doc in docs.values():
        records = doc.get("records") if isinstance(doc, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if (
                isinstance(record, dict)
                and record.get("record_kind") == "DERIVED_Q"
                and isinstance(record.get("q_id"), str)
                and record["q_id"]
            ):
                answer.add(record["q_id"])
    return answer


def _entry_path(run: Path, entry: Any) -> Path | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        return None
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return None
    return run / relative


def _immutable_input_hashes(
    run: Path,
    bank: dict[str, Any],
    sha: Callable[[bytes], str],
) -> dict[str, str]:
    """Resolve exact immutable input paths and frozen R IDs to saved bytes."""
    input_dir = run / "input"
    if not input_dir.is_dir():
        return {}
    input_root = input_dir.resolve()
    resolved: dict[str, str] = {}
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(input_root)
            content_hash = sha(path.read_bytes())
        except (OSError, ValueError):
            continue
        if _hash(content_hash):
            resolved[path.relative_to(run).as_posix()] = content_hash
    bank_hash = resolved.get("input/bank.yaml")
    if bank_hash is not None:
        for requirement_id in _requirement_ids(bank):
            resolved[requirement_id] = bank_hash
    return resolved


def _load_mapping(path: Path, load_yaml: Callable[[Path], Any]) -> dict[str, Any] | None:
    try:
        value = load_yaml(path)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _json_pointer(document: Any, pointer: Any) -> Any:
    """Resolve a canonical RFC-6901-style field address, returning a sentinel."""
    missing = _json_pointer
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return missing
    value = document
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return missing
    return value


def double_iceberg_enabled(snapshot: Any) -> bool:
    """Return whether the immutable snapshot explicitly selects the feature."""
    return (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("methodology_features"), list)
        and _ICEBERG_FEATURE in snapshot["methodology_features"]
    )


def _vector_document(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("artifact_type") == "OPERATOR"
        and doc.get("author_role", doc.get("role_id")) == "OP-VECTOR"
        and ("G" in doc or "goal" in doc)
        and isinstance(doc.get("requirements"), list)
    )


def _resolve_vector_anchor(
    run: Path,
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    load_yaml: Callable[[Path], Any],
    errors: list[str],
) -> tuple[dict[str, Any] | None, set[str]]:
    """Resolve local or imported Vector bytes without treating applicability as approval."""
    snapshot = _load_mapping(run / "input" / "snapshot.yaml", load_yaml)
    imported = (
        isinstance(snapshot, dict)
        and snapshot.get("input_mode") == "BANK_TO_PLAN"
        and snapshot.get("bank_entry_kind") == "APPROVED_RUN_IMPORT"
    )
    applicability = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "OPERATOR"
        and entry.get("role_id") == "OP-VECTOR"
        and isinstance(docs.get(result_id), dict)
        and docs[result_id].get("operator_contract") == "BANK_APPLICABILITY"
    }
    if imported:
        binding = snapshot.get("import_binding")
        prefix = "vector_anchor:IMPORTED_APPROVED_VECTOR"
        if not isinstance(binding, dict):
            errors.append(prefix + ":import_binding")
            return None, set()
        required = {
            "source_vector_result_id", "source_vector_sha256",
            "source_vector_revision", "vector_path", "vector_sha256",
            "source_approval_id", "source_approval_decision", "approval_path",
            "approval_sha256", "import_payload_sha256",
        }
        if not required.issubset(binding):
            errors.append(prefix + ":import_binding_fields")
        vector_path = binding.get("vector_path")
        approval_path = binding.get("approval_path")
        vector = _load_mapping(run / vector_path, load_yaml) if _safe_relative_path(vector_path) else None
        approval = _load_mapping(run / approval_path, load_yaml) if _safe_relative_path(approval_path) else None
        vector_hash = immutable_inputs.get(vector_path) if isinstance(vector_path, str) else None
        approval_hash = immutable_inputs.get(approval_path) if isinstance(approval_path, str) else None
        vector_id = binding.get("source_vector_result_id")
        valid = True
        if (
            not _vector_document(vector)
            or vector.get("id") != vector_id
            or vector.get("revision") != binding.get("source_vector_revision")
            or vector_hash != binding.get("vector_sha256")
            or binding.get("source_vector_sha256") != binding.get("vector_sha256")
        ):
            errors.append(prefix + ":vector_binding")
            valid = False
        if (
            not isinstance(approval, dict)
            or approval.get("approval_id") != binding.get("source_approval_id")
            or approval.get("decision") != binding.get("source_approval_decision")
            or approval.get("decision") != "APPROVE"
            or approval.get("vector_result_id") != vector_id
            or approval.get("vector_sha256") != binding.get("vector_sha256")
            or approval.get("vector_revision") != binding.get("source_vector_revision")
            or approval_hash != binding.get("approval_sha256")
        ):
            errors.append(prefix + ":approval_binding")
            valid = False
        admission = _load_mapping(run / "input" / "prep-admission.yaml", load_yaml)
        if not isinstance(admission, dict):
            errors.append(prefix + ":admission_binding")
            valid = False
        else:
            vector_binding = admission.get("vector_binding")
            approval_binding = admission.get("approval_binding")
            if not (
                isinstance(vector_binding, dict)
                and vector_binding.get("result_id") == vector_id
                and vector_binding.get("sha256") == binding.get("vector_sha256")
                and isinstance(approval_binding, dict)
                and approval_binding.get("id") == binding.get("source_approval_id")
                and approval_binding.get("sha256") == binding.get("approval_sha256")
            ):
                errors.append(prefix + ":admission_binding")
                valid = False
        if len(applicability) != 1:
            errors.append(prefix + f":applicability_cardinality:{len(applicability)}")
            valid = False
        else:
            app_id = next(iter(applicability))
            app = docs[app_id]
            current_snapshots = {
                entry.get("input_snapshot") for entry in results.values()
                if isinstance(entry, dict) and isinstance(entry.get("input_snapshot"), str)
            }
            current_snapshot = next(iter(current_snapshots)) if len(current_snapshots) == 1 else None
            if (
                current_snapshot is None
                or app.get("current_input_snapshot") != current_snapshot
                or app.get("current_run_id") != snapshot.get("run_id")
                or app.get("import_payload_sha256") != binding.get("import_payload_sha256")
                or app.get("overall") != "UNCHANGED"
            ):
                errors.append(prefix + ":applicability_binding")
                valid = False
        goal_path = "/G" if isinstance(vector, dict) and "G" in vector else "/goal"
        if valid and isinstance(vector, dict):
            return ({
                "anchor_kind": "IMPORTED_APPROVED_VECTOR",
                "vector_id": vector_id,
                "vector_sha256": binding.get("vector_sha256"),
                "revision": binding.get("source_vector_revision"),
                "approval_binding_id": binding.get("source_approval_id"),
                "approval_sha256": binding.get("approval_sha256"),
                "applicability_result_id": next(iter(applicability)),
                "goal_claim_path": goal_path,
                "vector_path": vector_path,
                "vector_document": vector,
            }, set(applicability))
        return None, set()

    local_vectors = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "OPERATOR"
        and entry.get("role_id") == "OP-VECTOR"
        and _vector_document(docs.get(result_id))
        and result_id not in applicability
    }
    requires_approval = (
        isinstance(snapshot, dict)
        and snapshot.get("input_mode") in {"GOAL_TO_PLAN", "BANK_TO_PLAN"}
    )
    if not requires_approval and len(local_vectors) != 1:
        return None, local_vectors

    prefix = "vector_anchor:LOCAL_ACCEPTED_VECTOR"
    approval = _load_mapping(run / "input" / "vector-approval.yaml", load_yaml)
    if requires_approval:
        vector_id = approval.get("vector_result_id") if approval else None
        if not isinstance(vector_id, str) or vector_id not in local_vectors:
            errors.append(prefix + ":approval_binding")
            return None, set()
    else:
        vector_id = next(iter(local_vectors))
    vector = docs[vector_id]
    approval_id: Any = vector_id
    approval_hash: Any = results[vector_id].get("content_hash")
    if isinstance(approval, dict):
        approval_id = approval.get("approval_id")
        approval_hash = immutable_inputs.get("input/vector-approval.yaml")
        if (
            approval.get("decision") != "APPROVE"
            or approval.get("vector_result_id") != vector_id
            or approval.get("vector_sha256") != results[vector_id].get("content_hash")
            or approval.get("vector_revision") != vector.get("revision")
            or not _hash(approval_hash)
            or (requires_approval and (
                not isinstance(approval_id, str) or not approval_id.strip()
                or not isinstance(vector.get("revision"), str) or not vector["revision"]
            ))
        ):
            errors.append(prefix + ":approval_binding")
            return None, set()

    if requires_approval:
        if not _result_matches_doc(vector_id, results, docs):
            errors.append(prefix + f":accepted_binding:{vector_id}")
            return None, set()

        # Architecture intake can reach this resolver before full PREP validation.
        admission_input = "input/prep-admission.yaml"
        admission_path = run / admission_input
        if admission_path.exists() or admission_path.is_symlink():
            if not _hash(immutable_inputs.get(admission_input)):
                errors.append(prefix + ":admission_binding")
                return None, set()
            from prep_validation import _binding_hash

            admission = _load_mapping(admission_path, load_yaml) or {}
            vector_binding = admission.get("vector_binding")
            if (
                not isinstance(vector_binding, dict)
                or vector_binding.get("result_id") != vector_id
                or _binding_hash(vector_binding) != results[vector_id].get("content_hash")
                or _binding_hash(admission.get("approval_binding")) != approval_hash
            ):
                errors.append(prefix + ":admission_binding")
                return None, set()
    return ({
        "anchor_kind": "LOCAL_ACCEPTED_VECTOR",
        "vector_id": vector_id,
        "vector_sha256": results[vector_id].get("content_hash"),
        "revision": vector.get("revision"),
        "approval_binding_id": approval_id,
        "approval_sha256": approval_hash,
        "applicability_result_id": next(iter(applicability)) if len(applicability) == 1 else vector_id,
        "goal_claim_path": "/G" if "G" in vector else "/goal",
        "vector_path": results[vector_id].get("path"),
        "vector_document": vector,
    }, {vector_id})


def _acyclic(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> bool:
    node_set = set(nodes)
    incoming = {node: 0 for node in node_set}
    outgoing: dict[str, set[str]] = {node: set() for node in node_set}
    for source, target in edges:
        if source in node_set and target in node_set and target not in outgoing[source]:
            outgoing[source].add(target)
            incoming[target] += 1
    queue = deque(sorted(node for node, count in incoming.items() if count == 0))
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for target in sorted(outgoing[node]):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return visited == len(node_set)


def _reachable(edges: Iterable[tuple[str, str]], start: str) -> set[str]:
    graph: dict[str, set[str]] = defaultdict(set)
    for source, target in edges:
        graph[source].add(target)
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for target in graph[node]:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def _edge_pair(value: Any) -> tuple[str, str] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(x, str) and x for x in value):
        return value[0], value[1]
    if not isinstance(value, dict):
        return None
    source = value.get("from", value.get("source"))
    target = value.get("to", value.get("target"))
    if isinstance(source, str) and source and isinstance(target, str) and target:
        return source, target
    return None


def _substantive_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)


def _result_matches_doc(result_id: str, results: dict[str, Any], docs: dict[str, Any]) -> bool:
    entry = results.get(result_id)
    doc = docs.get(result_id)
    return (
        isinstance(entry, dict)
        and isinstance(doc, dict)
        and entry.get("result_id", result_id) == result_id
        and doc.get("id") == result_id
        and doc.get("workflow_status") == "ACCEPTED"
        and entry.get("artifact_type") == doc.get("artifact_type")
        and entry.get("role_id") == doc.get("author_role")
        and entry.get("agent_id") == doc.get("agent_id")
        and entry.get("assignment_id") == doc.get("assignment_id")
        and entry.get("attempt_id") == doc.get("attempt_id")
        and _hash(entry.get("content_hash"))
    )


def _select_current_result(
    results: dict[str, Any],
    artifact_type: str,
    selected_id: str | None,
    label: str,
    errors: list[str],
    *,
    required: bool = True,
) -> str | None:
    """Resolve one immutable accepted revision without guessing among history."""
    candidates = sorted(
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == artifact_type
    )
    if selected_id is not None:
        if selected_id not in candidates:
            errors.append(f"{label}:selected_result_binding:{selected_id}")
            return None
        return selected_id
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        if required:
            errors.append(f"{label}:missing")
        return None
    errors.append(f"{label}:ambiguous_current:" + ",".join(candidates))
    return None


def _finite_check(value: Any, prefix: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(prefix + ":finite_check")
        return False
    kind = value.get("check_kind")
    if (
        kind not in _ICEBERG_FINITE_CHECKS
        or not _text(value.get("procedure"))
        or not _substantive_list(value.get("discriminating_outcomes"))
    ):
        errors.append(prefix + ":finite_check")
        return False
    if kind == "NOT_CURRENTLY_AVAILABLE" and not _text(value.get("unavailable_basis")):
        errors.append(prefix + ":unavailable_basis")
        return False
    if kind != "NOT_CURRENTLY_AVAILABLE" and "unavailable_basis" in value:
        errors.append(prefix + ":unavailable_basis_not_applicable")
        return False
    return True


def validate_blind_iceberg_position(
    framing_result_id: str,
    framing_doc: dict[str, Any],
    *,
    input_snapshot: str,
    vector_id: str,
    vector_sha256: str,
) -> list[str]:
    """Validate one P-owned blind seed set without requiring premature H/Q."""
    errors: list[str] = []
    prefix = f"iceberg_position:{framing_result_id}"
    position = framing_doc.get("iceberg_position") if isinstance(framing_doc, dict) else None
    required = {
        "position_id", "origin_role", "origin_assignment_id", "input_snapshot",
        "elements", "relations",
    }
    if not isinstance(position, dict) or not required.issubset(position):
        return [prefix + ":contract"]
    if (
        position.get("origin_role") != framing_doc.get("author_role")
        or position.get("origin_assignment_id") != framing_doc.get("assignment_id")
        or position.get("input_snapshot") != input_snapshot
        or framing_doc.get("vector_result_id") != vector_id
        or framing_doc.get("vector_sha256") != vector_sha256
    ):
        errors.append(prefix + ":origin_or_vector_binding")
    elements = position.get("elements")
    if not isinstance(elements, list) or not elements:
        errors.append(prefix + ":elements")
        elements = []
    seeds: set[str] = set()
    for index, element in enumerate(elements):
        item_prefix = prefix + f":element:{index}"
        required_element = {
            "seed_id", "candidate_view", "element_kind", "statement", "scope",
            "g_refs", "r_refs", "materiality", "knowledge_status", "source_refs",
            "finite_check",
        }
        if not isinstance(element, dict) or not required_element.issubset(element):
            errors.append(item_prefix + ":contract")
            continue
        seed_id = element.get("seed_id")
        if not isinstance(seed_id, str) or not seed_id or seed_id in seeds:
            errors.append(item_prefix + ":seed_id")
        else:
            seeds.add(seed_id)
        if element.get("candidate_view") not in {*_ICEBERG_VIEW_OWNERS, "CROSS_VIEW"}:
            errors.append(item_prefix + ":candidate_view")
        if element.get("element_kind") not in _ICEBERG_ELEMENT_KINDS:
            errors.append(item_prefix + ":element_kind")
        if element.get("knowledge_status") not in _ICEBERG_KNOWLEDGE:
            errors.append(item_prefix + ":knowledge_status")
        if any(not _text(element.get(field)) for field in ("statement", "scope", "materiality")):
            errors.append(item_prefix + ":substance")
        if element.get("g_refs") != [vector_id] or not _strings(element.get("r_refs"), nonempty=True):
            errors.append(item_prefix + ":g_r_refs")
        if not _strings(element.get("source_refs")):
            errors.append(item_prefix + ":source_refs")
        if element.get("knowledge_status") == "OBSERVED" and not element.get("source_refs"):
            errors.append(item_prefix + ":observed_without_source")
        _finite_check(element.get("finite_check"), item_prefix, errors)
    relations = position.get("relations")
    if not isinstance(relations, list):
        errors.append(prefix + ":relations")
        relations = []
    for index, relation in enumerate(relations):
        item_prefix = prefix + f":relation:{index}"
        required_relation = {
            "seed_edge_id", "relation", "from_seed_id", "to_seed_id", "scope",
            "combination_kind", "knowledge_status", "finite_check",
        }
        if not isinstance(relation, dict) or not required_relation.issubset(relation):
            errors.append(item_prefix + ":contract")
            continue
        if relation.get("from_seed_id") not in seeds or relation.get("to_seed_id") not in seeds:
            errors.append(item_prefix + ":seed_binding")
        if relation.get("relation") not in _ICEBERG_RELATIONS:
            errors.append(item_prefix + ":relation")
        if relation.get("combination_kind") not in _ICEBERG_COMBINATIONS:
            errors.append(item_prefix + ":combination_kind")
        if relation.get("knowledge_status") not in _ICEBERG_KNOWLEDGE or not _text(relation.get("scope")):
            errors.append(item_prefix + ":knowledge_or_scope")
        _finite_check(relation.get("finite_check"), item_prefix, errors)
    return errors


def _origin_ref_index(
    results: dict[str, Any], docs: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for result_id, entry in results.items():
        doc = docs.get(result_id)
        position = doc.get("iceberg_position") if isinstance(doc, dict) else None
        if not isinstance(position, dict):
            continue
        for element in position.get("elements", []):
            if isinstance(element, dict) and isinstance(element.get("seed_id"), str):
                index[(result_id, element["seed_id"])] = {
                    "framing_result_id": result_id,
                    "framing_result_sha256": entry.get("content_hash"),
                    "position_id": position.get("position_id"),
                    "seed_id": element["seed_id"],
                    "origin_role": entry.get("role_id"),
                    "unavailable": isinstance(element.get("finite_check"), dict)
                    and element["finite_check"].get("check_kind") == "NOT_CURRENTLY_AVAILABLE",
                }
    return index


def _validate_origin_refs(
    value: Any,
    origin_index: dict[tuple[str, str], dict[str, Any]],
    prefix: str,
    errors: list[str],
) -> tuple[set[str], bool]:
    if not isinstance(value, list) or not value:
        errors.append(prefix + ":origin_position_refs")
        return set(), False
    roles: set[str] = set()
    unavailable = False
    seen: set[tuple[str, str]] = set()
    for index, ref in enumerate(value):
        ref_prefix = prefix + f":origin_position_ref:{index}"
        if not isinstance(ref, dict):
            errors.append(ref_prefix + ":contract")
            continue
        key = (ref.get("framing_result_id"), ref.get("seed_id"))
        expected = origin_index.get(key)
        if expected is None or any(ref.get(field) != expected.get(field) for field in (
            "framing_result_id", "framing_result_sha256", "position_id", "seed_id", "origin_role"
        )):
            errors.append(ref_prefix + ":binding")
            continue
        if key in seen:
            errors.append(ref_prefix + ":duplicate")
        seen.add(key)
        roles.add(str(expected["origin_role"]))
        unavailable = unavailable or bool(expected["unavailable"])
    return roles, unavailable


def current_iceberg_view_projection(
    results: dict[str, Any],
    docs: dict[str, Any],
    *,
    input_snapshot: str,
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    """Derive unique current heads and retained ancestors from exact lineage."""
    errors: list[str] = []
    projection: dict[str, dict[str, Any]] = {}
    historical: set[str] = set()
    by_kind: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(dict)
    for result_id, entry in results.items():
        doc = docs.get(result_id)
        view = doc.get("iceberg_view") if isinstance(doc, dict) else None
        if not isinstance(view, dict):
            continue
        kind = view.get("view_kind")
        if kind not in _ICEBERG_VIEW_OWNERS:
            errors.append(f"iceberg_view:{result_id}:view_kind")
            continue
        if entry.get("artifact_type") != "OPERATOR" or entry.get("role_id") != _ICEBERG_VIEW_OWNERS[kind]:
            errors.append(f"iceberg_view:{result_id}:owner")
        if view.get("input_snapshot") != input_snapshot:
            errors.append(f"iceberg_view:{result_id}:input_snapshot")
        view_id = view.get("view_id")
        if not isinstance(view_id, str) or not view_id or view_id in by_kind[kind]:
            errors.append(f"iceberg_view:{result_id}:view_id")
            continue
        by_kind[kind][view_id] = (entry, doc)
    for kind in _ICEBERG_VIEW_OWNERS:
        nodes = by_kind.get(kind, {})
        if not nodes:
            errors.append(f"iceberg_view:{kind}:missing")
            continue
        children: dict[str, list[str]] = defaultdict(list)
        roots: list[str] = []
        result_to_view = {entry.get("result_id", result_id): view_id for view_id, (entry, _doc) in nodes.items() for result_id in [entry.get("result_id", "")]}
        for view_id, (entry, doc) in nodes.items():
            view = doc["iceberg_view"]
            try:
                revision = int(view.get("revision"))
            except (TypeError, ValueError):
                errors.append(f"iceberg_view:{entry.get('result_id', doc.get('id'))}:revision")
                revision = -1
            predecessor_result = doc.get("supersedes")
            predecessor_view = view.get("supersedes_view_id")
            if predecessor_result is None and predecessor_view is None:
                roots.append(view_id)
                if revision != 1:
                    errors.append(f"iceberg_view:{view_id}:initial_revision")
                continue
            if not isinstance(predecessor_result, str) or not isinstance(predecessor_view, str):
                errors.append(f"iceberg_view:{view_id}:lineage_pair")
                continue
            expected_view = result_to_view.get(predecessor_result)
            parent = nodes.get(predecessor_view)
            if expected_view != predecessor_view or parent is None:
                errors.append(f"iceberg_view:{view_id}:lineage_binding")
                continue
            parent_entry = parent[0]
            predecessor_binding = {
                "source_kind": "ACCEPTED_RESULT",
                "source_id": predecessor_result,
                "source_sha256": parent_entry.get("content_hash"),
                "source_path": parent_entry.get("path"),
            }
            if predecessor_binding not in view.get("source_bindings", []):
                errors.append(f"iceberg_view:{view_id}:lineage_source_binding")
            try:
                parent_revision = int(parent[1]["iceberg_view"].get("revision"))
            except (TypeError, ValueError):
                parent_revision = -2
            if revision != parent_revision + 1:
                errors.append(f"iceberg_view:{view_id}:revision_gap")
            children[predecessor_view].append(view_id)
        if len(roots) != 1:
            errors.append(f"iceberg_view:{kind}:root_cardinality:{len(roots)}")
        for view_id, child_ids in children.items():
            if len(child_ids) > 1:
                errors.append(f"iceberg_view:{view_id}:branch")
        heads = sorted(set(nodes) - set(children))
        if len(heads) != 1:
            errors.append(f"iceberg_view:{kind}:head_cardinality:{len(heads)}")
            continue
        head_id = heads[0]
        entry, doc = nodes[head_id]
        result_id = str(entry.get("result_id", doc.get("id")))
        view = doc["iceberg_view"]
        if view.get("workflow_status") != "ACCEPTED":
            errors.append(f"iceberg_view:{result_id}:stale_head")
        projection[kind] = {
            "view_id": head_id,
            "view_kind": kind,
            "result_id": result_id,
            "result_sha256": entry.get("content_hash"),
            "revision": view.get("revision"),
        }
        historical.update(
            str(other_entry.get("result_id", other_doc.get("id")))
            for other_id, (other_entry, other_doc) in nodes.items()
            if other_id != head_id
        )
    return projection, historical, errors


def resolve_model_revision_scope(
    results: dict[str, Any], docs: dict[str, Any],
    plan_projection: dict[str, str] | None = None,
    current_final_audit_id: str | None = None,
    selected_synthesis_id: str | None = None,
    stale_result_ids: Iterable[str] = (), *,
    current_assignment_results: set[str] | None = None,
    phase: str = "final",
) -> dict[str, Any]:
    """Compute one active consumer slice while retaining immutable history.

    S-000's exact reference selects Synthesis. Before S-000, a revision head
    must be the result of a current research assignment (or explicitly supplied
    by a pure caller). A lone legacy initial result remains unambiguous.
    """
    errors: list[str] = []
    stale = set(stale_result_ids)
    snapshots = {entry["input_snapshot"] for entry in results.values()
                 if isinstance(entry, dict) and isinstance(entry.get("input_snapshot"), str)}
    iceberg, view_history, view_errors = current_iceberg_view_projection(
        results, docs, input_snapshot=next(iter(snapshots)) if len(snapshots) == 1 else "",
    ) if any(isinstance(doc, dict) and "iceberg_view" in doc for doc in docs.values()) else ({}, set(), [])
    errors.extend(view_errors)
    synthesis = {
        rid for rid, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "SYNTHESIS"
    }
    children: dict[str, list[str]] = defaultdict(list)
    roots: set[str] = set()
    for rid in sorted(synthesis):
        doc = docs.get(rid)
        prefix = f"model_scope:synthesis:{rid}"
        if not isinstance(doc, dict) or not _result_matches_doc(rid, results, docs):
            errors.append(prefix + ":accepted_binding")
            continue
        previous = doc.get("supersedes")
        if previous is None:
            roots.add(rid)
            if doc.get("revision") != "1":
                errors.append(prefix + ":initial_revision")
            continue
        if not isinstance(previous, str) or previous not in synthesis or previous == rid:
            errors.append(prefix + ":supersedes")
            continue
        parent = docs.get(previous)
        if (not isinstance(parent, dict)
                or results[rid].get("role_id") != results[previous].get("role_id")
                or doc.get("input_snapshot") != parent.get("input_snapshot")):
            errors.append(prefix + ":lineage_binding")
            continue
        try:
            valid_revision = (isinstance(doc.get("revision"), str)
                              and isinstance(parent.get("revision"), str)
                              and doc["revision"].isdecimal() and parent["revision"].isdecimal()
                              and int(doc["revision"]) == int(parent["revision"]) + 1)
        except (TypeError, ValueError):
            valid_revision = False
        if not valid_revision:
            errors.append(prefix + ":revision_gap")
        declared_hashes = doc.get("accepted_dependency_hashes")
        if isinstance(declared_hashes, dict) and previous in declared_hashes and declared_hashes[previous] != results[previous].get("content_hash"):
            errors.append(prefix + ":predecessor_hash")
        children[previous].append(rid)
    if synthesis:
        if len(roots) != 1:
            errors.append(f"model_scope:synthesis:root_cardinality:{len(roots)}")
        for rid, child_ids in sorted(children.items()):
            if len(child_ids) != 1:
                errors.append(f"model_scope:synthesis:{rid}:branch")
        if not _acyclic(synthesis, [(parent, child) for parent, rows in children.items() for child in rows]):
            errors.append("model_scope:synthesis:cycle")
    heads = synthesis - set(children)
    if synthesis and len(heads) != 1:
        errors.append(f"model_scope:synthesis:head_cardinality:{len(heads)}")

    plans = {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "PLAN"}
    current_plans = set(plan_projection) if isinstance(plan_projection, dict) else set(plans)
    root_ids: list[str] = []
    for rid in current_plans:
        doc = docs.get(rid)
        identity = doc.get("identity_result") if isinstance(doc, dict) else None
        logical = (plan_projection.get(rid) if isinstance(plan_projection, dict)
                   else identity.get("stage_id") if isinstance(identity, dict) else None)
        if logical == "S-000":
            root_ids.append(rid)
    if len(root_ids) > 1:
        errors.append("model_scope:ambiguous_current_S-000")
    root = docs.get(root_ids[0], {}) if len(root_ids) == 1 else {}
    reference = root.get("architecture_ref") if isinstance(root, dict) else None
    if reference is not None:
        if (not isinstance(reference, dict)
                or set(reference) != {"synthesis_id", "synthesis_sha256", "graph_sha256"}
                or not isinstance(reference.get("synthesis_id"), str)
                or reference["synthesis_id"] not in synthesis):
            errors.append("model_scope:synthesis:exact_reference")
            selected_synthesis_id = None
        else:
            rid = reference["synthesis_id"]
            if selected_synthesis_id is not None and selected_synthesis_id != rid:
                errors.append("model_scope:synthesis:conflicting_selection")
            selected_synthesis_id = rid
            from prep_validation import canonical_yaml_bytes
            graph = docs.get(rid, {}).get("architecture_graph")
            if reference.get("synthesis_sha256") != results[rid].get("content_hash"):
                errors.append("model_scope:synthesis:selected_hash")
            if not isinstance(graph, dict) or reference.get("graph_sha256") != hashlib.sha256(canonical_yaml_bytes(graph)).hexdigest():
                errors.append("model_scope:synthesis:selected_graph_hash")
    elif selected_synthesis_id is None:
        if len(synthesis) == 1:
            selected_synthesis_id = next(iter(synthesis))
        elif len(heads) == 1 and current_assignment_results is not None and next(iter(heads)) in current_assignment_results:
            selected_synthesis_id = next(iter(heads))
        elif synthesis:
            errors.append("model_scope:synthesis:explicit_current_assignment_required")
    if selected_synthesis_id is not None and not isinstance(selected_synthesis_id, str):
        errors.append("model_scope:synthesis:selected_result_binding")
        selected_synthesis_id = None
    if selected_synthesis_id is not None:
        if selected_synthesis_id not in synthesis:
            errors.append("model_scope:synthesis:selected_result_binding")
        elif selected_synthesis_id not in heads:
            errors.append("model_scope:synthesis:historical_promotion")
        if selected_synthesis_id in stale:
            errors.append("model_scope:synthesis:selected_stale")
        if (current_assignment_results is not None
                and selected_synthesis_id not in current_assignment_results):
            errors.append("model_scope:synthesis:current_assignment_binding")
    active_synthesis = {selected_synthesis_id} & synthesis if selected_synthesis_id else set()
    audits = {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "FINAL_AUDIT"}
    active_audit = {current_final_audit_id} & audits if current_final_audit_id else audits
    if phase == "core":
        # A persisted audit selector can still name the preceding release while
        # its successor core is being prepared. Retain only a demonstrably exact
        # old context; malformed or mixed bindings never justify a downgrade.
        # Final validation always keeps the selected audit current.
        for rid in sorted(active_audit):
            audit = docs.get(rid)
            if not isinstance(audit, dict):
                continue
            own_errors: list[str] = []
            own_projection = _iceberg_consumer_projection(
                rid, audit.get("iceberg_view_bindings"), results, docs,
                iceberg, set(view_history) | audits, own_errors,
            )
            reference = audit.get("architecture_ref")
            sid = reference.get("synthesis_id") if isinstance(reference, dict) else None
            retained = False
            if isinstance(sid, str) and sid in synthesis - active_synthesis:
                from prep_validation import canonical_yaml_bytes
                synthesis_doc = docs.get(sid)
                graph = synthesis_doc.get("architecture_graph") if isinstance(synthesis_doc, dict) else None
                exact_reference = (
                    set(reference) == {"synthesis_id", "synthesis_sha256", "graph_sha256"}
                    and reference.get("synthesis_sha256") == results[sid].get("content_hash")
                    and isinstance(graph, dict)
                    and reference.get("graph_sha256") == hashlib.sha256(canonical_yaml_bytes(graph)).hexdigest()
                )
                retained = (exact_reference and not own_errors
                            and own_projection == _binding_map(synthesis_doc.get("iceberg_view_bindings")))
                if not retained:
                    errors.append(f"model_scope:audit:{rid}:historical_context_binding")
            elif reference is None and not any(
                isinstance(docs.get(item), dict) and isinstance(docs[item].get("architecture_graph"), dict)
                for item in synthesis
            ):
                # Iceberg-only legacy runs have no architecture reference.
                retained = (not own_errors and own_projection != iceberg
                            and any(binding["result_id"] in view_history
                                    for binding in own_projection.values()))
            if retained:
                active_audit = active_audit - {rid}
    history = set(view_history) | (synthesis - active_synthesis) | (plans - current_plans) | (audits - active_audit)
    # A review's exact target may be retained history while current execution
    # still uses the review. First identify unused retained reviews, then close
    # over explicit current review dependencies. Reusing a review never promotes
    # its old PLAN/Synthesis target into the selected consumer revision.
    reviews = {rid for rid, entry in results.items()
               if isinstance(entry, dict) and entry.get("artifact_type") == "REVIEW"}
    while True:
        retained_reviews = {
            rid for rid, entry in results.items()
            if isinstance(entry, dict) and entry.get("artifact_type") == "REVIEW"
            and isinstance(docs.get(rid), dict)
            and isinstance(docs[rid].get("target_result_id"), str)
            and docs[rid]["target_result_id"] in history
        }
        if retained_reviews.issubset(history):
            break
        history.update(retained_reviews)
    current_reviews = reviews - history
    pending = list(set(results) - history)
    visited: set[str] = set()
    while pending:
        rid = pending.pop()
        if rid in visited:
            continue
        visited.add(rid)
        doc = docs.get(rid)
        if not isinstance(doc, dict):
            continue
        referenced = set(_ids(doc.get("source_refs"))) & reviews
        declared = doc.get("accepted_dependency_hashes")
        if isinstance(declared, dict):
            referenced.update(set(declared) & reviews)
        if rid in reviews and isinstance(doc.get("target_result_id"), str):
            referenced.update({doc["target_result_id"]} & reviews)
        for field in ("execution_edges", "semantic_edges"):
            edges = doc.get(field)
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                for side in ("from", "to"):
                    identifier = edge.get(side + "_id")
                    if edge.get(side + "_type") == "REVIEW" and isinstance(identifier, str) and identifier in reviews:
                        referenced.add(identifier)
        current_reviews.update(referenced)
        pending.extend(referenced - visited)
    for rid in sorted(current_reviews & stale):
        errors.append(f"model_scope:review:{rid}:current_stale")
    history.difference_update(current_reviews)
    return {"active_result_ids": set(results) - history,
            "historical_result_ids": history,
            "active_synthesis_ids": active_synthesis,
            "active_review_ids": current_reviews,
            "current_iceberg_views": iceberg,
            "errors": sorted(set(errors))}


def model_revision_scope_for_run(
    run: Path, results: dict[str, Any], docs: dict[str, Any],
    plan_projection: dict[str, str] | None, current_final_audit_id: str | None,
    load_yaml: Callable[[Path], Any], sha: Callable[[bytes], str],
    *, phase: str = "final",
) -> dict[str, Any]:
    current = _load_mapping(run / "state/current.yaml", load_yaml) or {}
    assignments = _load_mapping(run / "state/assignments.yaml", load_yaml)
    assigned: set[str] | None = None
    if isinstance(assignments, dict) and ("registered" in assignments or "current_attempts" in assignments):
        assigned = set()
        registered = assignments.get("registered", {})
        attempts = assignments.get("current_attempts", {})
        if isinstance(registered, dict) and isinstance(attempts, dict):
            for rid, entry in results.items():
                if not isinstance(entry, dict) or entry.get("artifact_type") != "SYNTHESIS":
                    continue
                aid, attempt = entry.get("assignment_id"), entry.get("attempt_id")
                record = registered.get(str(aid) + "\0" + str(attempt))
                assignment = record.get("assignment") if isinstance(record, dict) else None
                if (isinstance(assignment, dict) and record.get("status") == "ACCEPTED"
                        and attempts.get(aid) == attempt
                        and assignment.get("result_id") == rid
                        and all(assignment.get(field) == entry.get(field) for field in ("assignment_id", "attempt_id", "role_id", "agent_id", "artifact_type", "input_snapshot"))):
                    assigned.add(rid)
    scope = resolve_model_revision_scope(
        results, docs, plan_projection, current_final_audit_id,
        stale_result_ids=_ids(current.get("stale_result_ids")),
        current_assignment_results=assigned,
        phase=phase,
    )
    # Every retained consumer and map remains hash checked, including records
    # omitted from current graph coverage. An explicit pure-API projection may
    # supply current PLAN docs before persistence (including a predecessor's
    # path); that is not a disk receipt. Public qs.accepted() independently
    # rehashes every registry file before calling this model layer.
    for rid, entry in results.items():
        if (isinstance(entry, dict)
                and (entry.get("artifact_type") in {"SYNTHESIS", "PLAN", "FINAL_AUDIT"}
                     or isinstance(docs.get(rid), dict) and "iceberg_view" in docs[rid])):
            projected_current_plan = (
                entry.get("artifact_type") == "PLAN"
                and isinstance(plan_projection, dict) and rid in plan_projection
                and rid not in scope["historical_result_ids"]
            )
            if not projected_current_plan and not _result_file_matches(run, rid, results, sha):
                scope["errors"].append(f"model_scope:{rid}:immutable_result_hash")
            path = _entry_path(run, entry)
            if (rid in scope["historical_result_ids"]
                    and (path is None or _load_mapping(path, load_yaml) != docs.get(rid))):
                scope["errors"].append(f"model_scope:{rid}:document_bytes_binding")
    scope["errors"] = sorted(set(scope["errors"]))
    return scope


def _prospective_execution_endpoints(doc: dict[str, Any]) -> dict[str, set[str]]:
    """Resolve typed endpoints in a retained Synthesis admitted before PLAN."""
    from architecture_validation import SCHEMA_VERSION

    graph = doc.get("architecture_graph")
    if (not isinstance(graph, dict) or graph.get("schema_version") != SCHEMA_VERSION
            or not _text(graph.get("id")) or not _text(graph.get("revision"))):
        return {}
    selected = graph.get("selected_mechanism_id")
    mechanisms, nodes = graph.get("mechanisms"), graph.get("nodes")
    if (not _text(selected) or doc.get("chosen_mechanism_id") != selected
            or not isinstance(mechanisms, list) or not isinstance(nodes, list)):
        return {}
    if [item.get("id") for item in mechanisms
            if isinstance(item, dict) and item.get("status") == "SELECTED"] != [selected]:
        return {}
    if sum(isinstance(item, dict) and item.get("id") == selected for item in mechanisms) != 1:
        return {}
    if any(not isinstance(node, dict) or not _text(node.get("id")) for node in nodes):
        return {}
    if len({node["id"] for node in nodes}) != len(nodes):
        return {}
    own = {node["id"]: node for node in nodes if node.get("mechanism_id") == selected}
    stages = {rid for rid, node in own.items()
              if node.get("kind") == "S" and node.get("existing_id", rid) == rid}
    tasks = {rid for rid, node in own.items() if node.get("kind") == "T"
             and node.get("existing_id", rid) == rid
             and _text(node.get("stage_id")) and node["stage_id"] in stages}
    checks = {rid for rid, node in own.items() if node.get("kind") == "C"
              and node.get("existing_id", rid) == rid
              and _text(node.get("stage_id")) and node["stage_id"] in stages
              and _text(node.get("task_id")) and node["task_id"] in tasks
              and own[node["task_id"]].get("stage_id") == node["stage_id"]}
    return {"T": tasks, "C": checks}


def _plan_execution_endpoints(plans: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {
        kind: {row["id"] for plan in plans for row in plan.get(field, [])
               if isinstance(row, dict) and _text(row.get("id"))}
        for kind, field in (("T", "tasks"), ("C", "acceptance_exit"))
    }


def _synthesis_execution_endpoints(
    synthesis_id: str, results: dict[str, Any], docs: dict[str, Any], sha: Callable[[bytes], str],
) -> dict[str, set[str]]:
    """Resolve only the selected Synthesis graph and its exactly bound PLANs."""
    from prep_validation import canonical_yaml_bytes

    synthesis = docs.get(synthesis_id, {})
    entry = results.get(synthesis_id, {})
    graph = synthesis.get("architecture_graph") if isinstance(synthesis, dict) else None
    if entry.get("role_id") != "OP-SYNTHESIS" or not isinstance(graph, dict):
        return {"T": set(), "C": set()}
    reference = {"synthesis_id": synthesis_id, "synthesis_sha256": entry.get("content_hash"),
                 "graph_sha256": sha(canonical_yaml_bytes(graph))}
    plans = [doc for rid, doc in docs.items() if isinstance(doc, dict)
             and results.get(rid, {}).get("artifact_type") == "PLAN" and doc.get("architecture_ref") == reference]
    declared = _prospective_execution_endpoints(synthesis)
    if not plans:
        return {kind: declared.get(kind, set()) for kind in ("T", "C")}
    accepted = _plan_execution_endpoints(plans)
    return {kind: accepted[kind] & declared.get(kind, set()) for kind in ("T", "C")}


def _validate_historical_consumer_links(
    run: Path, bank: dict[str, Any], results: dict[str, Any], docs: dict[str, Any],
    historical: set[str], sha: Callable[[bytes], str], errors: list[str],
) -> None:
    """Check retained consumers' own references without current coverage rules."""
    plans = {rid: doc for rid, doc in docs.items()
             if isinstance(doc, dict) and results.get(rid, {}).get("artifact_type") == "PLAN"}
    base_endpoints = {
        "E": {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "EVIDENCE"},
        "Q": _question_ids(bank, docs),
        "D": {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "DECISION"},
        "H": {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "HYPOTHESIS"},
        "REVIEW": {rid for rid, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "REVIEW"},
    }
    for rid in sorted(historical):
        doc = docs.get(rid)
        entry = results.get(rid)
        if (not isinstance(doc, dict) or not isinstance(entry, dict)
                or entry.get("artifact_type") not in {"SYNTHESIS", "PLAN", "FINAL_AUDIT"}):
            continue
        prefix = f"historical:{rid}"
        hashes = doc.get("accepted_dependency_hashes", {})
        if not isinstance(hashes, dict):
            errors.append(prefix + ":accepted_dependency_hashes")
        else:
            for dependency, expected_hash in hashes.items():
                if (not isinstance(dependency, str) or not _hash(expected_hash)
                        or results.get(dependency, {}).get("content_hash") != expected_hash):
                    errors.append(prefix + f":dependency_hash:{dependency}")
        reference = doc.get("architecture_ref")
        own_synthesis_id = rid if entry.get("artifact_type") == "SYNTHESIS" else None
        if reference is not None:
            _graph, ref_errors = _architecture_reference(run, reference, results, docs, sha)
            errors.extend(prefix + ":" + error for error in ref_errors)
            own_synthesis_id = reference.get("synthesis_id") if isinstance(reference, dict) and isinstance(reference.get("synthesis_id"), str) else None
            synthesis = docs.get(own_synthesis_id)
            trace = doc.get("iceberg_trace")
            own_bindings = ((trace.get("view_bindings") if isinstance(trace, dict) else None)
                            if entry.get("artifact_type") == "PLAN"
                            else doc.get("iceberg_view_bindings"))
            if (isinstance(synthesis, dict) and own_bindings is not None
                    and _binding_map(own_bindings) != _binding_map(synthesis.get("iceberg_view_bindings"))):
                errors.append(prefix + ":synthesis_view_binding")
        if own_synthesis_id is not None:
            execution = _synthesis_execution_endpoints(own_synthesis_id, results, docs, sha)
        else:
            own_plans = [value for value in plans.values() if reference is None
                         and entry["artifact_type"] == "PLAN" and value.get("plan_version") == doc.get("plan_version")]
            execution = _plan_execution_endpoints(own_plans)
        endpoints = {**base_endpoints, **execution}
        pairs: list[tuple[str, str]] = []
        edges = doc.get("execution_edges", [])
        if not isinstance(edges, list):
            errors.append(prefix + ":execution_edges_contract")
            edges = []
        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(prefix + f":execution_edge:{index}:contract")
                continue
            ids = []
            for side in ("from", "to"):
                kind, identifier = edge.get(side + "_type"), edge.get(side + "_id")
                if (not isinstance(kind, str) or kind not in endpoints
                        or not isinstance(identifier, str) or identifier not in endpoints[kind]):
                    errors.append(prefix + f":execution_edge:{index}:{side}_binding")
                else:
                    ids.append(kind + ":" + identifier)
            if len(ids) == 2:
                pairs.append((ids[0], ids[1]))
        if pairs and not _acyclic({value for pair in pairs for value in pair}, pairs):
            errors.append(prefix + ":execution_graph_cycle")
        provenance = doc.get("evidence_provenance_edges", [])
        if not isinstance(provenance, list):
            errors.append(prefix + ":evidence_provenance_edges_contract")
            provenance = []
        provenance_pairs: list[tuple[str, str]] = []
        for index, edge in enumerate(provenance):
            if (not isinstance(edge, dict) or not isinstance(edge.get("from_id"), str)
                    or not isinstance(edge.get("to_id"), str)
                    or edge["from_id"] not in base_endpoints["E"]
                    or edge["to_id"] not in base_endpoints["E"]):
                errors.append(prefix + f":evidence_provenance_edge:{index}:binding")
            else:
                provenance_pairs.append((edge["from_id"], edge["to_id"]))
        if provenance_pairs and not _acyclic(base_endpoints["E"], provenance_pairs):
            errors.append(prefix + ":evidence_provenance_cycle")


def _active_iceberg_semantic_docs(
    docs: dict[str, Any], historical: set[str]
) -> dict[str, Any]:
    """Omit retained consumer revisions from the computed active claim slice."""
    historical_views = set(historical)
    return {
        result_id: doc
        for result_id, doc in docs.items()
        if result_id not in historical_views
    }


def _validate_iceberg_edges(
    docs: dict[str, Any],
    results: dict[str, Any],
    valid_endpoints: dict[str, set[str]],
    question_ids: set[str],
    check_ids: set[str],
    errors: list[str],
    *,
    unconsumed_historical_view: str | None = None,
    diagnostics: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    edges: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_ids = valid_endpoints.get("E", set())
    for result_id, doc in docs.items():
        value = doc.get("semantic_edges") if isinstance(doc, dict) else None
        if not isinstance(value, list):
            continue
        allow_residue = unconsumed_historical_view is not None and result_id == unconsumed_historical_view
        if allow_residue:
            declared = set(_ids(doc["iceberg_view"].get("semantic_edge_ids")))
            id_counts = Counter(
                row["id"] for row in value
                if isinstance(row, dict) and isinstance(row.get("id"), str)
            )
        for index, edge in enumerate(value):
            prefix = f"iceberg_edge:index-{index}"
            required = {
                "id", "relation", "from_type", "from_id", "to_type", "to_id",
                "scope", "combination_kind", "knowledge_status", "evidence_ids",
                "distinguishing_check_id",
            }
            if not isinstance(edge, dict) or not required.issubset(edge):
                if allow_residue and isinstance(edge, dict) and edge.keys() == {"id"}:
                    edge_id = edge["id"]
                    if (isinstance(edge_id, str) and edge_id.strip()
                            and edge_id not in declared and id_counts[edge_id] == 1):
                        if diagnostics is not None:
                            diagnostics.append(
                                f"iceberg_view:{result_id}:semantic_edges:{index}:{edge_id}:undeclared_id_only"
                            )
                        continue
                errors.append(prefix + ":contract")
                continue
            edge_id = edge.get("id")
            prefix = f"iceberg_edge:{edge_id}"
            if not isinstance(edge_id, str) or not edge_id or edge_id in edges:
                errors.append(prefix + ":id")
                continue
            edges[edge_id] = edge
            relation = edge.get("relation")
            if relation not in _ICEBERG_RELATIONS:
                errors.append(prefix + ":relation")
            source_type, target_type = edge.get("from_type"), edge.get("to_type")
            if source_type not in valid_endpoints or edge.get("from_id") not in valid_endpoints.get(source_type, set()):
                errors.append(prefix + ":from_binding")
            if target_type not in valid_endpoints or edge.get("to_id") not in valid_endpoints.get(target_type, set()):
                errors.append(prefix + ":to_binding")
            if not _text(edge.get("scope")) or edge.get("knowledge_status") not in _ICEBERG_KNOWLEDGE:
                errors.append(prefix + ":scope_or_knowledge")
            bound_evidence = edge.get("evidence_ids")
            if not _strings(bound_evidence) or any(item not in evidence_ids for item in _ids(bound_evidence)):
                errors.append(prefix + ":evidence_binding")
                bound_evidence = []
            check_id = edge.get("distinguishing_check_id")
            if not isinstance(check_id, str) or check_id not in question_ids | check_ids:
                errors.append(prefix + ":distinguishing_check_binding")
            combination = edge.get("combination_kind")
            if combination not in _ICEBERG_COMBINATIONS:
                errors.append(prefix + ":combination_kind")
            if combination == "SINGLE":
                if "combination_id" in edge or "sequence_index" in edge:
                    errors.append(prefix + ":single_combination_fields")
            else:
                group_id = edge.get("combination_id")
                if not isinstance(group_id, str) or not group_id:
                    errors.append(prefix + ":combination_id")
                else:
                    groups[group_id].append(edge)
                if combination == "SEQUENCE":
                    sequence = edge.get("sequence_index")
                    if not isinstance(sequence, str) or not sequence.isdecimal() or int(sequence) < 1:
                        errors.append(prefix + ":sequence_index")
                elif "sequence_index" in edge:
                    errors.append(prefix + ":sequence_index_not_applicable")
            confirmation = edge.get("causal_confirmation")
            if relation == "CAUSES_HYPOTHESIS":
                if edge.get("knowledge_status") not in {"HYPOTHESIS", "UNKNOWN"}:
                    errors.append(prefix + ":causal_hypothesis_status")
                if confirmation is not None:
                    errors.append(prefix + ":causal_confirmation_not_applicable")
            elif relation == "CAUSAL_INFLUENCE":
                required_confirmation = {
                    "intervention_evidence_ids", "temporal_order_evidence_ids",
                    "mechanism_evidence_ids", "competing_explanation_evidence_ids",
                }
                if edge.get("knowledge_status") != "DERIVED" or not isinstance(confirmation, dict) or set(confirmation) != required_confirmation:
                    errors.append(prefix + ":causal_confirmation")
                else:
                    for field in sorted(required_confirmation):
                        ids = confirmation.get(field)
                        if (
                            not _strings(ids, nonempty=True)
                            or len(ids) != len(set(ids))
                            or not set(ids).issubset(set(bound_evidence))
                            or not set(ids).issubset(evidence_ids)
                        ):
                            errors.append(prefix + f":causal_confirmation:{field}")
            elif confirmation is not None:
                errors.append(prefix + ":causal_confirmation_not_applicable")
    for group_id, group in sorted(groups.items()):
        kinds = {edge.get("combination_kind") for edge in group}
        if len(group) < 2 or len(kinds) != 1:
            errors.append(f"iceberg_combination:{group_id}:group_contract")
            continue
        if kinds == {"SEQUENCE"}:
            indexes = [int(edge["sequence_index"]) for edge in group if isinstance(edge.get("sequence_index"), str) and edge["sequence_index"].isdecimal()]
            if sorted(indexes) != list(range(1, len(group) + 1)):
                errors.append(f"iceberg_combination:{group_id}:sequence")
    return edges


def _iceberg_source_binding(
    binding: Any,
    run: Path,
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    load_yaml: Callable[[Path], Any],
    prefix: str,
    errors: list[str],
) -> Any:
    required = {"source_kind", "source_id", "source_sha256", "source_path"}
    if not isinstance(binding, dict) or set(binding) != required:
        errors.append(prefix + ":source_binding")
        return None
    source_id = binding.get("source_id")
    source_path = binding.get("source_path")
    if binding.get("source_kind") == "ACCEPTED_RESULT":
        entry = results.get(source_id)
        if (
            not isinstance(entry, dict)
            or entry.get("content_hash") != binding.get("source_sha256")
            or entry.get("path") != source_path
            or not _result_matches_doc(str(source_id), results, docs)
        ):
            errors.append(prefix + ":accepted_result_binding")
            return None
        return docs.get(source_id)
    if binding.get("source_kind") == "IMMUTABLE_INPUT":
        if not isinstance(source_path, str) or immutable_inputs.get(source_path) != binding.get("source_sha256"):
            errors.append(prefix + ":immutable_input_binding")
            return None
        return _load_mapping(run / source_path, load_yaml)
    errors.append(prefix + ":source_kind")
    return None


def _validate_view_contents(
    run: Path,
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    load_yaml: Callable[[Path], Any],
    anchor: dict[str, Any],
    projection: dict[str, dict[str, Any]],
    origin_index: dict[tuple[str, str], dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    question_ids: set[str],
    errors: list[str],
) -> bool:
    unavailable_dependency = False
    valid_refs = {
        "G": {anchor["vector_id"]},
        "R": _requirement_ids(_load_mapping(run / "input" / "bank.yaml", load_yaml)),
        "Q": question_ids,
        "E": {item for item, entry in results.items() if entry.get("artifact_type") == "EVIDENCE"},
        "D": {item for item, entry in results.items() if entry.get("artifact_type") == "DECISION"},
        "H": {item for item, entry in results.items() if entry.get("artifact_type") == "HYPOTHESIS"},
    }
    expected_anchor = {key: anchor[key] for key in (
        "anchor_kind", "vector_id", "vector_sha256", "revision",
        "approval_binding_id", "approval_sha256", "applicability_result_id",
        "goal_claim_path",
    )}
    for binding in projection.values():
        result_id = binding["result_id"]
        view = docs[result_id]["iceberg_view"]
        prefix = f"iceberg_view:{result_id}"
        required = {
            "view_id", "view_kind", "revision", "input_snapshot",
            "normative_vector_binding", "source_bindings", "members",
            "semantic_edge_ids", "open_q_ids", "coverage_limit",
            "knowledge_status", "workflow_status",
        }
        if not required.issubset(view):
            errors.append(prefix + ":contract")
        if view.get("normative_vector_binding") != expected_anchor:
            errors.append(prefix + ":normative_vector_binding")
        source_bindings = view.get("source_bindings")
        if not isinstance(source_bindings, list) or not source_bindings:
            errors.append(prefix + ":source_bindings")
            source_bindings = []
        canonical_sources: list[dict[str, Any]] = []
        for index, source_binding in enumerate(source_bindings):
            if _iceberg_source_binding(
                source_binding, run, results, docs, immutable_inputs, load_yaml,
                prefix + f":source:{index}", errors,
            ) is not None and isinstance(source_binding, dict):
                canonical_sources.append(source_binding)
        members = view.get("members")
        if not isinstance(members, list) or not members:
            errors.append(prefix + ":members")
            members = []
        for index, member in enumerate(members):
            member_prefix = prefix + f":member:{index}"
            required_member = {
                "element_kind", "ref_type", "ref_id", "canonical_claim_path",
                "source_binding", "origin_position_refs", "materiality", "scope",
            }
            if not isinstance(member, dict) or not required_member.issubset(member):
                errors.append(member_prefix + ":contract")
                continue
            ref_type, ref_id = member.get("ref_type"), member.get("ref_id")
            if member.get("element_kind") not in _ICEBERG_ELEMENT_KINDS:
                errors.append(member_prefix + ":element_kind")
            if ref_type not in valid_refs or ref_id not in valid_refs.get(ref_type, set()):
                errors.append(member_prefix + ":ref_binding")
            if member.get("source_binding") not in canonical_sources:
                errors.append(member_prefix + ":source_not_declared")
            source_doc = _iceberg_source_binding(
                member.get("source_binding"), run, results, docs, immutable_inputs,
                load_yaml, member_prefix, errors,
            )
            if source_doc is not None and _json_pointer(source_doc, member.get("canonical_claim_path")) is _json_pointer:
                errors.append(member_prefix + ":canonical_claim_path")
            if any(not _text(member.get(field)) for field in ("materiality", "scope")):
                errors.append(member_prefix + ":substance")
            roles, unavailable = _validate_origin_refs(
                member.get("origin_position_refs"), origin_index, member_prefix, errors
            )
            unavailable_dependency = unavailable_dependency or unavailable
            referenced_h = docs.get(ref_id) if ref_type == "H" else None
            shared_h = isinstance(referenced_h, dict) and len({
                item for item in _ids(referenced_h.get("source_refs"))
                if results.get(item, {}).get("artifact_type") == "FRAMING"
            }) >= 2
            if shared_h and len(roles) < 2:
                errors.append(member_prefix + ":shared_h_requires_two_roles")
        edge_ids = view.get("semantic_edge_ids")
        if not _strings(edge_ids, nonempty=True) or any(item not in edges for item in _ids(edge_ids)):
            errors.append(prefix + ":semantic_edge_ids")
        if not _strings(view.get("open_q_ids")) or any(item not in question_ids for item in _ids(view.get("open_q_ids"))):
            errors.append(prefix + ":open_q_ids")
        if not _text(view.get("coverage_limit")) or view.get("knowledge_status") not in _ICEBERG_KNOWLEDGE or view.get("workflow_status") != "ACCEPTED":
            errors.append(prefix + ":status_or_coverage")
    return unavailable_dependency


def _binding_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    answer: dict[str, dict[str, Any]] = {}
    required = {"view_id", "view_kind", "result_id", "result_sha256", "revision"}
    for binding in value:
        if isinstance(binding, dict) and set(binding) == required and isinstance(binding.get("view_kind"), str):
            answer[binding["view_kind"]] = binding
    return answer


def current_iceberg_consumer_result_ids(docs: dict[str, Any], stale: set[str]) -> set[str]:
    """Return view result IDs selected by the supplied non-stale consumers."""
    selected: set[str] = set()
    for result_id, doc in docs.items():
        if result_id in stale or not isinstance(doc, dict):
            continue
        bindings = [doc.get("iceberg_view_bindings"), doc.get("view_bindings")]
        trace = doc.get("iceberg_trace")
        if isinstance(trace, dict):
            bindings.append(trace.get("view_bindings"))
        for value in bindings:
            if isinstance(value, dict):
                value = list(value.values())
            if isinstance(value, list):
                selected.update(
                    binding["result_id"] for binding in value
                    if isinstance(binding, dict) and isinstance(binding.get("result_id"), str)
                )
    return selected


def _iceberg_view_semantic_sources(docs: dict[str, Any]) -> set[str]:
    """Find accepted sources selected for view contents apart from lineage."""
    sources: set[str] = set()
    for doc in docs.values():
        view = doc.get("iceberg_view") if isinstance(doc, dict) else None
        if not isinstance(view, dict):
            continue
        declared = view.get("source_bindings")
        bindings = [
            binding for binding in declared
            if isinstance(binding, dict) and binding.get("source_id") != doc.get("supersedes")
        ] if isinstance(declared, list) else []
        members = view.get("members")
        if isinstance(members, list):
            bindings.extend(member.get("source_binding") for member in members if isinstance(member, dict))
        sources.update(
            binding["source_id"] for binding in bindings
            if isinstance(binding, dict) and binding.get("source_kind") == "ACCEPTED_RESULT"
            and isinstance(binding.get("source_id"), str)
        )
    return sources


def _iceberg_consumer_projection(
    result_id: str, bindings: Any, results: dict[str, Any], docs: dict[str, Any],
    current: dict[str, dict[str, Any]], historical: set[str], errors: list[str],
) -> dict[str, dict[str, Any]]:
    """Bind a retained consumer to its own exact map, never a future map."""
    projection = _binding_map(bindings)
    if (not isinstance(bindings, list) or len(bindings) != len(projection)
            or set(projection) != set(_ICEBERG_VIEW_OWNERS)):
        errors.append(f"iceberg_consumer:{result_id}:view_bindings_contract")
        return {}
    for kind, binding in projection.items():
        rid = binding.get("result_id")
        entry = results.get(rid) if isinstance(rid, str) else None
        doc = docs.get(rid) if isinstance(rid, str) else None
        view = doc.get("iceberg_view") if isinstance(doc, dict) else None
        expected = ({"view_id": view.get("view_id"), "view_kind": kind,
                     "result_id": rid, "result_sha256": entry.get("content_hash"),
                     "revision": view.get("revision")}
                    if isinstance(view, dict) and isinstance(entry, dict) else None)
        if (expected is None or binding != expected or view.get("view_kind") != kind
                or entry.get("artifact_type") != "OPERATOR"
                or entry.get("role_id") != _ICEBERG_VIEW_OWNERS[kind]):
            errors.append(f"iceberg_consumer:{result_id}:view_binding:{kind}")
    if result_id not in historical and projection != current:
        errors.append(f"iceberg_consumer:{result_id}:current_view_bindings")
    return projection


def _validate_iceberg_consumers(
    results: dict[str, Any],
    docs: dict[str, Any],
    projection: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
    unavailable_dependency: bool,
    phase: str,
    errors: list[str],
    historical: set[str] | None = None,
    historical_edges: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> None:
    historical = historical or set()
    historical_edges = historical_edges or {}
    synthesis_ids = [
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "SYNTHESIS"
    ]
    for result_id in synthesis_ids:
        synthesis = docs.get(result_id, {})
        prefix = f"iceberg_synthesis:{result_id}"
        binding_error_count = len(errors)
        own_projection = _iceberg_consumer_projection(
            result_id, synthesis.get("iceberg_view_bindings"), results, docs,
            projection, historical, errors,
        )
        if result_id not in historical and own_projection != projection:
            errors.append(prefix + ":view_bindings")
        member_refs = {
            str(binding.get("view_id")) + "#" + str(member.get("ref_id"))
            for binding in own_projection.values() if len(errors) == binding_error_count
            for view in [docs.get(binding.get("result_id"), {}).get("iceberg_view", {})]
            for member in view.get("members", [])
            if isinstance(member, dict)
        }
        own_edges = historical_edges.get(result_id, {}) if result_id in historical else edges
        trace = synthesis.get("iceberg_mechanism_trace")
        if not isinstance(trace, dict) or not isinstance(trace.get("chosen"), dict) or not isinstance(trace.get("alternatives"), list):
            errors.append(prefix + ":mechanism_trace")
            continue
        rows = [trace["chosen"]] + [row for row in trace["alternatives"] if isinstance(row, dict)]
        if len(rows) != len(trace["alternatives"]) + 1:
            errors.append(prefix + ":alternative_trace_contract")
        alternative_ids = {
            str(row.get("id")) for row in synthesis.get("alternatives", []) if isinstance(row, dict)
        }
        traced_alternatives = {
            str(row.get("alternative_id")) for row in trace["alternatives"] if isinstance(row, dict)
        }
        if alternative_ids != traced_alternatives:
            errors.append(prefix + ":alternative_trace_coverage")
        for index, row in enumerate(rows):
            row_prefix = prefix + f":trace:{index}"
            if any(not _strings(row.get(field), nonempty=True) for field in ("member_refs", "edge_ids", "e_refs", "d_refs")):
                errors.append(row_prefix + ":contract")
                continue
            if any(item not in member_refs for item in row["member_refs"]):
                errors.append(row_prefix + ":member_refs")
            if any(item not in own_edges for item in row["edge_ids"]):
                errors.append(row_prefix + ":edge_ids")
            for field, artifact_type in (("e_refs", "EVIDENCE"), ("d_refs", "DECISION")):
                if any(results.get(item, {}).get("artifact_type") != artifact_type for item in row[field]):
                    errors.append(row_prefix + f":{field}")

    for result_id, entry in results.items():
        if not isinstance(entry, dict) or entry.get("artifact_type") != "PLAN":
            continue
        trace = docs.get(result_id, {}).get("iceberg_trace")
        prefix = f"iceberg_plan:{result_id}"
        required = {
            "view_bindings", "g_refs", "r_refs", "capability_or_unknown_refs",
            "q_refs", "e_refs", "d_refs", "stage_refs", "task_refs",
            "check_refs", "useful_result",
        }
        if not isinstance(trace, dict) or not required.issubset(trace):
            errors.append(prefix + ":trace")
            continue
        own_projection = _iceberg_consumer_projection(
            result_id, trace.get("view_bindings"), results, docs,
            projection, historical, errors,
        )
        if result_id not in historical and own_projection != projection:
            errors.append(prefix + ":view_bindings")
        if any(not _strings(trace.get(field), nonempty=True) for field in required - {"view_bindings", "useful_result"}):
            errors.append(prefix + ":trace_refs")
        if not _text(trace.get("useful_result")):
            errors.append(prefix + ":useful_result")

    audit_ids = [
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "FINAL_AUDIT"
    ]
    required_probes = {
        "missing_or_shared_assumption", "false_necessity_or_causality",
        "impossible_capability", "missed_failure_combination", "stronger_bypass",
    }
    for result_id in audit_ids:
        audit = docs.get(result_id, {})
        prefix = f"iceberg_audit:{result_id}"
        own_projection = _iceberg_consumer_projection(
            result_id, audit.get("iceberg_view_bindings"), results, docs,
            projection, historical, errors,
        )
        if result_id not in historical and own_projection != projection:
            errors.append(prefix + ":view_bindings")
        probes = audit.get("iceberg_probes")
        if not isinstance(probes, dict) or set(probes) != required_probes:
            errors.append(prefix + ":probes")
        else:
            for probe_id, probe in probes.items():
                if (
                    not isinstance(probe, dict)
                    or not all(_text(probe.get(field)) for field in ("procedure", "result"))
                    or not _strings(probe.get("evidence_refs"), nonempty=True)
                    or not _strings(probe.get("affected_ids"), nonempty=True)
                    or any(results.get(item, {}).get("artifact_type") != "EVIDENCE" for item in probe["evidence_refs"])
                ):
                    errors.append(prefix + f":probe:{probe_id}")
        if result_id not in historical and unavailable_dependency and audit.get("verdict") == "READY":
            errors.append(prefix + ":ready_with_unavailable_dependent_check")


def validate_double_iceberg(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    phase: str,
    load_yaml: Callable[[Path], Any],
    sha: Callable[[bytes], str],
    *,
    revision_scope: dict[str, Any] | None = None,
    diagnostics: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    """Validate the marked feature and return exact current-head projection.

    An optional diagnostics list receives addresses of ignored historical rows.
    """
    run = Path(run)
    errors: list[str] = []
    snapshot = _load_mapping(run / "input" / "snapshot.yaml", load_yaml)
    if not double_iceberg_enabled(snapshot):
        return {}, set(), []
    snapshots = {
        entry.get("input_snapshot") for entry in results.values()
        if isinstance(entry, dict) and isinstance(entry.get("input_snapshot"), str)
    }
    input_snapshot = next(iter(snapshots)) if len(snapshots) == 1 else ""
    immutable_inputs = _immutable_input_hashes(run, bank, sha)
    anchor, _gate_objects = _resolve_vector_anchor(
        run, results, docs, immutable_inputs, load_yaml, errors
    )
    if anchor is None:
        errors.append("double_iceberg:vector_anchor")
        return {}, set(), errors
    framing_ids = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "FRAMING"
        and entry.get("role_id") in {f"P-{number:02d}" for number in range(1, 11)}
    }
    if len(framing_ids) != 10:
        errors.append(f"double_iceberg:framing_coverage:{len(framing_ids)}")
    for result_id in sorted(framing_ids):
        errors.extend(validate_blind_iceberg_position(
            result_id,
            docs.get(result_id, {}),
            input_snapshot=input_snapshot,
            vector_id=str(anchor["vector_id"]),
            vector_sha256=str(anchor["vector_sha256"]),
        ))
    origin_index = _origin_ref_index(results, docs)
    hypothesis_ids = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "HYPOTHESIS"
    }
    for result_id in sorted(hypothesis_ids):
        hypothesis = docs.get(result_id, {})
        prefix = f"iceberg_hypothesis:{result_id}"
        if any(not _text(hypothesis.get(field)) for field in ("claim", "scope", "materiality")):
            errors.append(prefix + ":substance")
        roles, _unavailable = _validate_origin_refs(
            hypothesis.get("origin_position_refs"), origin_index, prefix, errors
        )
        shared_sources = {
            item for item in _ids(hypothesis.get("source_refs"))
            if results.get(item, {}).get("artifact_type") == "FRAMING"
        }
        if len(shared_sources) >= 2 and len(roles) < 2:
            errors.append(prefix + ":shared_origin_roles")
        bound_evidence = _ids(hypothesis.get("evidence_ids"))
        if not bound_evidence and hypothesis.get("knowledge_status") not in {"HYPOTHESIS", "UNKNOWN"}:
            errors.append(prefix + ":unsupported_status")
        if bound_evidence and any(results.get(item, {}).get("artifact_type") != "EVIDENCE" for item in bound_evidence):
            errors.append(prefix + ":evidence_binding")
        _finite_check(hypothesis.get("finite_check"), prefix, errors)

    projection, retained_views, lineage_errors = current_iceberg_view_projection(
        results, docs, input_snapshot=input_snapshot
    )
    errors.extend(lineage_errors)
    if revision_scope is None:
        revision_scope = model_revision_scope_for_run(
            run, results, docs, None, None, load_yaml, sha,
            phase=phase,
        )
    errors.extend(revision_scope.get("errors", []))
    historical = set(revision_scope.get("historical_result_ids", retained_views))
    if len(projection) != 2:
        errors.append(f"double_iceberg:view_head_coverage:{len(projection)}")
    question_ids = _question_ids(bank, docs)
    active_plans = [doc for rid, doc in docs.items() if isinstance(doc, dict)
                    and results.get(rid, {}).get("artifact_type") == "PLAN" and rid not in historical]
    active_execution = _plan_execution_endpoints(active_plans)
    if not active_plans:
        for synthesis_id in revision_scope.get("active_synthesis_ids", set()):
            for kind, identifiers in _synthesis_execution_endpoints(synthesis_id, results, docs, sha).items():
                active_execution[kind].update(identifiers)
    check_ids = active_execution["C"]
    valid_endpoints = {
        "G": {str(anchor["vector_id"])},
        "R": _requirement_ids(bank),
        "H": hypothesis_ids,
        "E": {item for item, entry in results.items() if entry.get("artifact_type") == "EVIDENCE"},
        "Q": question_ids,
        "D": {item for item, entry in results.items() if entry.get("artifact_type") == "DECISION"},
        "REVIEW": {item for item, entry in results.items() if entry.get("artifact_type") == "REVIEW"},
        **active_execution,
    }
    active_semantic_docs = _active_iceberg_semantic_docs(docs, historical)
    edges = _validate_iceberg_edges(
        active_semantic_docs, results, valid_endpoints, question_ids, check_ids, errors
    )
    unavailable_dependency = _validate_view_contents(
        run, results, docs, immutable_inputs, load_yaml, anchor, projection,
        origin_index, edges, question_ids, errors,
    ) if len(projection) == 2 else False
    # The lineage validator already checks a successor's predecessor hash.
    # Semantic use of that predecessor is still subject to strict validation.
    unconsumed_views = (
        retained_views
        - current_iceberg_consumer_result_ids(docs, set())
        - _iceberg_view_semantic_sources(docs)
        if not errors else set()
    )
    retained_diagnostics: list[str] = []
    historical_edges: dict[str, dict[str, dict[str, Any]]] = {}
    historical_synthesis_edges: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    ambiguous_view_edges: dict[str, set[str]] = defaultdict(set)
    view_execution: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"T": set(), "C": set()})
    for rid in sorted(historical):
        if results.get(rid, {}).get("artifact_type") != "SYNTHESIS":
            continue
        local_errors: list[str] = []
        synthesis = docs.get(rid, {})
        bindings = _iceberg_consumer_projection(
            rid, synthesis.get("iceberg_view_bindings"), results, docs, projection, historical, local_errors,
        )
        if local_errors:
            continue  # The consumer check reports the invalid binding.
        bound_views = {binding["result_id"]: docs[binding["result_id"]] for binding in bindings.values()}
        execution = _synthesis_execution_endpoints(rid, results, docs, sha)
        for owner in bound_views:
            for kind, identifiers in execution.items():
                view_execution[owner][kind].update(identifiers)
        historical_edges[rid] = _validate_iceberg_edges(
            {**bound_views, rid: synthesis}, results,
            {**valid_endpoints, **execution}, question_ids, execution["C"], local_errors,
        )
        errors.extend(f"historical:{rid}:" + error for error in local_errors)
        # Retained views keep access to synthesis-owned edges, never to a
        # different view's edges merely because a synthesis consumed it.
        rows = synthesis.get("semantic_edges")
        if isinstance(rows, list):
            for edge in rows:
                if not isinstance(edge,dict) or not isinstance(edge.get("id"),str):
                    continue
                edge_id=edge["id"]
                if historical_edges[rid].get(edge_id) is not edge:
                    continue
                for view_owner in bound_views:
                    owned=historical_synthesis_edges[view_owner]
                    if edge_id in owned and owned[edge_id]!=edge:
                        ambiguous_view_edges[view_owner].add(edge_id)
                    else:
                        owned[edge_id]=edge
    # Each retained view gets its own edges plus exact SYNTHESIS consumers.
    for rid in sorted(historical):
        doc = docs.get(rid)
        view = doc.get("iceberg_view") if isinstance(doc, dict) else None
        if not isinstance(view, dict):
            continue
        binding = {"view_id": view.get("view_id"), "view_kind": view.get("view_kind"),
                   "result_id": rid, "result_sha256": results[rid].get("content_hash"),
                   "revision": view.get("revision")}
        local_errors = []
        retained_edges={edge_id:edge for edge_id,edge in historical_synthesis_edges[rid].items()
                        if edge_id not in ambiguous_view_edges[rid]}
        execution=view_execution[rid]
        own_edges=_validate_iceberg_edges(
            {rid: doc}, results, {**valid_endpoints, **execution},
            question_ids, execution["C"], local_errors,
            unconsumed_historical_view=rid if rid in unconsumed_views else None,
            diagnostics=retained_diagnostics,
        )
        for edge_id in sorted(ambiguous_view_edges[rid] & set(_ids(view.get("semantic_edge_ids"))) - own_edges.keys()):
            local_errors.append(f"iceberg_view:{rid}:ambiguous_historical_edge:{edge_id}")
        _validate_view_contents(
            run, results, docs, immutable_inputs, load_yaml, anchor,
            {str(view.get("view_kind")): binding}, origin_index,
            {**retained_edges, **own_edges}, question_ids, local_errors,
        )
        errors.extend("historical:" + error for error in local_errors)
    derived_q_ids: set[str] = set()
    for doc in docs.values():
        if not isinstance(doc, dict):
            continue
        for index, record in enumerate(doc.get("records", [])):
            if not isinstance(record, dict) or record.get("record_kind") != "DERIVED_Q":
                continue
            q_id = record.get("q_id")
            prefix = f"iceberg_derived_q:{q_id if isinstance(q_id, str) else index}"
            required = {
                "q_id", "parent_source_ids", "materiality", "origin_position_refs",
                "finite_check", "equivalent_q_ids", "resource_cap_impact", "evidence_ids",
            }
            if not required.issubset(record) or not isinstance(q_id, str) or not q_id:
                errors.append(prefix + ":contract")
                continue
            derived_q_ids.add(q_id)
            if (
                not _strings(record.get("parent_source_ids"), nonempty=True)
                or not _text(record.get("materiality"))
                or record.get("equivalent_q_ids") != []
                or not _text(record.get("resource_cap_impact"))
            ):
                errors.append(prefix + ":admission")
            _validate_origin_refs(record.get("origin_position_refs"), origin_index, prefix, errors)
            _finite_check(record.get("finite_check"), prefix, errors)
            evidence = _ids(record.get("evidence_ids"))
            if not evidence or any(results.get(item, {}).get("artifact_type") != "EVIDENCE" for item in evidence):
                errors.append(prefix + ":evidence_binding")
    if derived_q_ids and not (
        any(derived_q_ids & set(_ids(docs[binding["result_id"]]["iceberg_view"].get("open_q_ids"))) for binding in projection.values())
        or any(
            edge.get("from_type") == "Q" and edge.get("from_id") in derived_q_ids
            or edge.get("to_type") == "Q" and edge.get("to_id") in derived_q_ids
            for edge in edges.values()
        )
    ):
        errors.append("double_iceberg:derived_q_not_addressed")
    if hypothesis_ids and not any(
        isinstance(doc, dict)
        and results.get(doc.get("target_result_id"), {}).get("artifact_type") == "HYPOTHESIS"
        for doc in docs.values()
    ):
        errors.append("double_iceberg:hypothesis_review_missing")
    changed_evidence = {
        result_id for result_id, entry in results.items()
        if entry.get("artifact_type") == "EVIDENCE" and docs.get(result_id, {}).get("changed") == "CHANGED"
    }
    for binding in projection.values():
        view = docs[binding["result_id"]]["iceberg_view"]
        if any(
            isinstance(source, dict)
            and source.get("source_kind") == "ACCEPTED_RESULT"
            and source.get("source_id") in changed_evidence
            for source in view.get("source_bindings", [])
        ):
            errors.append(f"iceberg_view:{binding['result_id']}:changed_source_requires_successor")
    if len(projection) == 2:
        _validate_iceberg_consumers(
            results, docs, projection, edges, unavailable_dependency, phase, errors,
            historical, historical_edges,
        )
    if diagnostics is not None and not errors:
        diagnostics.extend(retained_diagnostics)
    return projection, historical, errors


def _validate_synthesis_contract(
    results: dict[str, Any], docs: dict[str, Any], errors: list[str],
    active_synthesis_ids: set[str] | None = None,
) -> set[str]:
    synthesis_ids = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "SYNTHESIS"
    }
    if not synthesis_ids:
        errors.append("synthesis:missing")
    for result_id in sorted(synthesis_ids):
        doc = docs.get(result_id)
        prefix = f"synthesis:{result_id}"
        if not isinstance(doc, dict) or not _text(doc.get("comparison_protocol")):
            errors.append(prefix + ":comparison_protocol")
        if not isinstance(doc, dict) or not _text(doc.get("chosen_mechanism")):
            errors.append(prefix + ":chosen_mechanism")
        alternatives = doc.get("alternatives") if isinstance(doc, dict) else None
        if not isinstance(alternatives, list) or not alternatives:
            errors.append(prefix + ":alternatives")
            continue
        for index, alternative in enumerate(alternatives):
            valid = _text(alternative)
            if isinstance(alternative, dict):
                valid = all(
                    _text(alternative.get(field))
                    for field in ("id", "direction", "mechanism", "comparison", "outcome")
                )
            if not valid:
                errors.append(prefix + f":alternative:{index}:contract")
    return synthesis_ids if active_synthesis_ids is None else synthesis_ids & active_synthesis_ids


def _validate_registry(results: dict[str, Any], docs: dict[str, Any], errors: list[str]) -> None:
    for result_id in sorted(results):
        if not _result_matches_doc(result_id, results, docs):
            errors.append(f"result:{result_id}:accepted_binding")
    for result_id in sorted(set(docs) - set(results)):
        errors.append(f"result:{result_id}:not_in_accepted_registry")


_EXTERNAL_REFERENCE_STATUSES = {
    "DISCOVERED", "INSPECTED", "TESTED", "TRANSFER_VALIDATED", "REJECTED",
}
_EXTERNAL_DISCOVERY_ROUTES = {
    "DIRECT_ANALOG", "FUNCTIONAL_ANALOG", "ALTERNATIVE_MECHANISM", "NEGATIVE_EXPERIENCE",
}
_EXTERNAL_RECEIPT_SCOPES = {"UPSTREAM_CI", "LOCAL_REPRODUCTION", "OTHER_OBSERVED_RUN"}
_EXTERNAL_HISTORY_KINDS = {"ISSUE", "PR", "COMMIT", "RELEASE"}
_EXTERNAL_ORIGIN_RELATIONSHIPS = {
    "PRIMARY", "FORK", "COPY", "DERIVATIVE", "SHARED_PRIMARY_SOURCE", "UNKNOWN",
}
# Exact v2 unknown-field leaves. These are the only external-E fields whose
# conditional absence can be represented by an explicit marker.
_EXTERNAL_UNKNOWN_SCALARS = {
    "maturity.status", "maturity.basis", "license.status", "license.identifier",
    "license.source_path", "license.content_sha256", "origin.primary_repository",
    "origin.relationship",
}
_EXTERNAL_UNKNOWN_COLLECTIONS = {
    "implementation.material_refs", "tests_inspected", "check_receipts",
    "version_fix_history", "current_limits", "license.use_limits",
    "origin.evidence_refs", "transfer.target_conditions",
    "transfer.applicability_evidence_ids", "transfer.limits", "transfer.new_risks",
}
_EXTERNAL_UNKNOWN_FIELDS = _EXTERNAL_UNKNOWN_SCALARS | _EXTERNAL_UNKNOWN_COLLECTIONS


def _commit_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(char in "0123456789abcdef" for char in value)
    )


def _safe_relative_path(value: Any) -> bool:
    if not _text(value):
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and bool(path.parts)


def _material_cache_error(run: Path, material: dict[str, Any], prefix: str) -> str | None:
    cache = material.get("cached_artifact")
    if cache is None:
        return None
    error = prefix + ":cached_artifact"
    if (not isinstance(cache, dict) or not _safe_relative_path(cache.get("path"))
        or not _hash(cache.get("sha256")) or cache["sha256"] != material.get("content_sha256")):
        return error
    run = Path(run).absolute()
    relative = (run.relative_to(run.anchor).as_posix().rstrip("/") + "/" + cache["path"])
    try:
        fd = os.open(run.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            raw = capture_file(fd, relative, 16 * 1024**2)
        finally:
            os.close(fd)
    except (OSError, AdmissionError):
        return error + ":source_unavailable"
    if hashlib.sha256(raw).hexdigest() != cache["sha256"]:
        return error + ":source_drift"
    return None


def external_cache_errors(run: Path, doc: dict[str, Any], prefix: str) -> list[str]:
    if doc.get("evidence_subkind") != "EXTERNAL_IMPLEMENTATION":
        return []
    implementation = doc.get("implementation")
    materials = implementation.get("material_refs") if isinstance(implementation, dict) else None
    if not isinstance(materials, list):
        return []
    return [
        error for index, material in enumerate(materials) if isinstance(material, dict)
        if (error := _material_cache_error(run, material, prefix + f":material:{index}"))
    ]


def _repository_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.count("/") == 1
        and all(part and not any(char.isspace() for char in part) for part in value.split("/"))
    )


def _unknown_field_paths(doc: dict[str, Any], prefix: str, errors: list[str]) -> set[str]:
    value = doc.get("unknown_fields")
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(prefix + ":unknown_fields_contract")
        return set()
    if len(value) != len(set(value)):
        errors.append(prefix + ":unknown_fields_duplicate")
    fields = set(value)
    for path in sorted(fields - _EXTERNAL_UNKNOWN_FIELDS):
        errors.append(prefix + f":unknown_field_invalid:{path}")
    return fields


def _nested_value(doc: dict[str, Any], path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _validate_unknown_field_closure(
    doc: dict[str, Any], unknown_fields: set[str], prefix: str, errors: list[str]
) -> None:
    """Enforce v2's exact scalar/collection conditional-unknown encoding."""
    for path in sorted(_EXTERNAL_UNKNOWN_SCALARS):
        value = _nested_value(doc, path)
        marked = path in unknown_fields
        if value == "UNKNOWN" and not marked:
            errors.append(prefix + f":unknown_scalar_missing_marker:{path}")
        elif marked and value != "UNKNOWN":
            errors.append(prefix + f":unknown_scalar_marker_not_unknown:{path}")
    for path in sorted(_EXTERNAL_UNKNOWN_COLLECTIONS):
        value = _nested_value(doc, path)
        marked = path in unknown_fields
        if isinstance(value, list) and any(item == "UNKNOWN" for item in value):
            errors.append(prefix + f":unknown_collection_member:{path}")
        if marked and value != []:
            errors.append(prefix + f":unknown_collection_marker_not_empty:{path}")


def _architecture_review_contract(review: Any, results: dict[str, Any], prefix: str, *, stale_result_ids: Iterable[str] = ()) -> list[str]:
    """Candidate-local typed audit guard, shared with final closure checks."""
    fields = {"structural_report_sha256", "semantic_outcome", "procedure", "observation", "evidence_ids", "findings", "limits"}
    if not isinstance(review, dict) or not fields.issubset(review):
        return [prefix + ":review_contract"]
    errors: list[str] = []
    stale = set(stale_result_ids)
    if not _hash(review.get("structural_report_sha256")):
        errors.append(prefix + ":structural_report_hash")
    if not isinstance(review.get("semantic_outcome"), str) or review["semantic_outcome"] not in {"PASS", "FAIL", "NOT_VERIFIED"}:
        errors.append(prefix + ":semantic_outcome")
    for field in ("procedure", "observation"):
        if not _text(review.get(field)):
            errors.append(prefix + ":" + field)
    for field in ("evidence_ids", "limits"):
        value = review.get(field)
        if not _strings(value) or len(value) != len(set(value)):
            errors.append(prefix + ":" + field + "_contract")
    for identifier in _ids(review.get("evidence_ids")):
        if identifier in stale or not isinstance(results.get(identifier), dict) or results[identifier].get("artifact_type") != "EVIDENCE":
            errors.append(prefix + ":evidence_binding")
    if review.get("semantic_outcome") == "PASS" and not _ids(review.get("evidence_ids")):
        errors.append(prefix + ":semantic_pass_without_evidence")
    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append(prefix + ":findings_contract")
        return errors
    seen: set[str] = set()
    for finding in findings:
        if (not isinstance(finding, dict) or not _text(finding.get("id"))
                or finding["id"] in seen or not _text(finding.get("target_id"))
                or not _text(finding.get("required_action")) or not _strings(finding.get("evidence_ids"), nonempty=True)
                or len(finding["evidence_ids"]) != len(set(finding["evidence_ids"]))
                or any(e in stale or not isinstance(results.get(e), dict) or results[e].get("artifact_type") != "EVIDENCE" for e in _ids(finding.get("evidence_ids")))):
            errors.append(prefix + ":finding_contract")
        else:
            seen.add(finding["id"])
    return sorted(set(errors))


def ledger_gap_outcome(record, question_ids, verified):
    """Recognize retained OPEN source coverage backed by the current ledger."""
    if (not verified or not question_ids or record.get("answer_status")!="OPEN"
        or record.get("knowledge_state")!="UNKNOWN" or "answer_result_id" in record
        or "author_assignment_id" in record
        or record.get("source_disposition") not in (None,"KEPT","MERGED","REFINED","SPLIT")):
        return False
    return all(verified.get(identifier) is not None and record["question_gap"]==verified[identifier]
               for identifier in question_ids)


def validate_intake_candidate(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    doc: dict[str, Any],
    artifact_type: str,
    load_yaml: Callable[[Path], Any],
    question_gap_proofs: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Check candidate contracts that must fail before immutable commit.

    The guard is deliberately candidate-scoped.  It does not require a future
    stage, dependency, decision, evidence, or final audit to exist, and the
    complete ``validate_model`` pass remains the authority for graph closure.
    """
    errors: list[str] = []
    prefix = f"intake:{artifact_type}:{doc.get('id', 'UNKNOWN')}"

    if "architecture_graph" in doc and (artifact_type != "SYNTHESIS" or doc.get("author_role") != "OP-SYNTHESIS"):
        errors.append(prefix + ":architecture_graph_owner")
    if "architecture_ref" in doc and artifact_type not in {"PLAN", "FINAL_AUDIT"}:
        errors.append(prefix + ":architecture_ref_owner")

    if artifact_type in {"SYNTHESIS", "PLAN", "FINAL_AUDIT"} and architecture_enabled(_load_mapping(Path(run) / "input/snapshot.yaml", load_yaml)):
        from architecture_validation import validate_architecture_graph
        accepted_docs = {}
        for result_id, entry in results.items():
            path = _entry_path(Path(run), entry)
            if path is not None:
                value = _load_mapping(path, load_yaml)
                if value is not None:
                    accepted_docs[result_id] = value
        if artifact_type == "SYNTHESIS":
            context = architecture_context(run, bank, results, accepted_docs, load_yaml=load_yaml)
            errors.extend(context["context_errors"])
            errors.extend(validate_architecture_graph(doc.get("architecture_graph"), context)["errors"])
            if not isinstance(doc.get("architecture_graph"), dict) or doc.get("chosen_mechanism_id") != doc["architecture_graph"].get("selected_mechanism_id"):
                errors.append(prefix + ":architecture_selected_mechanism")
        else:
            _, ref_errors = _architecture_reference(Path(run), doc.get("architecture_ref"), results, accepted_docs, lambda data: hashlib.sha256(data).hexdigest())
            errors.extend(ref_errors)
            if artifact_type == "FINAL_AUDIT":
                errors.extend(_architecture_review_contract(doc.get("architecture_review"), results, prefix + ":architecture", stale_result_ids=_ids((_load_mapping(Path(run) / "state/current.yaml", load_yaml) or {}).get("stale_result_ids"))))

    if artifact_type == "SOURCE_OUTCOMES":
        original_questions = {
            item["id"]
            for item in bank.get("questions", [])
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
        }
        records = doc.get("records")
        if not isinstance(records, list):
            return [prefix + ":records_contract"]
        for index, record in enumerate(records):
            record_prefix = prefix + f":record:{index}"
            if not isinstance(record, dict):
                errors.append(record_prefix + ":contract")
                continue
            kind = record.get("record_kind")
            if kind == "DERIVED_Q":
                # Derived-question closure remains part of full model validation.
                continue
            if kind != "SOURCE_OUTCOME":
                errors.append(record_prefix + ":record_kind")
                continue
            if not isinstance(record.get("answer_status"), str) or record["answer_status"] not in _ANSWER_STATUSES:
                errors.append(record_prefix + ":answer_status")
            if not isinstance(record.get("knowledge_state"), str) or record["knowledge_state"] not in _KNOWLEDGE_STATES:
                errors.append(record_prefix + ":knowledge_state")
            question_ids = record.get("original_q_ids")
            if (
                not isinstance(question_ids, list)
                or not question_ids
                or len(question_ids) != len(set(question_ids))
                or any(
                    not isinstance(question_id, str)
                    or question_id not in original_questions
                    for question_id in question_ids
                )
            ):
                errors.append(record_prefix + ":original_q_ids")
                continue
            if "question_gap" in record:
                if not ledger_gap_outcome(record, question_ids, question_gap_proofs):
                    errors.append(record_prefix + ":question_gap_not_verified")
                continue
            answer_result_id = record.get("answer_result_id")
            answer_entry = results.get(answer_result_id)
            if (
                answer_result_id == doc.get("id")
                or not isinstance(answer_entry, dict)
                or answer_entry.get("artifact_type") not in {"ANSWER", "PRIMARY"}
            ):
                errors.append(record_prefix + ":answer_result_not_accepted_primary_or_answer")
                continue
            if answer_entry.get("assignment_id") != record.get("author_assignment_id"):
                errors.append(record_prefix + ":author_assignment_binding")
            answer_path = _entry_path(run, answer_entry)
            try:
                answer_doc = load_yaml(answer_path) if answer_path is not None else None
            except Exception:
                answer_doc = None
            addressed: set[str] = set()
            if isinstance(answer_doc, dict):
                claimed = answer_doc.get("addressed_q_ids", answer_doc.get("original_q_ids", []))
                if isinstance(claimed, list):
                    addressed.update(item for item in claimed if isinstance(item, str))
                per_question = answer_doc.get("per_question_outcomes", [])
                if isinstance(per_question, list):
                    addressed.update(
                        item.get("q_id")
                        for item in per_question
                        if isinstance(item, dict) and isinstance(item.get("q_id"), str)
                    )
            if not set(question_ids).issubset(addressed):
                errors.append(record_prefix + ":answer_question_binding")

    elif artifact_type == "EVIDENCE":
        if doc.get("changed") not in {"CHANGED", "UNCHANGED"}:
            errors.append(prefix + ":changed_enum")
        if doc.get("evidence_subkind") == "EXTERNAL_IMPLEMENTATION":
            unknown_fields = _unknown_field_paths(doc, prefix, errors)
            _validate_unknown_field_closure(doc, unknown_fields, prefix, errors)
            errors.extend(external_cache_errors(run, doc, prefix))

    elif artifact_type == "PLAN":
        graph = doc.get("local_execution_graph")
        tasks = doc.get("tasks")
        if not isinstance(graph, dict):
            errors.append(prefix + ":local_graph_contract")
            return errors
        if not isinstance(tasks, list):
            errors.append(prefix + ":tasks_contract")
            return errors
        task_ids = {
            task["id"]
            for task in tasks
            if isinstance(task, dict)
            and isinstance(task.get("id"), str)
            and task["id"]
        }
        entry = graph.get("entry")
        exit_id = graph.get("exit")
        if not isinstance(entry, str) or not entry or not isinstance(exit_id, str) or not exit_id:
            errors.append(prefix + ":entry_exit_contract")
            return errors
        endpoints = task_ids | {entry, exit_id}
        edges = graph.get("edges")
        if not isinstance(edges, list):
            errors.append(prefix + ":local_edges_contract")
            return errors
        for index, value in enumerate(edges):
            pair = _edge_pair(value)
            if pair is None:
                errors.append(prefix + f":local_edge:{index}:contract")
                continue
            source, target = pair
            if source not in endpoints or target not in endpoints:
                errors.append(prefix + f":local_edge:{index}:endpoint:{source}->{target}")

        if architecture_enabled(_load_mapping(Path(run) / "input/snapshot.yaml", load_yaml)):
            errors.extend(local_plan_errors(doc,prefix))

    return errors


def _known_text_or_declared_unknown(value: Any, path: str, unknown_fields: set[str]) -> bool:
    if value == "UNKNOWN":
        return path in unknown_fields
    return _text(value)


def _known_hash_or_declared_unknown(value: Any, path: str, unknown_fields: set[str]) -> bool:
    if value == "UNKNOWN":
        return path in unknown_fields
    return _hash(value)


def _result_file_matches(
    run: Path, result_id: str, results: dict[str, Any], sha: Callable[[bytes], str]
) -> bool:
    entry = results.get(result_id)
    path = _entry_path(run, entry)
    if not isinstance(entry, dict) or path is None:
        return False
    try:
        return sha(path.read_bytes()) == entry.get("content_hash")
    except OSError:
        return False


def _validate_external_implementation_evidence(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    sha: Callable[[bytes], str],
    errors: list[str],
) -> None:
    """Validate only the typed external-E structural contract.

    This deliberately checks addressability and declared bindings, never whether
    remote bytes, CI execution, origin independence, licensing, or transfer
    claims are substantively true.
    """
    questions = _question_ids(bank, docs)
    decisions = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "DECISION"
        and _result_matches_doc(result_id, results, docs)
    }
    transfer_edges: list[tuple[str, str]] = []
    external_ids = {
        result_id
        for result_id, doc in docs.items()
        if isinstance(doc, dict) and doc.get("evidence_subkind") == "EXTERNAL_IMPLEMENTATION"
    }

    for result_id in sorted(external_ids):
        doc = docs[result_id]
        entry = results.get(result_id)
        prefix = f"external_evidence:{result_id}"
        if (
            not isinstance(entry, dict)
            or entry.get("artifact_type") != "EVIDENCE"
            or not _result_matches_doc(result_id, results, docs)
            or not _result_file_matches(run, result_id, results, sha)
        ):
            errors.append(prefix + ":accepted_evidence_binding")

        unknown_fields = _unknown_field_paths(doc, prefix, errors)
        _validate_unknown_field_closure(doc, unknown_fields, prefix, errors)
        if doc.get("authority_class") != "C_EXTERNAL_IMPLEMENTATION":
            errors.append(prefix + ":authority_class")
        status = doc.get("reference_status")
        if status not in _EXTERNAL_REFERENCE_STATUSES:
            errors.append(prefix + ":reference_status")

        question_ids = doc.get("question_ids")
        decision_ids = doc.get("decision_ids")
        if not _strings(question_ids) or len(question_ids) != len(set(question_ids)):
            errors.append(prefix + ":question_ids")
            question_ids = []
        if not _strings(decision_ids) or len(decision_ids) != len(set(decision_ids)):
            errors.append(prefix + ":decision_ids")
            decision_ids = []
        if not question_ids and not decision_ids:
            errors.append(prefix + ":empty_question_decision_binding")
        for question_id in question_ids:
            if question_id not in questions:
                errors.append(prefix + f":unknown_question:{question_id}")
        for decision_id in decision_ids:
            if decision_id not in decisions:
                errors.append(prefix + f":unknown_decision:{decision_id}")
        related_ids = set(_ids(doc.get("related_ids")))
        for related_id in sorted(set(question_ids) | set(decision_ids)):
            if related_id not in related_ids:
                errors.append(prefix + f":related_id_missing:{related_id}")

        discovery = doc.get("discovery")
        if not isinstance(discovery, dict):
            errors.append(prefix + ":discovery_contract")
        else:
            if discovery.get("route") not in _EXTERNAL_DISCOVERY_ROUTES:
                errors.append(prefix + ":discovery_route")
            for field in ("source", "tool_id", "query", "retrieved_at"):
                if not _text(discovery.get(field)) or discovery.get(field) == "UNKNOWN":
                    errors.append(prefix + f":discovery_{field}")

        repository = doc.get("repository")
        repository_name = None
        repository_url = None
        commit_sha = None
        if not isinstance(repository, dict):
            errors.append(prefix + ":repository_contract")
        else:
            repository_name = repository.get("full_name")
            repository_url = repository.get("url")
            commit_sha = repository.get("commit_sha")
            if not _repository_name(repository_name):
                errors.append(prefix + ":repository_full_name")
            if not isinstance(repository_url, str) or repository_url != f"https://github.com/{repository_name}":
                errors.append(prefix + ":repository_url")
            if not _commit_sha(commit_sha):
                errors.append(prefix + ":repository_commit_sha")
            expected_commit = f"https://github.com/{repository_name}/commit/{commit_sha}"
            if repository.get("commit_permalink") != expected_commit or not _commit_sha(commit_sha):
                errors.append(prefix + ":repository_commit_permalink")

        implementation = doc.get("implementation")
        material_refs: list[Any] = []
        if not isinstance(implementation, dict):
            errors.append(prefix + ":implementation_contract")
        else:
            if not _text(implementation.get("mechanism")):
                errors.append(prefix + ":implementation_mechanism")
            for field in ("inputs", "conditions", "assumptions"):
                if not _strings(implementation.get(field), nonempty=True):
                    errors.append(prefix + f":implementation_{field}")
            raw_materials = implementation.get("material_refs")
            if not isinstance(raw_materials, list):
                errors.append(prefix + ":material_refs_type")
            else:
                material_refs = raw_materials
                for index, material in enumerate(raw_materials):
                    material_prefix = prefix + f":material:{index}"
                    if not isinstance(material, dict):
                        errors.append(material_prefix + ":contract")
                        continue
                    path = material.get("path")
                    if not _safe_relative_path(path):
                        errors.append(material_prefix + ":path")
                    if not _strings(material.get("functions"), nonempty=True):
                        errors.append(material_prefix + ":functions")
                    expected = f"https://github.com/{repository_name}/blob/{commit_sha}/{path}"
                    if material.get("permalink") != expected or not _commit_sha(commit_sha):
                        errors.append(material_prefix + ":permalink")
                    if not _text(material.get("read_scope")):
                        errors.append(material_prefix + ":read_scope")
                    if not _hash(material.get("content_sha256")):
                        errors.append(material_prefix + ":content_sha256")
                    cache_error = _material_cache_error(run, material, material_prefix)
                    if cache_error is not None:
                        errors.append(cache_error)
                    if not _text(material.get("retrieved_at")):
                        errors.append(material_prefix + ":retrieved_at")
        if status in {"INSPECTED", "TESTED", "TRANSFER_VALIDATED"} and not material_refs:
            errors.append(prefix + ":status_requires_material")

        tests_inspected = doc.get("tests_inspected")
        if not isinstance(tests_inspected, list):
            errors.append(prefix + ":tests_inspected_type")
            tests_inspected = []
        for index, test in enumerate(tests_inspected):
            test_prefix = prefix + f":test:{index}"
            if not isinstance(test, dict):
                errors.append(test_prefix + ":contract")
                continue
            path = test.get("path")
            if not _safe_relative_path(path):
                errors.append(test_prefix + ":path")
            if not _strings(test.get("named_checks"), nonempty=True):
                errors.append(test_prefix + ":named_checks")
            expected = f"https://github.com/{repository_name}/blob/{commit_sha}/{path}"
            if test.get("permalink") != expected or not _commit_sha(commit_sha):
                errors.append(test_prefix + ":permalink")
            if not _hash(test.get("content_sha256")):
                errors.append(test_prefix + ":content_sha256")

        receipts = doc.get("check_receipts")
        if not isinstance(receipts, list):
            errors.append(prefix + ":check_receipts_type")
            receipts = []
        for index, receipt in enumerate(receipts):
            receipt_prefix = prefix + f":receipt:{index}"
            if not isinstance(receipt, dict):
                errors.append(receipt_prefix + ":contract")
                continue
            if receipt.get("execution_scope") not in _EXTERNAL_RECEIPT_SCOPES:
                errors.append(receipt_prefix + ":execution_scope")
            if not _text(receipt.get("named_check")):
                errors.append(receipt_prefix + ":named_check")
            if receipt.get("commit_sha") != commit_sha or not _commit_sha(receipt.get("commit_sha")):
                errors.append(receipt_prefix + ":commit_sha")
            if receipt.get("result") != "PASS":
                errors.append(receipt_prefix + ":result")
            if not _text(receipt.get("receipt_ref")):
                errors.append(receipt_prefix + ":receipt_ref")
            if not _text(receipt.get("executed_at")):
                errors.append(receipt_prefix + ":executed_at")
        if status in {"TESTED", "TRANSFER_VALIDATED"} and not receipts:
            errors.append(prefix + ":status_requires_check_receipt")
        if status in {"TESTED", "TRANSFER_VALIDATED"} and not tests_inspected:
            errors.append(prefix + ":status_requires_tests_inspected")

        history = doc.get("version_fix_history")
        if not isinstance(history, list):
            errors.append(prefix + ":version_fix_history_type")
        else:
            for index, item in enumerate(history):
                item_prefix = prefix + f":history:{index}"
                if not isinstance(item, dict):
                    errors.append(item_prefix + ":contract")
                    continue
                if item.get("kind") not in _EXTERNAL_HISTORY_KINDS:
                    errors.append(item_prefix + ":kind")
                if item.get("state") != "FIX_VERIFIED":
                    errors.append(item_prefix + ":state")
                for field in ("reference", "relevance"):
                    if not _text(item.get(field)):
                        errors.append(item_prefix + f":{field}")
        if not isinstance(doc.get("current_limits"), list) or not _strings(doc.get("current_limits")):
            errors.append(prefix + ":current_limits")

        maturity = doc.get("maturity")
        if not isinstance(maturity, dict) or maturity.get("status") not in {"KNOWN", "UNKNOWN"}:
            errors.append(prefix + ":maturity_status")
        elif not _known_text_or_declared_unknown(maturity.get("basis"), "maturity.basis", unknown_fields):
            errors.append(prefix + ":maturity_basis")

        license_value = doc.get("license")
        if not isinstance(license_value, dict) or license_value.get("status") not in {"KNOWN", "UNKNOWN"}:
            errors.append(prefix + ":license_status")
        else:
            for field in ("identifier", "source_path"):
                if not _known_text_or_declared_unknown(license_value.get(field), "license." + field, unknown_fields):
                    errors.append(prefix + ":license_" + field)
            if not _known_hash_or_declared_unknown(license_value.get("content_sha256"), "license.content_sha256", unknown_fields):
                errors.append(prefix + ":license_content_sha256")
            use_limits = license_value.get("use_limits")
            if not _strings(use_limits):
                errors.append(prefix + ":license_use_limits")
            elif any(item == "UNKNOWN" for item in use_limits) and "license.use_limits" not in unknown_fields:
                errors.append(prefix + ":license_use_limits_unknown_marker")

        origin = doc.get("origin")
        if not isinstance(origin, dict):
            errors.append(prefix + ":origin_contract")
        else:
            if not _text(origin.get("origin_family")) or origin.get("origin_family") == "UNKNOWN":
                errors.append(prefix + ":origin_family")
            if not _known_text_or_declared_unknown(origin.get("primary_repository"), "origin.primary_repository", unknown_fields):
                errors.append(prefix + ":origin_primary_repository")
            relationship = origin.get("relationship")
            if relationship not in _EXTERNAL_ORIGIN_RELATIONSHIPS:
                errors.append(prefix + ":origin_relationship")
            if not _strings(origin.get("evidence_refs")):
                errors.append(prefix + ":origin_evidence_refs")
            if origin.get("independent_origin") is True:
                errors.append(prefix + ":unsupported_independence_assertion")
        if not _known_text_or_declared_unknown(doc.get("interpretation"), "interpretation", unknown_fields):
            errors.append(prefix + ":interpretation")

        disposition = doc.get("disposition")
        if disposition not in {"USE", "REJECT", "DEFER"}:
            errors.append(prefix + ":disposition")
        if not _text(doc.get("disposition_basis")) or doc.get("disposition_basis") == "UNKNOWN":
            errors.append(prefix + ":disposition_basis")
        if status == "REJECTED" and disposition != "REJECT":
            errors.append(prefix + ":rejected_requires_reject_disposition")

        transfer = doc.get("transfer")
        if not isinstance(transfer, dict):
            errors.append(prefix + ":transfer_contract")
        else:
            target_conditions = transfer.get("target_conditions")
            target_ids = transfer.get("applicability_evidence_ids")
            for field in ("target_conditions", "limits", "new_risks"):
                if not _strings(transfer.get(field)):
                    errors.append(prefix + ":transfer_" + field)
            if not _strings(target_ids):
                errors.append(prefix + ":transfer_applicability_evidence_ids")
                target_ids = []
            if len(target_ids) != len(set(target_ids)):
                errors.append(prefix + ":transfer_applicability_evidence_ids_duplicate")
            if status == "TRANSFER_VALIDATED" and not _strings(target_conditions, nonempty=True):
                errors.append(prefix + ":transfer_validated_requires_target_conditions")
            if status == "TRANSFER_VALIDATED" and not target_ids:
                errors.append(prefix + ":transfer_validated_requires_applicability_evidence")
            for target_id in target_ids:
                target_entry = results.get(target_id)
                if target_id == result_id:
                    errors.append(prefix + ":transfer_self_reference")
                    continue
                if (
                    not isinstance(target_entry, dict)
                    or target_entry.get("artifact_type") != "EVIDENCE"
                    or not _result_matches_doc(target_id, results, docs)
                    or not _result_file_matches(run, target_id, results, sha)
                ):
                    errors.append(prefix + f":transfer_target_not_accepted_evidence:{target_id}")
                    continue
                transfer_edges.append((result_id, target_id))

    if transfer_edges and not _acyclic(external_ids | {target for _, target in transfer_edges}, transfer_edges):
        errors.append("external_evidence:transfer_cycle")

def _review_contract(
    review_id: str,
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    errors: list[str],
) -> bool:
    entry = results.get(review_id)
    review = docs.get(review_id)
    prefix = f"review:{review_id}"
    if not isinstance(entry, dict) or entry.get("artifact_type") != "REVIEW" or not isinstance(review, dict):
        errors.append(prefix + ":accepted_binding")
        return False
    target_id = review.get("target_result_id")
    target = results.get(target_id) if isinstance(target_id, str) else None
    target_doc = docs.get(target_id) if isinstance(target_id, str) else None
    valid = True
    registry_target = (
        isinstance(target, dict)
        and isinstance(target_doc, dict)
        and review.get("target_hash") == target.get("content_hash")
    )
    immutable_target = (
        isinstance(target_id, str)
        and not isinstance(target, dict)
        and target_id in immutable_inputs
        and review.get("target_hash") == immutable_inputs[target_id]
    )
    if not isinstance(target_id, str) or not (registry_target or immutable_target):
        errors.append(prefix + ":target_binding")
        valid = False
    reviewer_role = entry.get("role_id")
    reviewer_agent = entry.get("agent_id")
    if isinstance(target, dict) and (
        reviewer_agent == target.get("agent_id") or review.get("agent_id") == target.get("agent_id")
    ):
        errors.append(prefix + ":author_is_reviewer")
        valid = False
    blind_id = review.get("blind_input_result_id", review.get("blind_result_id"))
    blind_entry = results.get(blind_id)
    blind_doc = docs.get(blind_id)
    if (
        not isinstance(blind_id, str)
        or not isinstance(blind_entry, dict)
        or blind_entry.get("artifact_type") != "FRAMING"
        or not isinstance(blind_doc, dict)
        or blind_entry.get("role_id") != reviewer_role
        or blind_entry.get("agent_id") != reviewer_agent
        or review.get("blind_input_hash") != blind_entry.get("content_hash")
        or (review.get("blind_position_hash") is not None and review.get("blind_position_hash") != blind_entry.get("content_hash"))
    ):
        errors.append(prefix + ":blind_framing_binding")
        valid = False
    required_text = (
        "reviewer_competence", "nonauthor_declaration", "check_id",
        "check_provenance", "risk_coverage", "procedure", "risk_tested",
        "claim_scope", "counterexample_or_observation",
    )
    for field in required_text:
        if not _text(review.get(field)):
            errors.append(prefix + f":empty_{field}")
            valid = False
    if review.get("review_kind") not in {"PEER", "SUBJECT", "SYSTEM"}:
        errors.append(prefix + ":review_kind")
        valid = False
    if review.get("outcome") not in _OUTCOMES:
        errors.append(prefix + ":outcome")
        valid = False
    for field in ("required_repair", "regression", "independence_limits"):
        if not isinstance(review.get(field), list):
            errors.append(prefix + f":{field}_type")
            valid = False
    return valid


def _validate_reviews(
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    errors: list[str],
    active_critical_decision_ids: set[str] | None = None,
) -> set[str]:
    valid_reviews: set[str] = set()
    for review_id in sorted(
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "REVIEW"
    ):
        if _review_contract(review_id, results, docs, immutable_inputs, errors):
            valid_reviews.add(review_id)

    primaries = {
        result_id: entry
        for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "PRIMARY"
        and isinstance(entry.get("role_id"), str)
        and entry["role_id"].startswith("P-")
    }
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, set[str]] = defaultdict(set)
    for review_id in valid_reviews:
        review = docs[review_id]
        target_id = review.get("target_result_id")
        if (
            target_id in primaries
            and review.get("review_kind") == "PEER"
            and results[review_id].get("role_id", "").startswith("P-")
        ):
            incoming[target_id].append(review_id)
            target_role = primaries[target_id].get("role_id")
            if target_role != results[review_id].get("role_id"):
                outgoing[results[review_id]["role_id"]].add(str(target_role))

    for primary_id, entry in sorted(primaries.items()):
        primary = docs.get(primary_id, {})
        critique = primary.get("self_critique") if isinstance(primary, dict) else None
        required = {
            "strongest_competing_cause", "false_success_example",
            "falsification_condition", "goal_consequence",
        }
        if not isinstance(critique, dict) or not required.issubset(critique) or any(not _text(critique.get(key)) for key in required):
            errors.append(f"primary:{primary_id}:self_critique")
        reviewers = {
            (results[review_id].get("role_id"), results[review_id].get("agent_id"))
            for review_id in incoming[primary_id]
            if results[review_id].get("role_id") != entry.get("role_id")
            and results[review_id].get("agent_id") != entry.get("agent_id")
        }
        if len(reviewers) < 1:
            errors.append(f"primary:{primary_id}:peer_review_coverage")

    for role in sorted({entry.get("role_id") for entry in primaries.values()}):
        if isinstance(role, str) and len(outgoing[role]) < 2:
            errors.append(f"perspective:{role}:outgoing_peer_review_coverage")

    critical_decisions = {
        result_id: doc
        for result_id, doc in docs.items()
        if isinstance(results.get(result_id), dict)
        and results[result_id].get("artifact_type") == "DECISION"
        and isinstance(doc, dict)
        and doc.get("criticality") == "CRITICAL"
    }
    if active_critical_decision_ids is not None:
        if (
            not isinstance(active_critical_decision_ids, set)
            or any(not isinstance(result_id, str) for result_id in active_critical_decision_ids)
            or not active_critical_decision_ids.issubset(critical_decisions)
        ):
            errors.append("model:active_critical_decisions_contract")
        else:
            critical_decisions = {
                result_id: critical_decisions[result_id]
                for result_id in active_critical_decision_ids
            }
    for decision_id, decision in sorted(critical_decisions.items()):
        candidates = [
            review_id for review_id in valid_reviews
            if docs[review_id].get("target_result_id") == decision_id
            and docs[review_id].get("outcome") == "PASS"
            and docs[review_id].get("review_kind") in {"SUBJECT", "SYSTEM"}
            and results[review_id].get("agent_id") != results[decision_id].get("agent_id")
        ]
        by_kind = {
            kind: [review_id for review_id in candidates if docs[review_id].get("review_kind") == kind]
            for kind in ("SUBJECT", "SYSTEM")
        }
        if not by_kind["SUBJECT"] or not by_kind["SYSTEM"]:
            errors.append(f"decision:{decision_id}:critical_review_coverage")
            continue
        distinct_pair = False
        for subject_id in by_kind["SUBJECT"]:
            for system_id in by_kind["SYSTEM"]:
                subject = docs[subject_id]
                system = docs[system_id]
                distinct_fields = (
                    "assignment_id", "agent_id", "check_id", "check_provenance",
                    "procedure", "risk_coverage", "risk_tested",
                )
                if all(subject.get(field) != system.get(field) for field in distinct_fields):
                    distinct_pair = True
                    break
            if distinct_pair:
                break
        if not distinct_pair:
            errors.append(f"decision:{decision_id}:reused_review_proof")
    return valid_reviews


def local_plan_errors(stage: dict[str, Any], prefix: str) -> list[str]:
    """Intrinsic stage structure needs no already accepted sibling."""
    graph=stage.get("local_execution_graph")
    tasks=stage.get("tasks")
    checks=stage.get("acceptance_exit")
    contracts=stage.get("inputs_contracts")
    errors=[]
    task_ids=_unique_local_ids(tasks)
    check_ids=_unique_local_ids(checks)
    if not task_ids or not check_ids:
        errors.append(prefix + ":local_task_check_contract")
    if (not isinstance(contracts,dict) or any(not isinstance(contracts.get(field),list) or not contracts[field]
                                              for field in ("inputs","outputs"))):
        errors.append(prefix + ":local_typed_contracts")
    if not isinstance(graph,dict) or not task_ids:
        return errors
    entry,exit_id,first=graph.get("entry"),graph.get("exit"),graph.get("first_task")
    if not all(_text(value) for value in (entry,exit_id,first)) or first not in task_ids:
        return errors+[prefix + ":local_first_task"]
    endpoints=task_ids|{entry,exit_id}
    edges=graph.get("edges")
    pairs=[_edge_pair(edge) for edge in edges] if isinstance(edges,list) else []
    if any(pair is None or not set(pair).issubset(endpoints) for pair in pairs):
        return errors+[prefix + ":local_edge_endpoint"]
    if ((entry,first) not in pairs or exit_id not in _reachable(pairs,entry)
        or not _acyclic(endpoints,pairs)):
        errors.append(prefix + ":local_execution_path")
    return errors


def _unique_local_ids(rows: Any) -> set[str]:
    if not isinstance(rows,list) or any(not isinstance(row,dict) or not _text(row.get("id")) for row in rows):
        return set()
    ids={row["id"] for row in rows}
    return ids if len(ids)==len(rows) else set()


def _validate_stages(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    errors: list[str],
    plan_projection: dict[str, str] | None = None,
) -> tuple[set[str], set[str], dict[str, dict[str, Any]]]:
    """Validate only the explicitly current PLAN graph when supplied.

    Keys remain immutable accepted result IDs; projection values are the logical
    stage identities (for example an accepted `S-000-R2` can be current logical
    `S-000`). Historical PLAN entries stay in `results` for provenance but do
    not create a duplicate active graph.
    """
    stage_docs: dict[str, dict[str, Any]] = {}
    stage_entries: dict[str, dict[str, Any]] = {}
    result_to_logical = plan_projection or {}
    if plan_projection is not None and (not isinstance(plan_projection, dict) or any(not isinstance(key,str) or not isinstance(value,str) or not value for key,value in plan_projection.items()) or len(set(plan_projection.values())) != len(plan_projection)):
        errors.append("plan_projection:contract")
        result_to_logical = {}
    for result_id, entry in sorted(results.items()):
        if not isinstance(entry, dict) or entry.get("artifact_type") != "PLAN":
            continue
        if plan_projection is not None and result_id not in result_to_logical:
            continue
        path = entry.get("path")
        doc = docs.get(result_id)
        if (
            not isinstance(path, str)
            or not path.startswith("plan/S-")
            or not path.endswith((".yaml", ".yml"))
            or not isinstance(doc, dict)
            or doc.get("id") != result_id
            or entry.get("role_id") != "PLAN-AUTHOR"
        ):
            errors.append(f"plan_result:{result_id}:canonical_binding")
            continue
        logical_stage_id=result_to_logical.get(result_id,result_id)
        if logical_stage_id in stage_docs:
            errors.append(f"plan_projection:duplicate_logical_stage:{logical_stage_id}")
            continue
        stage_docs[logical_stage_id] = doc
        stage_entries[logical_stage_id] = entry
    if "S-000" not in stage_docs:
        errors.append("stage:S-000:missing")

    requirements = _requirement_ids(bank)
    decision_ids = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "DECISION"
    }
    evidence_ids = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "EVIDENCE"
    }
    task_docs: dict[str, dict[str, Any]] = {}
    task_stage: dict[str, str] = {}
    check_docs: dict[str, dict[str, Any]] = {}
    check_stage: dict[str, str] = {}
    future_not_run: dict[str, dict[str, Any]] = {}
    input_contracts: dict[str, dict[str, Any]] = {}
    input_contract_stage: dict[str, str] = {}
    output_contracts: dict[str, dict[str, Any]] = {}
    output_contract_stage: dict[str, str] = {}
    output_paths: dict[str, str] = {}
    local_order_edges: list[tuple[str, str]] = []
    stage_predecessors: dict[str, set[str]] = {}
    stage_next: dict[str, set[str]] = {}

    for stage_id, stage in sorted(stage_docs.items()):
        prefix = f"stage:{stage_id}"
        if "architecture_ref" in stage:
            errors.extend(local_plan_errors(stage,prefix))
        section_index = stage.get("sections")
        if not isinstance(section_index, list) or set(section_index) != set(_STAGE_SECTIONS):
            errors.append(prefix + ":section_index")
        for number, field in _STAGE_SECTIONS.items():
            value = stage.get(field)
            if field in {"tasks", "acceptance_exit"}:
                valid = isinstance(value, list) and bool(value)
            else:
                valid = isinstance(value, dict) and bool(value)
            if not valid:
                errors.append(prefix + f":missing_named_section:{number}:{field}")

        identity = stage.get("identity_result")
        if not isinstance(identity, dict):
            identity = {}
        if (
            identity.get("stage_id") != stage_id
            or identity.get("plan_version") != stage.get("plan_version")
            or not _text(identity.get("owner"))
            or not _strings(identity.get("requirements"), nonempty=True)
            or not _strings(identity.get("decisions"), nonempty=True)
            or not _text(identity.get("accepted_result"))
        ):
            errors.append(prefix + ":identity_contract")
        for requirement in _ids(identity.get("requirements")):
            if requirement not in requirements:
                errors.append(prefix + f":identity_unknown_r_ref:{requirement}")
        for decision in _ids(identity.get("decisions")):
            if decision not in decision_ids:
                errors.append(prefix + f":identity_unknown_d_ref:{decision}")
        if not _text(stage.get("owner")) or not _text(stage.get("result")):
            errors.append(prefix + ":owner_result")

        graph = stage.get("local_execution_graph")
        if not isinstance(graph, dict):
            graph = {}
        actual_to_logical={actual:logical for actual,logical in result_to_logical.items()}
        predecessors = {actual_to_logical.get(value,value) for value in _ids(graph.get("predecessors"))}
        next_stages = {actual_to_logical.get(value,value) for value in _ids(graph.get("next_stages"))}
        stage_predecessors[stage_id] = predecessors
        stage_next[stage_id] = next_stages
        entry = graph.get("entry")
        exit_id = graph.get("exit")
        first_task = graph.get("first_task")
        if not all(_text(value) for value in (entry, exit_id, graph.get("initial_state"), graph.get("write_conflict_policy"))):
            errors.append(prefix + ":entry_exit_contract")
        if not isinstance(graph.get("parallel_waves"), list):
            errors.append(prefix + ":parallel_waves")
        bounded = graph.get("bounded_iterations")
        if not isinstance(bounded, list):
            errors.append(prefix + ":bounded_iterations")
            bounded = []
        for index, item in enumerate(bounded):
            required = {"id", "start", "step", "bound", "stop", "failure_action"}
            if not isinstance(item, dict) or not required.issubset(item) or any(not _text(item.get(key)) for key in required):
                errors.append(prefix + f":bounded_iteration:{index}")

        section = stage.get("errors_recovery_evidence")
        if not isinstance(section, dict):
            section = {}
        for field in ("retry", "idempotency", "compensation", "checkpoint"):
            if not _text(section.get(field)):
                errors.append(prefix + f":recovery_{field}")
        for field in ("existing", "future_not_run", "drift_recheck"):
            if not isinstance(section.get(field), list):
                errors.append(prefix + f":recovery_{field}_type")
        for evidence_id in _ids(section.get("existing")):
            if evidence_id not in evidence_ids:
                errors.append(prefix + f":unknown_existing_evidence:{evidence_id}")
        for index, item in enumerate(section.get("future_not_run", []) if isinstance(section.get("future_not_run"), list) else []):
            required = {"id", "task_id", "check_id", "status", "condition", "next_action"}
            if (
                not isinstance(item, dict)
                or not required.issubset(item)
                or item.get("status") != "NOT_RUN"
                or not all(_text(item.get(key)) for key in ("id", "task_id", "check_id", "condition", "next_action"))
            ):
                errors.append(prefix + f":future_not_run:{index}")
            elif item["id"] in future_not_run:
                errors.append(prefix + f":duplicate_future_not_run:{item['id']}")
            else:
                future_not_run[item["id"]] = item

        tasks = stage.get("tasks") if isinstance(stage.get("tasks"), list) else []
        local_ids: set[str] = set()
        for index, task in enumerate(tasks):
            task_id = task.get("id") if isinstance(task, dict) else None
            address = str(task_id) if isinstance(task_id, str) and task_id else f"index-{index}"
            task_prefix = f"task:{address}"
            if not isinstance(task, dict) or not _TASK_REQUIRED.issubset(task):
                errors.append(task_prefix + ":contract_fields")
                continue
            if task_id in task_docs:
                errors.append(task_prefix + ":duplicate_owner")
                continue
            task_docs[task_id] = task
            task_stage[task_id] = stage_id
            local_ids.add(task_id)
            if not all(_text(task.get(field)) for field in ("owner", "result", "procedure", "errors_recovery", "branch_guard")):
                errors.append(task_prefix + ":substantive_fields")
            if task.get("execution_status") not in _TASK_STATUS:
                errors.append(task_prefix + ":execution_status")
            for field in ("algorithms", "inputs", "dependencies", "outputs", "acceptance_criteria", "checks", "r_refs", "d_refs", "e_refs", "writes", "serialized_with"):
                if not isinstance(task.get(field), list):
                    errors.append(task_prefix + f":{field}_type")
            for field in ("outputs", "acceptance_criteria", "checks", "r_refs", "d_refs", "writes"):
                if not _ids(task.get(field)):
                    errors.append(task_prefix + f":empty_{field}")
            if not (
                _text(task.get("resources"))
                or isinstance(task.get("resources"), (dict, list)) and bool(task.get("resources"))
            ):
                errors.append(task_prefix + ":resources")

        edge_values = graph.get("edges")
        local_edges: list[tuple[str, str]] = []
        if not isinstance(edge_values, list) or not edge_values:
            errors.append(prefix + ":local_edges")
        else:
            for index, value in enumerate(edge_values):
                pair = _edge_pair(value)
                if pair is None:
                    errors.append(prefix + f":local_edge:{index}")
                else:
                    local_edges.append(pair)
        endpoints = local_ids | ({entry, exit_id} if isinstance(entry, str) and isinstance(exit_id, str) else set())
        for source, target in local_edges:
            if source not in endpoints or target not in endpoints:
                errors.append(prefix + f":local_edge_endpoint:{source}->{target}")
            if source in local_ids and target in local_ids:
                local_order_edges.append((source, target))
        if local_edges and not _acyclic(endpoints, local_edges):
            errors.append(prefix + ":local_cycle")
        if isinstance(entry, str) and isinstance(exit_id, str):
            reached = _reachable(local_edges, entry)
            if exit_id not in reached or not local_ids.issubset(reached):
                errors.append(prefix + ":local_reachability")
            can_reach_exit = _reachable(
                ((target, source) for source, target in local_edges),
                exit_id,
            )
            for task_id in sorted(local_ids - can_reach_exit):
                errors.append(prefix + f":task_cannot_reach_exit:{task_id}")
        if first_task not in local_ids:
            errors.append(prefix + ":first_task")

        checks = stage.get("acceptance_exit") if isinstance(stage.get("acceptance_exit"), list) else []
        for index, check in enumerate(checks):
            check_id = check.get("id") if isinstance(check, dict) else None
            address = str(check_id) if isinstance(check_id, str) and check_id else f"index-{index}"
            check_prefix = f"check:{address}"
            if not isinstance(check, dict) or not _CHECK_REQUIRED.issubset(check):
                errors.append(check_prefix + ":contract_fields")
                continue
            if check_id in check_docs:
                errors.append(check_prefix + ":duplicate_owner")
                continue
            check_docs[check_id] = check
            check_stage[check_id] = stage_id
            if check.get("task_id") not in local_ids:
                errors.append(check_prefix + f":unknown_local_task:{check.get('task_id')}")
            for field in ("inputs", "preconditions"):
                if not isinstance(check.get(field), list):
                    errors.append(check_prefix + f":{field}_type")
            for field in ("property", "environment", "procedure", "expected_observation", "negative_check", "boundary_check", "sensitivity_check", "checker"):
                if not _text(check.get(field)):
                    errors.append(check_prefix + f":empty_{field}")

        contracts = stage.get("inputs_contracts")
        if (
            not isinstance(contracts, dict)
            or not isinstance(contracts.get("inputs"), list)
            or not contracts.get("inputs")
            or not isinstance(contracts.get("outputs"), list)
            or not contracts.get("outputs")
        ):
            errors.append(prefix + ":inputs_contracts")
        elif isinstance(contracts, dict):
            for index, contract in enumerate(contracts["inputs"]):
                contract_prefix = prefix + f":input_contract:{index}"
                required = {"id", "input_kind", "contract", "consumer_ids"}
                if (
                    not isinstance(contract, dict)
                    or not required.issubset(contract)
                    or not _text(contract.get("id"))
                    or contract.get("input_kind") not in _INPUT_KINDS
                    or not _text(contract.get("contract"))
                    or not _strings(contract.get("consumer_ids"), nonempty=True)
                ):
                    errors.append(contract_prefix + ":fields")
                    continue
                contract_id = contract["id"]
                if contract_id in input_contracts:
                    errors.append(contract_prefix + f":duplicate_id:{contract_id}")
                else:
                    input_contracts[contract_id] = contract
                    input_contract_stage[contract_id] = stage_id
            for index, contract in enumerate(contracts["outputs"]):
                contract_prefix = prefix + f":output_contract:{index}"
                required = {
                    "id", "path", "producer_task_id", "acceptance_check_id",
                    "consumer_ids", "contract", "availability", "branch_guard",
                    "acceptance_status",
                }
                if (
                    not isinstance(contract, dict)
                    or not required.issubset(contract)
                    or not _text(contract.get("id"))
                    or not _text(contract.get("path"))
                    or not _strings(contract.get("consumer_ids"))
                    or not _text(contract.get("contract"))
                    or contract.get("availability") not in _OUTPUT_AVAILABILITY
                    or contract.get("acceptance_status") not in _ACCEPTANCE_STATUS
                    or not _text(contract.get("branch_guard"))
                ):
                    errors.append(contract_prefix + ":fields")
                    continue
                contract_id = contract["id"]
                if contract_id in output_contracts:
                    errors.append(contract_prefix + f":duplicate_id:{contract_id}")
                elif contract["path"] in output_paths:
                    errors.append(contract_prefix + f":duplicate_path:{contract['path']}")
                else:
                    output_contracts[contract_id] = contract
                    output_contract_stage[contract_id] = stage_id
                    output_paths[contract["path"]] = contract_id
        handoff = stage.get("outputs_handoff")
        if (
            not isinstance(handoff, dict)
            or not _strings(handoff.get("artifacts"), nonempty=True)
            or not isinstance(handoff.get("consumers"), list)
            or not isinstance(handoff.get("allowed_next_stages"), list)
            or not isinstance(handoff.get("forbidden_transitions"), list)
        ):
            errors.append(prefix + ":outputs_handoff")
        resources = stage.get("resources_stop")
        if not isinstance(resources, dict) or any(not _text(resources.get(field)) for field in ("budget", "estimate_basis", "monitoring", "stop_action")):
            errors.append(prefix + ":resources_stop")

    stage_ids = set(stage_docs)
    plan_versions = [stage.get("plan_version") for stage in stage_docs.values()]
    if plan_versions and (
        any(not _text(version) for version in plan_versions)
        or len(set(plan_versions)) != 1
    ):
        errors.append("stage_graph:plan_version_mismatch")
    stage_edges: set[tuple[str, str]] = set()
    for stage_id in sorted(stage_ids):
        for predecessor in sorted(stage_predecessors.get(stage_id, set())):
            if predecessor not in stage_ids:
                errors.append(f"stage:{stage_id}:unknown_predecessor:{predecessor}")
            else:
                stage_edges.add((predecessor, stage_id))
                if stage_id not in stage_next.get(predecessor, set()):
                    errors.append(f"stage:{stage_id}:predecessor_not_reciprocal:{predecessor}")
        for next_stage in sorted(stage_next.get(stage_id, set())):
            if next_stage not in stage_ids:
                errors.append(f"stage:{stage_id}:unknown_next:{next_stage}")
            else:
                stage_edges.add((stage_id, next_stage))
                if stage_id not in stage_predecessors.get(next_stage, set()):
                    errors.append(f"stage:{stage_id}:next_not_reciprocal:{next_stage}")
    if "S-000" in stage_ids and stage_predecessors.get("S-000"):
        errors.append("stage:S-000:has_predecessor")
    if stage_ids and not _acyclic(stage_ids, stage_edges):
        errors.append("stage_graph:cycle")
    if "S-000" in stage_ids:
        unreachable = stage_ids - _reachable(stage_edges, "S-000")
        for stage_id in sorted(unreachable):
            errors.append(f"stage_graph:unreachable:{stage_id}")
    stage_reach = {
        stage_id: _reachable(stage_edges, stage_id)
        for stage_id in stage_ids
    }

    task_ids = set(task_docs)
    task_edges: set[tuple[str, str]] = set(normalize_plan_order(stage_docs, task_docs)["task_edges"])
    for task_id, task in sorted(task_docs.items()):
        for dependency in _ids(task.get("dependencies")):
            if dependency not in task_ids:
                errors.append(f"task:{task_id}:unknown_dependency:{dependency}")
            else:
                task_edges.add((dependency, task_id))
                dependency_stage = task_stage[dependency]
                consumer_stage = task_stage[task_id]
                if (
                    dependency_stage != consumer_stage
                    and consumer_stage not in stage_reach[dependency_stage]
                ):
                    errors.append(
                        f"task:{task_id}:dependency_reverses_stage_order:{dependency}"
                    )
        for requirement in _ids(task.get("r_refs")):
            if requirement not in requirements:
                errors.append(f"task:{task_id}:unknown_r_ref:{requirement}")
        for decision in _ids(task.get("d_refs")):
            if decision not in decision_ids:
                errors.append(f"task:{task_id}:unknown_d_ref:{decision}")
        for evidence in _ids(task.get("e_refs")):
            if evidence not in evidence_ids and evidence not in future_not_run:
                errors.append(f"task:{task_id}:unknown_e_ref:{evidence}")
        check_refs = set(_ids(task.get("checks")))
        acceptance_refs = set(_ids(task.get("acceptance_criteria")))
        if check_refs != acceptance_refs:
            errors.append(f"task:{task_id}:check_acceptance_mismatch")
        for check_id in sorted(check_refs | acceptance_refs):
            if check_id not in check_docs:
                errors.append(f"task:{task_id}:unknown_c_ref:{check_id}")
            elif check_docs[check_id].get("task_id") != task_id:
                errors.append(f"task:{task_id}:foreign_c_ref:{check_id}")
    for future_id, item in sorted(future_not_run.items()):
        if item.get("task_id") not in task_docs:
            errors.append(f"future_not_run:{future_id}:unknown_task:{item.get('task_id')}")
        if item.get("check_id") not in check_docs:
            errors.append(f"future_not_run:{future_id}:unknown_check:{item.get('check_id')}")
    if task_ids and not _acyclic(task_ids, task_edges):
        errors.append("task_graph:cycle")

    order_reach = {task_id: _reachable(task_edges, task_id) for task_id in task_ids}

    def consumer_task(consumer_id: str) -> str | None:
        if consumer_id in task_docs:
            return consumer_id
        check = check_docs.get(consumer_id)
        return check.get("task_id") if isinstance(check, dict) else None

    for task_id, task in sorted(task_docs.items()):
        for input_id in _ids(task.get("inputs")):
            binding = resolve_plan_input(input_id, input_contracts, output_contracts, output_paths)
            if binding is None:
                errors.append(f"task:{task_id}:unknown_input:{input_id}")
                continue
            contract = binding["contract"]
            if binding["reference_kind"] == "INPUT" and task_id not in _ids(contract.get("consumer_ids")):
                errors.append(f"task:{task_id}:input_consumer_binding:{input_id}")
            if binding["reference_kind"] == "OUTPUT" and task_id not in _ids(contract.get("consumer_ids")):
                errors.append(f"task:{task_id}:output_consumer_binding:{binding['contract_id']}")
    for check_id, check in sorted(check_docs.items()):
        for input_id in _ids(check.get("inputs")):
            binding = resolve_plan_input(input_id, input_contracts, output_contracts, output_paths)
            if binding is None:
                errors.append(f"check:{check_id}:unknown_input:{input_id}")
                continue
            contract = binding["contract"]
            if binding["reference_kind"] == "INPUT" and check_id not in _ids(contract.get("consumer_ids")):
                errors.append(f"check:{check_id}:input_consumer_binding:{input_id}")
            if (
                binding["reference_kind"] == "OUTPUT"
                and check_id != contract.get("acceptance_check_id")
                and check_id not in _ids(contract.get("consumer_ids"))
            ):
                errors.append(f"check:{check_id}:output_consumer_binding:{binding['contract_id']}")

    for contract_id, contract in sorted(output_contracts.items()):
        prefix = f"output_contract:{contract_id}"
        producer_id = contract.get("producer_task_id")
        check_id = contract.get("acceptance_check_id")
        consumers = _ids(contract.get("consumer_ids"))
        if producer_id not in task_docs:
            errors.append(prefix + f":unknown_producer:{producer_id}")
        elif contract.get("path") not in _ids(task_docs[producer_id].get("outputs")):
            errors.append(prefix + ":producer_output_binding")
        if check_id not in check_docs or check_docs.get(check_id, {}).get("task_id") != producer_id:
            errors.append(prefix + f":acceptance_check_binding:{check_id}")
        acceptance_status = contract.get("acceptance_status")
        acceptance_evidence_id = contract.get("acceptance_evidence_id")
        acceptance_evidence = results.get(acceptance_evidence_id)
        if acceptance_status == "ACCEPTED":
            if (
                not isinstance(acceptance_evidence_id, str)
                or not isinstance(acceptance_evidence, dict)
                or acceptance_evidence.get("artifact_type") != "EVIDENCE"
            ):
                errors.append(prefix + ":acceptance_evidence_binding")
        elif "acceptance_evidence_id" in contract:
            errors.append(prefix + ":planned_has_acceptance_evidence")
        availability = contract.get("availability")
        if availability == "SELECTED_BRANCH":
            branch_evidence_id = contract.get("branch_selection_evidence_id")
            branch_evidence = results.get(branch_evidence_id)
            if contract.get("branch_selection_status") != "SELECTED":
                errors.append(prefix + ":selected_branch_status")
            if (
                not isinstance(branch_evidence_id, str)
                or not isinstance(branch_evidence, dict)
                or branch_evidence.get("artifact_type") != "EVIDENCE"
            ):
                errors.append(prefix + ":branch_selection_evidence_binding")
        elif "branch_selection_status" in contract or "branch_selection_evidence_id" in contract:
            errors.append(prefix + ":branch_selection_fields_not_applicable")
        if availability == "UNSELECTED_BRANCH":
            if consumers:
                errors.append(prefix + ":unselected_has_consumers")
            referenced = any(
                contract_id in _ids(doc.get("inputs")) or contract.get("path") in _ids(doc.get("inputs"))
                for doc in list(task_docs.values()) + list(check_docs.values())
            )
            if referenced:
                errors.append(prefix + ":unselected_output_referenced")
        for consumer_id in consumers:
            target_task = consumer_task(consumer_id)
            if target_task is None:
                errors.append(prefix + f":unknown_consumer:{consumer_id}")
            elif producer_id in task_docs and target_task not in order_reach.get(producer_id, set()):
                errors.append(prefix + f":consumer_before_acceptance:{consumer_id}")
            elif (
                producer_id in task_docs
                and task_docs[target_task].get("execution_status") in {"RUNNING", "ACCEPTED"}
            ):
                if task_docs[producer_id].get("execution_status") != "ACCEPTED":
                    errors.append(prefix + f":active_consumer_without_accepted_producer:{consumer_id}")
                if acceptance_status != "ACCEPTED":
                    errors.append(prefix + f":active_consumer_without_accepted_output:{consumer_id}")

    for contract_id, contract in sorted(input_contracts.items()):
        prefix = f"input_contract:{contract_id}"
        kind = contract.get("input_kind")
        consumers = _ids(contract.get("consumer_ids"))
        for consumer_id in consumers:
            if consumer_task(consumer_id) is None:
                errors.append(prefix + f":unknown_consumer:{consumer_id}")
        if kind == "EXISTING":
            required = {"source_id", "version", "identity", "accepted_hash"}
            source_id = contract.get("source_id")
            source = results.get(source_id) if isinstance(source_id, str) else None
            expected_hash = (
                source.get("content_hash") if isinstance(source, dict)
                else immutable_inputs.get(source_id) if isinstance(source_id, str) else None
            )
            if not required.issubset(contract) or not _text(contract.get("version")) or not _text(contract.get("identity")):
                errors.append(prefix + ":existing_fields")
            if expected_hash is None or contract.get("accepted_hash") != expected_hash:
                errors.append(prefix + f":existing_source_binding:{source_id}")
        elif kind == "PLANNED":
            required = {"producer_task_id", "output_id", "acceptance_check_id"}
            producer_id = contract.get("producer_task_id")
            output_id = contract.get("output_id")
            check_id = contract.get("acceptance_check_id")
            if not required.issubset(contract) or "accepted_hash" in contract:
                errors.append(prefix + ":planned_fields_or_future_hash")
            output = output_contracts.get(output_id)
            if (
                producer_id not in task_docs
                or not isinstance(output, dict)
                or output.get("producer_task_id") != producer_id
                or output.get("acceptance_check_id") != check_id
                or check_id not in check_docs
                or check_docs.get(check_id, {}).get("task_id") != producer_id
            ):
                errors.append(prefix + f":planned_chain:{producer_id}:{output_id}:{check_id}")
            elif not set(consumers).issubset(set(_ids(output.get("consumer_ids")))):
                errors.append(prefix + ":planned_output_consumer_binding")
            for consumer_id in consumers:
                target_task = consumer_task(consumer_id)
                if producer_id in task_docs and target_task is not None and target_task not in order_reach.get(producer_id, set()):
                    errors.append(prefix + f":consumer_before_acceptance:{consumer_id}")
        elif kind == "EXTERNAL":
            required = {
                "source", "owner", "availability_condition",
                "availability_status", "blocked_consumer_ids",
            }
            blocked = contract.get("blocked_consumer_ids")
            if (
                not required.issubset(contract)
                or any(not _text(contract.get(field)) for field in ("source", "owner", "availability_condition"))
                or contract.get("availability_status") not in _EXTERNAL_AVAILABILITY
                or not _strings(blocked)
            ):
                errors.append(prefix + ":external_fields")
                blocked = []
            if not set(_ids(blocked)).issubset(set(consumers)):
                errors.append(prefix + ":blocked_consumers_not_subset")
            if contract.get("availability_status") == "UNAVAILABLE":
                if set(_ids(blocked)) != set(consumers):
                    errors.append(prefix + ":unavailable_consumers_not_blocked")
                if "availability_evidence_id" in contract:
                    errors.append(prefix + ":unavailable_has_availability_evidence")
                for consumer_id in consumers:
                    target_task = consumer_task(consumer_id)
                    if (
                        target_task in task_docs
                        and task_docs[target_task].get("execution_status")
                        in {"RUNNING", "ACCEPTED"}
                    ):
                        errors.append(
                            prefix + f":unavailable_consumer_executed:{consumer_id}"
                        )
            elif contract.get("availability_status") == "AVAILABLE":
                evidence_id = contract.get("availability_evidence_id")
                evidence = results.get(evidence_id)
                if (
                    not isinstance(evidence_id, str)
                    or not isinstance(evidence, dict)
                    or evidence.get("artifact_type") != "EVIDENCE"
                ):
                    errors.append(prefix + ":availability_evidence_binding")
                if _ids(blocked):
                    errors.append(prefix + ":available_has_blocked_consumers")
            for consumer_id in _ids(blocked):
                target_task = consumer_task(consumer_id)
                if target_task in task_docs and task_docs[target_task].get("execution_status") in {"RUNNING", "ACCEPTED"}:
                    errors.append(prefix + f":blocked_consumer_executed:{consumer_id}")
    writers: dict[str, list[str]] = defaultdict(list)
    for task_id, task in task_docs.items():
        for path in _ids(task.get("writes")):
            writers[path].append(task_id)
    for path, owners in sorted(writers.items()):
        if len(owners) < 2:
            continue
        for index, left in enumerate(sorted(owners)):
            for right in sorted(owners)[index + 1:]:
                declared = right in _ids(task_docs[left].get("serialized_with")) or left in _ids(task_docs[right].get("serialized_with"))
                ordered = right in order_reach[left] or left in order_reach[right]
                if not declared or not ordered:
                    errors.append(f"write_conflict:{path}:{left}:{right}")

    for requirement in sorted(requirements):
        closed = False
        for task_id, task in task_docs.items():
            if requirement not in _ids(task.get("r_refs")):
                continue
            decisions_ok = bool(_ids(task.get("d_refs"))) and all(item in decision_ids for item in _ids(task.get("d_refs")))
            evidence_ok = bool(_ids(task.get("e_refs"))) and all(
                item in evidence_ids or item in future_not_run for item in _ids(task.get("e_refs"))
            )
            checks_ok = bool(_ids(task.get("checks"))) and all(
                item in check_docs and check_docs[item].get("task_id") == task_id for item in _ids(task.get("checks"))
            )
            if decisions_ok and evidence_ok and checks_ok:
                closed = True
                break
        if not closed:
            errors.append(f"requirement:{requirement}:trace_R_D_T_C_E_or_NOT_RUN")
    return task_ids, set(check_docs), stage_docs


def _validate_dependency_graph(
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    task_ids: set[str],
    check_ids: set[str],
    administrative_reviews: set[str],
    iceberg_anchor: dict[str, Any] | None,
    iceberg_enabled: bool,
    historical_iceberg_views: set[str],
    errors: list[str],
) -> None:
    semantic_edges: list[dict[str, Any]] = []
    provenance_edges: list[dict[str, Any]] = []
    execution_edges: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    active_semantic_docs = _active_iceberg_semantic_docs(docs, historical_iceberg_views)
    for result_id, doc in docs.items():
        if (
            result_id in active_semantic_docs
            and isinstance(doc.get("semantic_edges"), list)
        ):
            semantic_edges.extend(doc["semantic_edges"])
        if result_id in active_semantic_docs:
            if isinstance(doc.get("evidence_provenance_edges"), list):
                provenance_edges.extend(doc["evidence_provenance_edges"])
            if isinstance(doc.get("execution_edges"), list):
                execution_edges.extend(doc["execution_edges"])
            if isinstance(doc.get("node_status"), dict):
                statuses.update({str(key): str(value) for key, value in doc["node_status"].items()})
    if not semantic_edges:
        errors.append("semantic_graph:missing")
    if not execution_edges:
        errors.append("execution_graph:missing")
        return
    accepted_reviews = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "REVIEW"
        and result_id not in historical_iceberg_views
    }
    execution_valid = {
        "E": {result_id for result_id, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "EVIDENCE"},
        "Q": _question_ids(bank, docs),
        "D": {result_id for result_id, entry in results.items() if isinstance(entry, dict) and entry.get("artifact_type") == "DECISION"},
        "REVIEW": accepted_reviews - administrative_reviews,
        "T": task_ids,
        "C": check_ids,
    }
    if iceberg_enabled:
        execution_valid["H"] = {
            result_id for result_id, entry in results.items()
            if isinstance(entry, dict) and entry.get("artifact_type") == "HYPOTHESIS"
        }
    semantic_valid = dict(execution_valid)
    if iceberg_enabled:
        semantic_valid["G"] = (
            {iceberg_anchor["vector_id"]} if isinstance(iceberg_anchor, dict) else set()
        )
        semantic_valid["R"] = _requirement_ids(bank)

    def typed_edge(
        edge: Any,
        prefix: str,
        index: int,
        valid: dict[str, set[str]],
    ) -> tuple[str, str] | None:
        if not isinstance(edge, dict):
            errors.append(f"{prefix}:index-{index}:contract")
            return None
        source_type = edge.get("from_type")
        target_type = edge.get("to_type")
        source_id = edge.get("from_id")
        target_id = edge.get("to_id")
        address = f"{source_type}:{source_id}->{target_type}:{target_id}"
        if source_type not in valid or target_type not in valid or not isinstance(source_id, str) or not isinstance(target_id, str):
            errors.append(prefix + ":" + address + ":contract")
            return None
        if source_id not in valid[source_type]:
            errors.append(prefix + ":" + address + ":missing_from")
        if target_id not in valid[target_type]:
            errors.append(prefix + ":" + address + ":missing_to")
        if source_id not in valid[source_type] or target_id not in valid[target_type]:
            return None
        return f"{source_type}:{source_id}", f"{target_type}:{target_id}"

    for index, edge in enumerate(semantic_edges):
        typed_edge(edge, "semantic_edge", index, semantic_valid)

    provenance_pairs: list[tuple[str, str]] = []
    declared_provenance: set[tuple[str, str]] = set()
    provenance_seen: set[str] = set()
    evidence_nodes = execution_valid["E"]
    for index, edge in enumerate(provenance_edges):
        source_id = edge.get("from_id") if isinstance(edge, dict) else None
        target_id = edge.get("to_id") if isinstance(edge, dict) else None
        address = f"E:{source_id}->E:{target_id}"
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            errors.append(f"evidence_provenance_edge:index-{index}:contract")
            continue
        if source_id not in evidence_nodes:
            errors.append("evidence_provenance_edge:" + address + ":missing_from")
        if target_id not in evidence_nodes:
            errors.append("evidence_provenance_edge:" + address + ":missing_to")
        if source_id in evidence_nodes and target_id in evidence_nodes:
            pair = (source_id, target_id)
            if pair in declared_provenance:
                errors.append("evidence_provenance_edge:" + address + ":duplicate")
            declared_provenance.add(pair)
            provenance_seen.update({source_id, target_id})
    source_ref_provenance: set[tuple[str, str]] = set()
    for target_id in sorted(evidence_nodes):
        target_doc = docs.get(target_id)
        if not isinstance(target_doc, dict):
            continue
        for source_id in _ids(target_doc.get("source_refs")):
            if source_id not in evidence_nodes:
                continue
            pair = (source_id, target_id)
            if source_id == target_id:
                errors.append(f"evidence_source_ref:E:{source_id}->E:{target_id}:self_reference")
            source_ref_provenance.add(pair)
    for source_id, target_id in sorted(source_ref_provenance - declared_provenance):
        errors.append(
            f"evidence_source_ref:E:{source_id}->E:{target_id}:missing_provenance_edge"
        )
    for source_id, target_id in sorted(declared_provenance - source_ref_provenance):
        errors.append(
            f"evidence_provenance_edge:E:{source_id}->E:{target_id}:missing_source_ref"
        )
    for source_id, target_id in sorted(source_ref_provenance):
        provenance_pairs.append((f"E:{source_id}", f"E:{target_id}"))
        provenance_seen.update({source_id, target_id})
    provenance_nodes = {f"E:{item}" for item in evidence_nodes}
    if provenance_pairs and not _acyclic(provenance_nodes, provenance_pairs):
        errors.append("evidence_provenance_graph:cycle")

    seen: dict[str, set[str]] = {kind: set() for kind in execution_valid}
    seen["E"].update(provenance_seen)
    execution_pairs: list[tuple[str, str]] = []
    for index, edge in enumerate(execution_edges):
        pair = typed_edge(edge, "execution_edge", index, execution_valid)
        if pair is None:
            continue
        source, target = pair
        source_type, source_id = source.split(":", 1)
        target_type, target_id = target.split(":", 1)
        if source_type == target_type == "E":
            errors.append(f"execution_edge:{source}->{target}:use_evidence_provenance")
            continue
        seen[source_type].add(source_id)
        seen[target_type].add(target_id)
        execution_pairs.append(pair)
    for kind in execution_valid:
        for entity_id in sorted(execution_valid[kind] - seen[kind]):
            errors.append(f"execution_graph:unaddressed:{kind}:{entity_id}")
    execution_nodes = {
        f"{kind}:{entity_id}"
        for kind, entity_ids in execution_valid.items()
        for entity_id in entity_ids
    }
    if execution_pairs and not _acyclic(execution_nodes, execution_pairs):
        errors.append("execution_graph:cycle")

    changed = [
        f"E:{result_id}"
        for result_id in execution_valid["E"]
        if docs.get(result_id, {}).get("changed") == "CHANGED"
    ]
    for result_id in sorted(execution_valid["E"]):
        if docs.get(result_id, {}).get("changed") not in {"CHANGED", "UNCHANGED"}:
            errors.append(f"evidence:{result_id}:changed_enum")
    graph: dict[str, set[str]] = defaultdict(set)
    for source, target in provenance_pairs + execution_pairs:
        graph[source].add(target)
    stale = set(changed)
    queue = deque(changed)
    while queue:
        node = queue.popleft()
        for target in graph[node]:
            if target not in stale:
                stale.add(target)
                queue.append(target)
    for node in sorted(stale):
        if node not in changed and statuses.get(node) != "NEEDS_RECHECK":
            errors.append(f"dependency_graph:stale_not_propagated:{node}")


def _gate_path(
    run: Path,
    gate_id: str,
    results: dict[str, Any],
    docs: dict[str, Any],
    current_final_audit_id: str | None,
) -> Path:
    """Resolve GATE-6 against the selected immutable audit revision.

    The unversioned file is a compatibility path only while the accepted
    FINAL_AUDIT registry has one member.  Once history exists, absence of the
    selected versioned gate fails closed instead of silently reading stale
    canonical bytes.
    """
    canonical = run / "gates" / f"{gate_id}.yaml"
    if gate_id != "GATE-6" or current_final_audit_id is None:
        return canonical
    selected = run / "gates" / gate_id / f"{current_final_audit_id}.yaml"
    if selected.is_file():
        return selected
    accepted_audits = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "FINAL_AUDIT"
        and isinstance(docs.get(result_id), dict)
        and docs[result_id].get("workflow_status") == "ACCEPTED"
    }
    if accepted_audits == {current_final_audit_id}:
        return canonical
    return selected


def _administrative_final_reviews(
    run: Path,
    results: dict[str, Any],
    docs: dict[str, Any],
    phase: str,
    load_yaml: Callable[[Path], Any],
    current_final_audit_id: str | None,
) -> set[str]:
    candidates: set[str] = set()
    for review_id, entry in results.items():
        review = docs.get(review_id)
        target_id = review.get("target_result_id") if isinstance(review, dict) else None
        target = results.get(target_id) if isinstance(target_id, str) else None
        if (
            isinstance(entry, dict)
            and entry.get("artifact_type") == "REVIEW"
            and isinstance(target, dict)
            and target.get("artifact_type") == "FINAL_AUDIT"
            and (current_final_audit_id is None or target_id == current_final_audit_id)
        ):
            candidates.add(review_id)
    if phase == "core":
        return candidates
    try:
        gate = load_yaml(_gate_path(
            run, "GATE-6", results, docs, current_final_audit_id
        ))
    except Exception:
        return set()
    content_review = gate.get("content_review") if isinstance(gate, dict) else None
    review_id = content_review.get("result_id") if isinstance(content_review, dict) else None
    return {review_id} if review_id in candidates else set()


def _gate_common(gate: dict[str, Any], gate_id: str, snapshot: str | None) -> bool:
    required = {
        "schema_version", "id", "run_id", "input_snapshot", "author_role",
        "agent_id", "knowledge_status", "workflow_status", "source_refs",
        "related_ids", "revision", "created_at", "updated_at", "gate_id",
        "required_objects", "completed_checks", "content_review",
        "unresolved_critical", "decision", "allowed_next",
        "blocked_dependencies", "evidence_refs",
    }
    return (
        set(gate) >= required
        and gate.get("id") == gate_id
        and gate.get("gate_id") == gate_id
        and (snapshot is None or gate.get("input_snapshot") == snapshot)
        and gate.get("workflow_status") == "ACCEPTED"
        and _strings(gate.get("source_refs"))
        and _strings(gate.get("related_ids"))
    )


def _validate_gates(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    immutable_inputs: dict[str, str],
    phase: str,
    load_yaml: Callable[[Path], Any],
    valid_reviews: set[str],
    synthesis_ids: set[str],
    plan_projection: dict[str, str] | None,
    current_source_outcomes_id: str | None,
    current_final_audit_id: str | None,
    iceberg_projection: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    snapshots = {
        entry.get("input_snapshot") for entry in results.values()
        if isinstance(entry, dict) and isinstance(entry.get("input_snapshot"), str)
    }
    snapshot = next(iter(snapshots)) if len(snapshots) == 1 else None
    review_use: dict[str, str] = {}
    last_gate = 5 if phase == "core" else 6
    framing_ids = {
        result_id for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "FRAMING"
        and entry.get("role_id") in {f"P-{number:02d}" for number in range(1, 11)}
    }
    framing_vector_ids = {
        docs[result_id].get("vector_result_id")
        for result_id in framing_ids
        if isinstance(docs.get(result_id), dict)
        and isinstance(docs[result_id].get("vector_result_id"), str)
    }
    vector_anchor, gate_vector_ids = _resolve_vector_anchor(
        run, results, docs, immutable_inputs, load_yaml, errors
    )
    vector_ids = (
        {vector_anchor["vector_id"]}
        if isinstance(vector_anchor, dict)
        and vector_anchor.get("vector_id") in framing_vector_ids
        else set()
    )
    source_answer_ids: set[str] = set()
    source_doc = docs.get(current_source_outcomes_id) if current_source_outcomes_id else None
    if isinstance(source_doc, dict):
        for record in source_doc.get("records", []):
            if isinstance(record, dict) and record.get("record_kind") == "SOURCE_OUTCOME":
                answer_id = record.get("answer_result_id")
                if isinstance(answer_id, str):
                    source_answer_ids.add(answer_id)
    normative_reviews = {
        review_id for review_id in valid_reviews
        if isinstance(docs.get(review_id), dict)
        and isinstance(results.get(docs[review_id].get("target_result_id")), dict)
        and results[docs[review_id]["target_result_id"]].get("artifact_type") in {"PRIMARY", "DECISION"}
    }
    iceberg_review_ids = {
        review_id for review_id in valid_reviews
        if isinstance(docs.get(review_id), dict)
        and results.get(docs[review_id].get("target_result_id"), {}).get("artifact_type") == "HYPOTHESIS"
    }
    derived_evidence_ids: set[str] = set()
    if isinstance(source_doc, dict):
        for record in source_doc.get("records", []):
            if isinstance(record, dict) and record.get("record_kind") == "DERIVED_Q":
                derived_evidence_ids.update(
                    item for item in _ids(record.get("evidence_ids"))
                    if results.get(item, {}).get("artifact_type") == "EVIDENCE"
                )
    projected_plan_ids = (
        set(plan_projection) if isinstance(plan_projection, dict)
        else {
            result_id for result_id, entry in results.items()
            if isinstance(entry, dict) and entry.get("artifact_type") == "PLAN"
        }
    )
    fixed_required: dict[int, set[str]] = {
        1: {"input/bank.yaml"} | framing_ids | gate_vector_ids,
        2: ({current_source_outcomes_id} if current_source_outcomes_id else set()) | source_answer_ids | derived_evidence_ids,
        3: normative_reviews | iceberg_review_ids,
        4: set(synthesis_ids) | {item["result_id"] for item in iceberg_projection.values()},
        5: projected_plan_ids | {item["result_id"] for item in iceberg_projection.values()},
        6: ({current_final_audit_id} if current_final_audit_id else set()) | {item["result_id"] for item in iceberg_projection.values()},
    }
    if len(framing_ids) != 10:
        errors.append(f"gate:GATE-1:framing_coverage:{len(framing_ids)}")
    if len(framing_vector_ids) != 1 or len(vector_ids) != 1:
        errors.append("gate:GATE-1:vector_binding")
    if "input/bank.yaml" not in immutable_inputs:
        errors.append("gate:GATE-1:bank_binding")
    for number in range(1, last_gate + 1):
        gate_id = f"GATE-{number}"
        path = _gate_path(
            run, gate_id, results, docs, current_final_audit_id
        )
        try:
            gate = load_yaml(path)
        except Exception:
            errors.append(f"gate:{gate_id}:missing_or_invalid")
            continue
        if not isinstance(gate, dict) or not _gate_common(gate, gate_id, snapshot):
            errors.append(f"gate:{gate_id}:contract")
            continue

        required_objects = gate.get("required_objects")
        if (
            not _strings(required_objects, nonempty=True)
            or len(required_objects) != len(set(required_objects))
        ):
            errors.append(f"gate:{gate_id}:required_objects")
            required_objects = []
        required_hash_by_id: dict[str, str] = {}
        for result_id in required_objects:
            entry = results.get(result_id)
            if isinstance(entry, dict) and _hash(entry.get("content_hash")):
                required_hash_by_id[result_id] = entry["content_hash"]
            elif result_id in immutable_inputs:
                required_hash_by_id[result_id] = immutable_inputs[result_id]
            elif isinstance(entry, dict):
                errors.append(f"gate:{gate_id}:required_object_hash:{result_id}")
            else:
                errors.append(f"gate:{gate_id}:required_object:{result_id}")
        required_hashes = set(required_hash_by_id.values())
        for result_id in sorted(fixed_required[number] - set(required_objects)):
            errors.append(f"gate:{gate_id}:missing_required_object:{result_id}")

        allowed_evidence_types = {"EVIDENCE", "IMMUTABLE_INPUT"}
        if number == 6:
            allowed_evidence_types.add("FINAL_AUDIT")
        checks = gate.get("completed_checks")
        if not isinstance(checks, list) or not checks:
            errors.append(f"gate:{gate_id}:empty_completed_checks")
            checks = []
        seen_checks: set[str] = set()
        check_evidence: list[tuple[int, Any, Any]] = []
        for index, check in enumerate(checks):
            prefix = f"gate:{gate_id}:check:{index}"
            required = {"check_id", "property", "outcome", "evidence_result_id", "evidence_sha256"}
            if not isinstance(check, dict) or not required.issubset(check):
                errors.append(prefix + ":contract")
                continue
            check_id = check.get("check_id")
            if not _text(check_id) or check_id in seen_checks or not _text(check.get("property")):
                errors.append(prefix + ":identity_or_property")
            if isinstance(check_id, str):
                seen_checks.add(check_id)
            evidence_id = check.get("evidence_result_id")
            evidence = results.get(evidence_id) if isinstance(evidence_id, str) else None
            evidence_hash = (
                evidence.get("content_hash")
                if isinstance(evidence, dict)
                else immutable_inputs.get(evidence_id)
                if isinstance(evidence_id, str)
                else None
            )
            evidence_type = (
                evidence.get("artifact_type")
                if isinstance(evidence, dict)
                else "IMMUTABLE_INPUT"
                if evidence_hash is not None
                else None
            )
            if evidence_hash is None or check.get("evidence_sha256") != evidence_hash:
                errors.append(prefix + ":evidence_binding")
            elif evidence_type not in allowed_evidence_types:
                errors.append(prefix + f":evidence_type:{evidence_type}")
            check_evidence.append(
                (index, check.get("evidence_result_id"), check.get("evidence_sha256"))
            )
            if check.get("outcome") not in _OUTCOMES:
                errors.append(prefix + ":outcome")

        review = gate.get("content_review")
        content_review_valid = True
        required_review = {
            "status", "result_id", "sha256", "reviewer_role",
            "reviewer_agent_id", "target_hashes", "outcome",
        }
        if not isinstance(review, dict) or not required_review.issubset(review):
            errors.append(f"gate:{gate_id}:content_review_placeholder")
            content_review_valid = False
        else:
            review_id = review.get("result_id")
            entry = results.get(review_id)
            review_doc = docs.get(review_id)
            if (
                review.get("status") != "ACCEPTED"
                or review_id not in valid_reviews
                or not isinstance(entry, dict)
                or entry.get("artifact_type") != "REVIEW"
                or review.get("sha256") != entry.get("content_hash")
                or review.get("reviewer_role") != entry.get("role_id")
                or review.get("reviewer_agent_id") != entry.get("agent_id")
                or review.get("outcome") != review_doc.get("outcome")
            ):
                errors.append(f"gate:{gate_id}:content_review_binding")
                content_review_valid = False
            target_hashes = review.get("target_hashes")
            if (
                not _strings(target_hashes, nonempty=True)
                or len(target_hashes) != len(set(target_hashes))
                or set(target_hashes) != required_hashes
            ):
                errors.append(f"gate:{gate_id}:content_review_target_hashes")
                content_review_valid = False
            if (
                not isinstance(review_doc, dict)
                or review_doc.get("target_result_id") not in required_hash_by_id
                or review_doc.get("target_hash")
                != required_hash_by_id.get(review_doc.get("target_result_id"))
            ):
                errors.append(f"gate:{gate_id}:content_review_target_mismatch")
                content_review_valid = False
            if isinstance(review_id, str):
                if review_id in review_use:
                    errors.append(f"gate:{gate_id}:content_review_reused:{review_id}:{review_use[review_id]}")
                else:
                    review_use[review_id] = gate_id

        evidence_refs = gate.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            errors.append(f"gate:{gate_id}:empty_evidence_refs")
            evidence_refs = []
        audit_bound = False
        evidence_ref_pairs: set[tuple[Any, Any]] = set()
        for index, reference in enumerate(evidence_refs):
            result_id = reference.get("result_id") if isinstance(reference, dict) else None
            entry = results.get(result_id) if isinstance(result_id, str) else None
            content_hash = (
                entry.get("content_hash")
                if isinstance(entry, dict)
                else immutable_inputs.get(result_id)
                if isinstance(result_id, str)
                else None
            )
            artifact_type = (
                entry.get("artifact_type")
                if isinstance(entry, dict)
                else "IMMUTABLE_INPUT"
                if content_hash is not None
                else None
            )
            if not isinstance(reference, dict) or content_hash is None or reference.get("sha256") != content_hash:
                errors.append(f"gate:{gate_id}:evidence_ref:{index}:binding")
                continue
            evidence_ref_pairs.add((reference.get("result_id"), reference.get("sha256")))
            if artifact_type not in allowed_evidence_types:
                errors.append(
                    f"gate:{gate_id}:evidence_ref:{index}:type:{artifact_type}"
                )
            elif artifact_type == "FINAL_AUDIT":
                audit_bound = True
        for index, result_id, content_hash in check_evidence:
            if (result_id, content_hash) not in evidence_ref_pairs:
                errors.append(f"gate:{gate_id}:check:{index}:evidence_ref_missing")
        if number == 6 and not audit_bound:
            errors.append("gate:GATE-6:final_audit_binding")

        unresolved = gate.get("unresolved_critical")
        if not isinstance(unresolved, list):
            errors.append(f"gate:{gate_id}:unresolved_critical_type")
            unresolved = []
        decision = gate.get("decision")
        if decision not in _GATE_DECISIONS:
            errors.append(f"gate:{gate_id}:decision")
        if decision == "PASS":
            if unresolved:
                errors.append(f"gate:{gate_id}:pass_with_open_critical")
            if gate.get("blocked_dependencies"):
                errors.append(f"gate:{gate_id}:pass_with_blocked_dependency")
            if any(isinstance(check, dict) and check.get("outcome") != "PASS" for check in checks):
                errors.append(f"gate:{gate_id}:pass_with_failed_check")
            if not content_review_valid or not isinstance(review, dict) or review.get("outcome") != "PASS":
                errors.append(f"gate:{gate_id}:pass_without_content_acceptance")


def _resolved_audit_id(
    value: str,
    results: dict[str, Any],
    requirements: set[str],
    questions: set[str],
    stages: set[str],
    tasks: set[str],
    checks: set[str],
) -> bool:
    return value in results or value in requirements or value in questions or value in stages or value in tasks or value in checks


def _top_records(value: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    answer: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in answer:
            return None
        answer[item["id"]] = item
    return answer


def _validate_audit(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, Any],
    stage_docs: dict[str, dict[str, Any]],
    task_ids: set[str],
    check_ids: set[str],
    sha: Callable[[bytes], str],
    current_final_audit_id: str | None,
    current_source_outcomes_id: str | None,
    errors: list[str],
) -> None:
    audit_entries = [
        (result_id, entry) for result_id, entry in results.items()
        if isinstance(entry, dict)
        and entry.get("artifact_type") == "FINAL_AUDIT"
        and entry.get("role_id") == "FINAL-AUDITOR"
        and (current_final_audit_id is None or result_id == current_final_audit_id)
    ]
    if len(audit_entries) != 1:
        errors.append("audit:accepted_final_binding")
        return
    audit_id, audit_entry = audit_entries[0]
    audit = docs.get(audit_id)
    if not isinstance(audit, dict):
        errors.append(f"audit:{audit_id}:accepted_final_binding")
        return
    prefix = f"audit:{audit_id}"
    plan_agents = {
        entry.get("agent_id") for entry in results.values()
        if isinstance(entry, dict) and entry.get("artifact_type") == "PLAN"
    }
    if audit_entry.get("agent_id") in plan_agents or not _text(audit.get("auditor_independence")):
        errors.append(prefix + ":independence")
    if audit.get("independence_mode") not in {"INDEPENDENT", "SELF_REVIEW_ONLY", "NOT_PERFORMED"}:
        errors.append(prefix + ":independence_mode")
    if not _hash(audit.get("core_manifest_sha256")):
        errors.append(prefix + ":core_manifest_sha256")

    areas = audit.get("areas")
    details = audit.get("area_details")
    expected_areas = set(_STAGE_SECTIONS)
    if not isinstance(areas, dict) or set(areas) != expected_areas or any(value not in _OUTCOMES for value in areas.values()):
        errors.append(prefix + ":areas")
        areas = {}
    if not isinstance(details, dict) or set(details) != expected_areas:
        errors.append(prefix + ":area_details")
        details = {}

    defects = _top_records(audit.get("defects"))
    conditions = _top_records(audit.get("open_conditions"))
    if defects is None:
        errors.append(prefix + ":defects_contract")
        defects = {}
    if conditions is None:
        errors.append(prefix + ":open_conditions_contract")
        conditions = {}
    detail_defects: set[str] = set()
    detail_conditions: set[str] = set()
    requirements = _requirement_ids(bank)
    questions = _question_ids(bank, docs)
    for area_id in sorted(expected_areas):
        detail = details.get(area_id)
        area_prefix = f"audit:{audit_id}:area:{area_id}"
        required = {
            "ids", "files", "hashes", "procedure", "findings",
            "counterexample_result", "alternative_result", "limitations",
            "defect_ids", "open_condition_ids", "readiness_impact",
        }
        if not isinstance(detail, dict) or not required.issubset(detail):
            errors.append(area_prefix + ":contract")
            continue
        ids = _ids(detail.get("ids"))
        if not ids:
            errors.append(area_prefix + ":empty_ids")
        for entity_id in ids:
            if not _resolved_audit_id(entity_id, results, requirements, questions, set(stage_docs), task_ids, check_ids):
                errors.append(area_prefix + f":unknown_id:{entity_id}")
        files = detail.get("files")
        hashes = detail.get("hashes")
        if not _strings(files, nonempty=True) or not _strings(hashes, nonempty=True) or len(files) != len(hashes):
            errors.append(area_prefix + ":file_hash_pairs")
        else:
            for relative, expected_hash in zip(files, hashes):
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    errors.append(area_prefix + f":unsafe_file:{relative}")
                    continue
                actual = run / path
                try:
                    digest = sha(actual.read_bytes())
                except OSError:
                    errors.append(area_prefix + f":missing_file:{relative}")
                    continue
                if expected_hash != digest:
                    errors.append(area_prefix + f":file_hash_mismatch:{relative}")
        if not _text(detail.get("procedure")):
            errors.append(area_prefix + ":procedure")
        if not _substantive_list(detail.get("findings")):
            errors.append(area_prefix + ":findings")
        if not _text(detail.get("counterexample_result")):
            errors.append(area_prefix + ":counterexample_result")
        if not _text(detail.get("alternative_result")):
            errors.append(area_prefix + ":alternative_result")
        if not isinstance(detail.get("limitations"), list):
            errors.append(area_prefix + ":limitations_type")
        if not _text(detail.get("readiness_impact")):
            errors.append(area_prefix + ":readiness_impact")
        for field in ("defect_ids", "open_condition_ids"):
            value = detail.get(field)
            if not _strings(value) or len(value) != len(set(value)):
                errors.append(area_prefix + ":" + field + "_contract")
        current_defects = set(_ids(detail.get("defect_ids")))
        current_conditions = set(_ids(detail.get("open_condition_ids")))
        detail_defects.update(current_defects)
        detail_conditions.update(current_conditions)
        for defect_id in sorted(current_defects - set(defects)):
            errors.append(area_prefix + f":unknown_defect:{defect_id}")
        for condition_id in sorted(current_conditions - set(conditions)):
            errors.append(area_prefix + f":unknown_open_condition:{condition_id}")
        if areas.get(area_id) == "FAIL" and not current_defects:
            errors.append(area_prefix + ":fail_without_defect")
        if areas.get(area_id) == "NOT_VERIFIED" and not current_conditions:
            errors.append(area_prefix + ":not_verified_without_open_condition")
        pass_blocking_conditions = {
            condition_id for condition_id in current_conditions
            if not isinstance(conditions.get(condition_id), dict)
            or conditions[condition_id].get("criticality") != "NONCRITICAL"
            or conditions[condition_id].get("condition_kind") not in {
                "RESIDUAL_CONDITION", "FUTURE_CHECK"
            }
        }
        if areas.get(area_id) == "PASS" and (current_defects or pass_blocking_conditions):
            errors.append(area_prefix + ":pass_with_defect_or_open_condition")

    for defect_id in sorted(set(defects) - detail_defects):
        errors.append(prefix + f":unlinked_defect:{defect_id}")
    for condition_id in sorted(set(conditions) - detail_conditions):
        errors.append(prefix + f":unlinked_open_condition:{condition_id}")
    defect_required = {
        "id", "area_ids", "violated_requirement_or_condition",
        "evidence_or_counterexample", "file_or_task", "minimal_repair",
        "regression", "goal_impact",
    }
    for defect_id, defect in sorted(defects.items()):
        if not defect_required.issubset(defect) or not _strings(defect.get("area_ids"), nonempty=True) or any(not _text(defect.get(field)) for field in defect_required - {"id", "area_ids"}):
            errors.append(prefix + f":defect_contract:{defect_id}")
        elif not set(defect["area_ids"]).issubset(expected_areas):
            errors.append(prefix + f":defect_area:{defect_id}")
    condition_required = {
        "id", "area_ids", "criticality", "condition", "next_path",
        "condition_kind", "goal_requirement_impact", "admissibility_basis",
        "early_check_id", "gated_dependency_ids", "affected_requirement_ids",
        "affected_decision_ids",
    }
    task_docs: dict[str, dict[str, Any]] = {}
    check_to_task: dict[str, str] = {}
    task_edges: set[tuple[str, str]] = set()
    for stage in stage_docs.values():
        for task in stage.get("tasks", []) if isinstance(stage, dict) else []:
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                task_docs[task["id"]] = task
        for check in stage.get("acceptance_exit", []) if isinstance(stage, dict) else []:
            if (
                isinstance(check, dict)
                and isinstance(check.get("id"), str)
                and isinstance(check.get("task_id"), str)
            ):
                check_to_task[check["id"]] = check["task_id"]
    for task_id, task in task_docs.items():
        for dependency_id in _ids(task.get("dependencies")):
            if dependency_id in task_docs:
                task_edges.add((dependency_id, task_id))
    for stage in stage_docs.values():
        graph = stage.get("local_execution_graph") if isinstance(stage, dict) else None
        for edge in graph.get("edges", []) if isinstance(graph, dict) else []:
            pair = _edge_pair(edge)
            if pair is not None and pair[0] in task_docs and pair[1] in task_docs:
                task_edges.add(pair)
    task_reach = {
        task_id: _reachable(task_edges, task_id) for task_id in task_docs
    }
    decision_ids = {
        result_id
        for result_id, entry in results.items()
        if isinstance(entry, dict) and entry.get("artifact_type") == "DECISION"
    }
    invalid_noncritical_conditions: set[str] = set()
    for condition_id, condition in sorted(conditions.items()):
        if (
            not condition_required.issubset(condition)
            or not _strings(condition.get("area_ids"), nonempty=True)
            or not set(condition.get("area_ids", [])).issubset(expected_areas)
            or condition.get("criticality") not in {"CRITICAL", "NONCRITICAL"}
            or not _text(condition.get("condition"))
            or not _text(condition.get("next_path"))
            or condition.get("condition_kind") not in _CONDITION_KINDS
            or not _text(condition.get("goal_requirement_impact"))
            or not _text(condition.get("admissibility_basis"))
            or condition.get("early_check_id") not in check_ids
            or not _strings(condition.get("gated_dependency_ids"), nonempty=True)
            or not _strings(condition.get("affected_requirement_ids"))
            or not _strings(condition.get("affected_decision_ids"))
        ):
            errors.append(prefix + f":open_condition_contract:{condition_id}")
            if condition.get("criticality") == "NONCRITICAL":
                invalid_noncritical_conditions.add(condition_id)
            continue
        affected_requirements = set(_ids(condition.get("affected_requirement_ids")))
        affected_decisions = set(_ids(condition.get("affected_decision_ids")))
        for requirement_id in sorted(affected_requirements - requirements):
            errors.append(prefix + f":open_condition_unknown_requirement:{condition_id}:{requirement_id}")
        for decision_id in sorted(affected_decisions - decision_ids):
            errors.append(prefix + f":open_condition_unknown_decision:{condition_id}:{decision_id}")
        if condition.get("criticality") == "NONCRITICAL":
            if condition.get("condition_kind") not in {"RESIDUAL_CONDITION", "FUTURE_CHECK"}:
                errors.append(prefix + f":noncritical_kind:{condition_id}")
                invalid_noncritical_conditions.add(condition_id)
            if affected_requirements:
                errors.append(prefix + f":noncritical_affects_goal_requirement:{condition_id}")
                invalid_noncritical_conditions.add(condition_id)
            critical_decisions = {
                decision_id for decision_id in affected_decisions
                if isinstance(docs.get(decision_id), dict)
                and docs[decision_id].get("criticality") == "CRITICAL"
            }
            for decision_id in sorted(critical_decisions):
                errors.append(
                    prefix + f":noncritical_affects_critical_decision:{condition_id}:{decision_id}"
                )
                invalid_noncritical_conditions.add(condition_id)
            early_task_id = check_to_task.get(condition.get("early_check_id"))
            for dependency_id in condition.get("gated_dependency_ids", []):
                if dependency_id not in task_ids:
                    errors.append(
                        prefix + f":noncritical_dependency_not_task:{condition_id}:{dependency_id}"
                    )
                    invalid_noncritical_conditions.add(condition_id)
                elif (
                    early_task_id not in task_docs
                    or dependency_id == early_task_id
                    or dependency_id not in task_reach.get(early_task_id, set())
                ):
                    errors.append(
                        prefix + f":noncritical_check_not_before_dependency:{condition_id}:{dependency_id}"
                    )
                    invalid_noncritical_conditions.add(condition_id)
        for dependency_id in condition.get("gated_dependency_ids", []):
            if not _resolved_audit_id(dependency_id, results, requirements, questions, set(stage_docs), task_ids, check_ids):
                errors.append(prefix + f":open_condition_unknown_dependency:{condition_id}:{dependency_id}")

    if not _substantive_list(audit.get("counterexample_results")):
        errors.append(prefix + ":counterexample_results")
    if not _substantive_list(audit.get("alternative_results")):
        errors.append(prefix + ":alternative_results")
    if not _substantive_list(audit.get("verdict_reasons")):
        errors.append(prefix + ":verdict_reasons")
    if not _text(audit.get("allowed_next_action")):
        errors.append(prefix + ":allowed_next_action")
    if not _substantive_list(audit.get("claim_limits")):
        errors.append(prefix + ":claim_limits")

    readiness = audit.get("readiness")
    if not isinstance(readiness, dict) or set(readiness) != _READINESS_AXES or any(value not in _READINESS for value in readiness.values()):
        errors.append(prefix + ":readiness")
        readiness = {}
    if audit.get("plan_readiness") != readiness.get("plan"):
        errors.append(prefix + ":plan_readiness_mismatch")
    if audit.get("implementation_status") not in {"NOT_RUN", "RUNNING", "ACCEPTED", "FAILED", "BLOCKED", "NEEDS_RECHECK"}:
        errors.append(prefix + ":implementation_status")
    if audit.get("effect_status") not in {"NOT_RUN", "RUNNING", "ACCEPTED", "FAILED", "BLOCKED", "NEEDS_RECHECK"}:
        errors.append(prefix + ":effect_status")

    has_fail = any(value == "FAIL" for value in areas.values())
    has_unknown = any(value == "NOT_VERIFIED" for value in areas.values())
    blocked = any(value == "BLOCKED" for value in readiness.values())
    condition_kinds = {
        condition.get("condition_kind")
        for condition in conditions.values()
        if isinstance(condition, dict)
    }
    has_critical_condition = any(
        isinstance(condition, dict) and condition.get("criticality") == "CRITICAL"
        for condition in conditions.values()
    )
    repair = (
        bool(defects)
        or has_fail
        or "REPAIRABLE_DEFECT" in condition_kinds
        or audit.get("independence_mode") != "INDEPENDENT"
        or audit.get("last_fix_status") != "CHECKED"
    )
    externally_blocked = "REQUIRED_EXTERNAL_INPUT" in condition_kinds
    if blocked or externally_blocked:
        expected_verdict = "BLOCKED"
    elif repair:
        expected_verdict = "REPAIR_REQUIRED"
    elif (
        has_unknown
        or "CENTRAL_UNCERTAINTY" in condition_kinds
        or has_critical_condition
        or invalid_noncritical_conditions
    ):
        expected_verdict = "RESEARCH_REQUIRED"
    else:
        expected_verdict = "READY"
    if audit.get("verdict") not in _VERDICTS or audit.get("verdict") != expected_verdict:
        errors.append(prefix + f":verdict_coherence:{expected_verdict}")
    if expected_verdict == "READY" and (readiness.get("package") != "READY" or readiness.get("plan") != "READY"):
        errors.append(prefix + ":ready_without_package_and_plan")
    source_doc = docs.get(current_source_outcomes_id) if current_source_outcomes_id else None
    if expected_verdict == "READY" and isinstance(source_doc, dict) and any(
        isinstance(record, dict)
        and record.get("record_kind") == "SOURCE_OUTCOME"
        and record.get("answer_status") == "OPEN"
        for record in source_doc.get("records", [])
    ):
        errors.append(prefix + ":ready_with_open_source_question")


def architecture_enabled(snapshot: Any) -> bool:
    """Architecture is mandatory only for the immutable feature-pinned run."""
    return isinstance(snapshot, dict) and isinstance(snapshot.get("methodology_features"), list) and _ARCHITECTURE_FEATURE in snapshot["methodology_features"]


def architecture_context(
    run: Path, bank: dict[str, Any], results: dict[str, Any], docs: dict[str, Any],
    stage_docs: dict[str, Any] | None = None, *, load_yaml: Callable[[Path], Any],
    sha: Callable[[bytes], str] = lambda data: hashlib.sha256(data).hexdigest(),
) -> dict[str, Any]:
    """Adapt the existing accepted model and plan contracts without copying state."""
    from prep_validation import canonical_yaml_bytes
    immutable = _immutable_input_hashes(Path(run), bank, sha)
    anchor_errors: list[str] = []
    anchor, _ = _resolve_vector_anchor(Path(run), results, docs, immutable, load_yaml, anchor_errors)
    context: dict[str, Any] = {
        "requirements": _requirement_ids(bank),
        "goal_ids": {anchor["vector_id"]} if anchor else set(),
        "stages": stage_docs or {}, "tasks": {}, "checks": {},
        "input_contracts": {}, "output_contracts": {},
        "input_contract_stage": {}, "output_contract_stage": {},
        "task_stage": {}, "check_stage": {}, "results": results, "docs": docs,
        "immutable_inputs": immutable, "plan_required": stage_docs is not None,
        "acyclic": _acyclic, "reachable": _reachable, "canon": canonical_yaml_bytes,
        "sha": sha, "context_errors": anchor_errors,
        "stale_result_ids": _ids((_load_mapping(Path(run) / "state/current.yaml", load_yaml) or {}).get("stale_result_ids")),
    }
    for stage_id, stage in sorted((stage_docs or {}).items()):
        if not isinstance(stage, dict):
            continue
        for task in stage.get("tasks", []) if isinstance(stage.get("tasks"), list) else []:
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                context["tasks"][task["id"]] = task
                context["task_stage"][task["id"]] = stage_id
        for check in stage.get("acceptance_exit", []) if isinstance(stage.get("acceptance_exit"), list) else []:
            if isinstance(check, dict) and isinstance(check.get("id"), str):
                context["checks"][check["id"]] = check
                context["check_stage"][check["id"]] = stage_id
        contracts = stage.get("inputs_contracts", {})
        if not isinstance(contracts, dict):
            continue
        for direction in ("input", "output"):
            for contract in contracts.get(direction + "s", []) if isinstance(contracts.get(direction + "s"), list) else []:
                if isinstance(contract, dict) and isinstance(contract.get("id"), str):
                    context[direction + "_contracts"][contract["id"]] = contract
                    context[direction + "_contract_stage"][contract["id"]] = stage_id
    context["task_order_edges"] = normalize_plan_order(context["stages"], context["tasks"])["task_edges"]
    return context


def _architecture_reference(
    run: Path, reference: Any, results: dict[str, Any], docs: dict[str, Any],
    sha: Callable[[bytes], str],
) -> tuple[dict[str, Any] | None, list[str]]:
    from prep_validation import canonical_yaml_bytes
    prefix = "architecture_ref"
    if not isinstance(reference, dict) or set(reference) != {"synthesis_id", "synthesis_sha256", "graph_sha256"}:
        return None, [prefix + ":contract"]
    synthesis_id = reference.get("synthesis_id")
    if not isinstance(synthesis_id, str):
        return None, [prefix + ":synthesis_id"]
    entry = results.get(synthesis_id)
    doc = docs.get(synthesis_id)
    if (not isinstance(entry, dict) or entry.get("artifact_type") != "SYNTHESIS"
            or entry.get("role_id") != "OP-SYNTHESIS" or not isinstance(doc, dict)
            or doc.get("id") != synthesis_id):
        return None, [prefix + f":accepted_synthesis:{synthesis_id}"]
    errors: list[str] = []
    if reference.get("synthesis_sha256") != entry.get("content_hash") or not _result_file_matches(run, synthesis_id, results, sha):
        errors.append(prefix + f":synthesis_hash:{synthesis_id}")
    graph = doc.get("architecture_graph")
    if not isinstance(graph, dict):
        return None, errors + [prefix + f":graph_missing:{synthesis_id}"]
    if doc.get("chosen_mechanism_id") != graph.get("selected_mechanism_id"):
        errors.append(prefix + f":selected_mechanism:{synthesis_id}")
    if reference.get("graph_sha256") != sha(canonical_yaml_bytes(graph)):
        errors.append(prefix + f":graph_hash:{synthesis_id}")
    return graph, errors


def resolve_architecture_model(
    run: Path, bank: dict[str, Any], results: dict[str, Any], docs: dict[str, Any],
    plan_projection: dict[str, str] | None = None, *, current: dict[str, Any] | None = None,
    allow_unselected: bool = False, allow_stale: bool = False,
    load_yaml: Callable[[Path], Any],
    sha: Callable[[bytes], str] = lambda data: hashlib.sha256(data).hexdigest(),
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one graph through current S-000, never by filename or list order."""
    from architecture_validation import validate_architecture_graph
    run = Path(run)
    result: dict[str, Any] = {"selection_status": "LEGACY_QUALIFIED", "errors": []}
    if snapshot is None:
        snapshot = _load_mapping(run / "input/snapshot.yaml", load_yaml)
    if not architecture_enabled(snapshot):
        return result
    stage_docs: dict[str, Any] = {}
    errors: list[str] = []
    if plan_projection is not None and (not isinstance(plan_projection, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in plan_projection.items()) or len(set(plan_projection.values())) != len(plan_projection)):
        return {"selection_status": "INVALID", "errors": ["architecture:plan_projection_contract"]}
    for result_id, entry in sorted(results.items()):
        if not isinstance(entry, dict) or entry.get("artifact_type") != "PLAN":
            continue
        if plan_projection is not None and result_id not in plan_projection:
            continue
        stage_id = plan_projection.get(result_id, result_id) if plan_projection is not None else result_id
        if stage_id in stage_docs:
            errors.append(f"architecture:duplicate_stage:{stage_id}")
        if isinstance(docs.get(result_id), dict):
            stage_docs[stage_id] = docs[result_id]
    root_stage = stage_docs.get("S-000")
    if not isinstance(root_stage, dict):
        return {"selection_status": "UNSELECTED", "errors": errors + ([] if allow_unselected else ["architecture:current_S-000_missing"])}
    reference = root_stage.get("architecture_ref")
    graph, ref_errors = _architecture_reference(run, reference, results, docs, sha)
    errors.extend(ref_errors)
    if graph is None:
        return {"selection_status": "INVALID", "errors": sorted(set(errors))}
    for stage_id, stage in stage_docs.items():
        if stage.get("architecture_ref") != reference:
            errors.append(f"architecture:stage:{stage_id}:reference_mismatch")
    current = current if isinstance(current, dict) else (_load_mapping(run / "state/current.yaml", load_yaml) or {})
    stale = set(_ids(current.get("stale_result_ids")))
    selected_stale = reference["synthesis_id"] in stale or any(stage.get("id") in stale for stage in stage_docs.values())
    if selected_stale and not allow_stale:
        errors.append("architecture:selected_model_stale")
    context = architecture_context(run, bank, results, docs, stage_docs, load_yaml=load_yaml, sha=sha)
    errors.extend(context["context_errors"])
    context["stale_result_ids"] = sorted(stale)
    report = validate_architecture_graph(graph, context)
    errors.extend(report["errors"])
    return {"architecture_ref": reference, "graph": graph, "report": report,
            "selection_status": "INVALID" if errors else ("NEEDS_RECHECK" if selected_stale else "SELECTED"),
            "errors": sorted(set(errors))}


def _validate_architecture_audit(
    resolved: dict[str, Any], audit: dict[str, Any], results: dict[str, Any],
    sha: Callable[[bytes], str], errors: list[str], *, stale_result_ids: Iterable[str] = (),
) -> None:
    from prep_validation import canonical_yaml_bytes
    if audit.get("architecture_ref") != resolved.get("architecture_ref"):
        errors.append("architecture:audit:reference_mismatch")
    review = audit.get("architecture_review")
    shape_errors = _architecture_review_contract(review, results, "architecture:audit", stale_result_ids=stale_result_ids)
    errors.extend(shape_errors)
    if shape_errors:
        return
    if review.get("structural_report_sha256") != sha(canonical_yaml_bytes(resolved.get("report"))):
        errors.append("architecture:audit:structural_report_hash")
    if not isinstance(review.get("semantic_outcome"),str) or review.get("semantic_outcome") not in {"PASS", "FAIL", "NOT_VERIFIED"}:
        errors.append("architecture:audit:semantic_outcome")
    for field in ("procedure", "observation"):
        if not _text(review.get(field)):
            errors.append("architecture:audit:" + field)
    evidence_ids = review.get("evidence_ids")
    if not _strings(evidence_ids) or any(not isinstance(results.get(e), dict) or results[e].get("artifact_type") != "EVIDENCE" for e in _ids(evidence_ids)):
        errors.append("architecture:audit:evidence_binding")
    if review.get("semantic_outcome") == "PASS" and not _ids(evidence_ids):
        errors.append("architecture:audit:semantic_pass_without_evidence")
    if not _strings(review.get("limits")):
        errors.append("architecture:audit:limits_contract")
    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append("architecture:audit:findings_contract")
        findings = []
    graph = resolved.get("graph", {})
    addresses = {i.get("id") for field in ("nodes", "edges", "contracts") for i in graph.get(field, []) if isinstance(i, dict) and isinstance(i.get("id"), str)}
    seen: set[str] = set()
    for finding in findings:
        if (not isinstance(finding, dict) or not _text(finding.get("id"))
                or finding["id"] in seen or not isinstance(finding.get("target_id"),str) or finding.get("target_id") not in addresses
                or not _text(finding.get("required_action")) or not _strings(finding.get("evidence_ids"), nonempty=True)
                or any(not isinstance(results.get(e), dict) or results[e].get("artifact_type") != "EVIDENCE" for e in _ids(finding.get("evidence_ids")))):
            errors.append("architecture:audit:finding_contract")
        elif isinstance(finding, dict):
            seen.add(finding["id"])
    report = resolved.get("report", {})
    if audit.get("verdict") == "READY" and (
        resolved.get("errors") or review.get("semantic_outcome") != "PASS" or findings
        or not isinstance(report, dict)
        or report.get("model_integrity_status") != "PASS"
        or report.get("structural_status") != "PASS"
        or report.get("reachability_status") != "COMPLETE"
        or report.get("blockers") != []
    ):
        errors.append("architecture:audit:graph_connectivity_does_not_establish_ready")


def validate_model(
    run: Path,
    bank: dict[str, Any],
    results: dict[str, Any],
    docs: dict[str, dict[str, Any]],
    phase: str,
    load_yaml: Callable[[Path], Any],
    sha: Callable[[bytes], str],
    plan_projection: dict[str, str] | None = None,
    current_source_outcomes_id: str | None = None,
    current_final_audit_id: str | None = None,
    active_critical_decision_ids: set[str] | None = None,
    question_gap_proofs: dict[str, dict[str, str]] | None = None,
    *,
    diagnostics: list[str] | None = None,
) -> list[str]:
    """Return deterministic, addressable structural errors for one accepted run.

    ``phase='core'`` validates the pre-audit model and GATE-1..GATE-5.  The
    final audit and GATE-6 are intentionally outside that phase.  ``final``
    adds their exact structural bindings and coherence checks. Optional
    diagnostics report nonblocking Double Iceberg observations.
    """
    errors: list[str] = []
    run = Path(run)
    if phase not in {"core", "final"}:
        return [f"model:invalid_phase:{phase}"]
    if not isinstance(bank, dict) or not _requirement_ids(bank):
        errors.append("bank:requirements_contract")
    if not isinstance(results, dict) or not isinstance(docs, dict):
        return ["model:registry_or_docs_type"]

    immutable_inputs = _immutable_input_hashes(run, bank, sha)
    snapshot_doc = _load_mapping(run / "input" / "snapshot.yaml", load_yaml)
    iceberg_enabled = double_iceberg_enabled(snapshot_doc)
    _validate_registry(results, docs, errors)
    resolved_source_outcomes_id = _select_current_result(
        results, "SOURCE_OUTCOMES", current_source_outcomes_id,
        "source_outcomes_selection", errors,
    )
    resolved_final_audit_id = _select_current_result(
        results, "FINAL_AUDIT", current_final_audit_id,
        "final_audit_selection", errors, required=phase == "final",
    )
    if resolved_source_outcomes_id is not None:
        source_doc = docs.get(resolved_source_outcomes_id)
        if not isinstance(source_doc, dict) or not _result_matches_doc(
            resolved_source_outcomes_id, results, docs
        ):
            errors.append(
                f"source_outcomes_selection:accepted_binding:{resolved_source_outcomes_id}"
            )
        else:
            errors.extend(validate_intake_candidate(
                run, bank, results, source_doc, "SOURCE_OUTCOMES", load_yaml, question_gap_proofs
            ))
            original_questions = {
                item["id"] for item in bank.get("questions", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            covered: list[str] = []
            for record in source_doc.get("records", []):
                if isinstance(record, dict) and record.get("record_kind") == "SOURCE_OUTCOME":
                    covered.extend(_ids(record.get("original_q_ids")))
            if set(covered) != original_questions or len(covered) != len(set(covered)):
                errors.append(
                    f"source_outcomes:{resolved_source_outcomes_id}:original_question_coverage"
                )
    _validate_external_implementation_evidence(run, bank, results, docs, sha, errors)
    valid_reviews = _validate_reviews(
        results, docs, immutable_inputs, errors, active_critical_decision_ids
    )
    revision_scope = model_revision_scope_for_run(
        run, results, docs, plan_projection, resolved_final_audit_id, load_yaml, sha,
        phase=phase,
    )
    errors.extend(revision_scope["errors"])
    _validate_historical_consumer_links(
        run, bank, results, docs, revision_scope["historical_result_ids"], sha, errors,
    )
    synthesis_ids = _validate_synthesis_contract(
        results, docs, errors, revision_scope["active_synthesis_ids"],
    )
    task_ids, check_ids, stage_docs = _validate_stages(
        run, bank, results, docs, immutable_inputs, errors, plan_projection
    )
    administrative_reviews = _administrative_final_reviews(
        run, results, docs, phase, load_yaml, resolved_final_audit_id
    )
    iceberg_projection, iceberg_history, iceberg_errors = validate_double_iceberg(
        run, bank, results, docs, phase, load_yaml, sha,
        revision_scope=revision_scope, diagnostics=diagnostics,
    )
    errors.extend(iceberg_errors)
    iceberg_anchor: dict[str, Any] | None = None
    if iceberg_enabled:
        iceberg_anchor, _iceberg_gate_objects = _resolve_vector_anchor(
            run, results, docs, immutable_inputs, load_yaml, errors
        )
    _validate_dependency_graph(
        bank, results, docs, task_ids, check_ids, administrative_reviews,
        iceberg_anchor, iceberg_enabled, revision_scope["historical_result_ids"], errors,
    )
    if architecture_enabled(snapshot_doc):
        architecture = resolve_architecture_model(
            run, bank, results, docs, plan_projection, load_yaml=load_yaml, sha=sha,
        )
        errors.extend(architecture["errors"])
        if phase == "final" and resolved_final_audit_id is not None:
            audit_doc = docs.get(resolved_final_audit_id)
            if isinstance(audit_doc, dict):
                _validate_architecture_audit(architecture, audit_doc, results, sha, errors, stale_result_ids=_ids((_load_mapping(run / "state/current.yaml", load_yaml) or {}).get("stale_result_ids")))
    _validate_gates(
        run, bank, results, docs, immutable_inputs, phase, load_yaml,
        valid_reviews, synthesis_ids, plan_projection,
        resolved_source_outcomes_id, resolved_final_audit_id,
        iceberg_projection, errors,
    )
    if phase == "final":
        _validate_audit(
            run, bank, results, docs, stage_docs, task_ids, check_ids, sha,
            resolved_final_audit_id, resolved_source_outcomes_id, errors,
        )
    return sorted(set(errors))

