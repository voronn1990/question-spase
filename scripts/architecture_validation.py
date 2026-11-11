"""Canonical architecture graph checks and projections (no execution or role changes).

The caller supplies the controller's existing graph helpers and selected canonical
plan records.  A structural PASS is deliberately not a semantic or runtime claim.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable

from prep_validation import canonical_yaml_bytes
from plan_semantics import normalize_plan_order, resolve_plan_input

FEATURE = "ARCHITECTURE_GRAPH_V1"
SCHEMA_VERSION = "question-space-architecture-v1"
NODE_KINDS = {"G", "R", "CAPABILITY", "COMPONENT", "ALGORITHM", "DATA", "S", "T", "C"}
EXECUTION_KINDS = {"REQUIRES", "DEPENDS_ON", "REALIZES", "IMPLEMENTS", "PRODUCES", "CONSUMES", "CONTRIBUTES_TO"}
EDGE_KINDS = EXECUTION_KINDS | {"VERIFIES", "EVIDENCE_SUPPORT", "RESEARCH_FEEDBACK"}
IMPLEMENTATION_STATUSES = {"IMPLEMENTED_VERIFIED", "IMPLEMENTED_UNQUALIFIED", "PLANNED_ABSENT", "CONTRACT_DIVERGENCE", "EXTRA_UNJUSTIFIED"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def architecture_graph_sha256(graph: dict[str, Any], *, canonicalize: Callable | None = None, sha256: Callable | None = None) -> str:
    payload = (canonicalize or canonical_yaml_bytes)(graph)
    return (sha256 or (lambda value: hashlib.sha256(value).hexdigest()))(payload)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value) and len(value) == len(set(value))


def _records(value: Any, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(value, list):
        errors.append(f"architecture {label}: expected list")
        return result
    for index, record in enumerate(value):
        if not isinstance(record, dict) or not _text(record.get("id")):
            errors.append(f"architecture {label}[{index}]: expected mapping with id")
            continue
        identifier = record["id"]
        if identifier in result:
            errors.append(f"architecture {label}: duplicate id {identifier}")
        else:
            result[identifier] = record
    return result


def _selection(graph: dict[str, Any]) -> tuple[dict, dict, list[dict]]:
    if not isinstance(graph, dict):
        raise ValueError("architecture graph: expected mapping")
    raw_nodes, raw_edges = graph.get("nodes", []), graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ValueError("architecture nodes/edges: expected lists")
    nodes = {node["id"]: node for node in raw_nodes if isinstance(node, dict) and _text(node.get("id"))}
    selected = {key: node for key, node in nodes.items() if node.get("mechanism_id") == graph.get("selected_mechanism_id")}
    edges = [edge for edge in raw_edges if isinstance(edge, dict) and isinstance(edge.get("from_id"), str) and isinstance(edge.get("to_id"), str) and edge["from_id"] in selected and edge["to_id"] in selected]
    return nodes, selected, edges


def _helpers(context: dict[str, Any]) -> tuple[Callable | None, Callable | None]:
    return context.get("acyclic"), context.get("reachable")


def _projection_reachable(pairs: Iterable[tuple[str, str]], start: str, callback: Callable | None = None) -> set[str]:
    # Delayed fallback permits direct rendering without an import-time cycle.
    # Validation always receives the same helpers as its selected-plan caller.
    if callback is None:
        from model_validation import _reachable
        callback = _reachable
    return set(callback(list(pairs), start))


def _accepted_evidence(identifier: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    results, docs = context.get("results", {}), context.get("docs", {})
    if not isinstance(identifier, str):
        return None
    doc = docs.get(identifier) if isinstance(identifier, str) else None
    if identifier not in results or not isinstance(doc, dict) or doc.get("artifact_type") != "EVIDENCE":
        return None
    if identifier in set(context.get("stale_result_ids", [])):
        return None
    return doc


def _reviewed_branch(contract: dict[str, Any], context: dict[str, Any]) -> bool:
    evidence_id = contract.get("branch_selection_evidence_id")
    proof = _accepted_evidence(evidence_id, context)
    if proof is None:
        return False
    review_id = proof.get("branch_selection_review_id")
    if not _text(review_id):
        return False
    results, docs = context["results"], context["docs"]
    review, review_entry = docs.get(review_id), results.get(review_id)
    evidence_entry = results.get(evidence_id)
    return (
        isinstance(review, dict) and isinstance(review_entry, dict) and isinstance(evidence_entry, dict)
        and review_id not in context.get("stale_result_ids", [])
        and review_entry.get("artifact_type") == "REVIEW" and review.get("artifact_type") == "REVIEW"
        and _text(review_entry.get("agent_id")) and _text(evidence_entry.get("agent_id"))
        and review_entry["agent_id"] != evidence_entry["agent_id"]
        and review.get("outcome") == "PASS"
        and review.get("target_result_id") == evidence_id
        and _text(evidence_entry.get("content_hash"))
        and review.get("target_hash") == evidence_entry["content_hash"]
        and _text(contract.get("version"))
        and _text(contract.get("producer_task_id")) and _text(contract.get("acceptance_check_id"))
        and review.get("branch_selection_scope") == {
            "output_id": contract["id"], "contract_version": contract["version"],
            "producer_task_id": contract["producer_task_id"],
            "acceptance_check_id": contract["acceptance_check_id"],
            "contract_sha256": hashlib.sha256(canonical_yaml_bytes(contract)).hexdigest(),
        }
    )


def _producer_tasks(identifier: str, selected: dict, edges: list[dict]) -> set[str]:
    node = selected.get(identifier, {})
    if node.get("kind") == "T":
        return {identifier}
    return {edge["from_id"] for edge in edges if edge.get("kind") == "IMPLEMENTS" and edge.get("to_id") == identifier and selected.get(edge.get("from_id"), {}).get("kind") == "T"}


def _input_key(binding: dict[str, Any] | None) -> tuple[str, str] | None:
    """Compare supported aliases by identity, without dropping INPUT obligations.

    A PLANNED INPUT wrapper and its OUTPUT/path denote the same resource, but
    the wrapper's consumer, version and availability still need validation.
    """
    if not binding:
        return None
    if binding.get("input_kind") == "PLANNED":
        identifier = binding.get("output_id")
        return ("OUTPUT", identifier) if _text(identifier) else None
    identifier = binding.get("contract_id")
    return ("INPUT", identifier) if _text(identifier) else None


def _shape_errors(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("schema_version", "id", "revision", "project_kind", "selected_mechanism_id"):
        if not _text(graph.get(field)):
            errors.append(f"architecture graph.{field}: expected nonempty string")
    fields = {
        "mechanisms": (("id", "status"), (), ()),
        "nodes": (("id", "kind", "label", "subsystem_id", "mechanism_id", "required"), ("existing_id", "stage_id", "task_id", "contract_id", "availability", "source_id", "accepted_hash", "version", "format", "source", "owner", "availability_condition", "availability_status", "availability_evidence_id", "procedure", "expected_observation"), ()),
        "edges": (("id", "kind", "from_id", "to_id", "meaning", "scope"), ("contract_id", "input_id", "compatibility_check_id"), ("conditions", "check_ids")),
        "contracts": (("id", "version", "format", "semantics", "availability", "error_behavior"), (), ("external_conditions", "acceptance_check_ids")),
    }
    for collection, (required, optional, lists) in fields.items():
        records = graph.get(collection)
        if not isinstance(records, list):
            errors.append(f"architecture {collection}: expected list")
            continue
        for index, record in enumerate(records):
            prefix = f"architecture {collection}[{index}]"
            if not isinstance(record, dict):
                errors.append(prefix + ": expected mapping")
                continue
            for field in required:
                if not _text(record.get(field)):
                    errors.append(prefix + f".{field}: expected nonempty string")
            for field in optional:
                if field in record and not _text(record[field]):
                    errors.append(prefix + f".{field}: expected nonempty string")
            for field in lists:
                if not _strings(record.get(field)):
                    errors.append(prefix + f".{field}: expected unique string list")
            if collection == "nodes":
                if "compatibility_contract_ids" in record and not _strings(record["compatibility_contract_ids"]):
                    errors.append(prefix + ".compatibility_contract_ids: expected unique string list")
                if "plan_ref" in record:
                    ref = record["plan_ref"]
                    if not isinstance(ref, dict) or not _text(ref.get("stage_id")) or not _text(ref.get("output_id")):
                        errors.append(prefix + ".plan_ref: expected exact stage_id/output_id mapping")
            if collection == "contracts":
                refs = record.get("plan_refs")
                if not isinstance(refs, list):
                    errors.append(prefix + ".plan_refs: expected list")
                else:
                    for ref in refs:
                        if not isinstance(ref, dict) or any(not _text(ref.get(field)) for field in ("stage_id", "kind", "contract_id")) or "path" in ref and not _text(ref["path"]):
                            errors.append(prefix + ".plan_refs: expected exact canonical reference mappings")
    return sorted(set(errors))


def _context_shape_errors(context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for collection in ("tasks", "checks", "input_contracts", "output_contracts", "results", "docs", "stages", "immutable_inputs", "task_stage", "check_stage", "input_contract_stage", "output_contract_stage"):
        records = context.get(collection, {})
        if not isinstance(records, dict) or any(not isinstance(key, str) for key in records):
            errors.append(f"architecture context.{collection}: expected string-keyed mapping")
            continue
        if collection in {"immutable_inputs", "task_stage", "check_stage", "input_contract_stage", "output_contract_stage"}:
            if any(not isinstance(value, str) for value in records.values()):
                errors.append(f"architecture context.{collection}: expected string values")
            continue
        for identifier, record in records.items():
            prefix = f"architecture context.{collection}.{identifier}"
            if not isinstance(record, dict):
                errors.append(prefix + ": expected mapping")
                continue
            list_fields = {"tasks": ("inputs", "outputs", "dependencies", "r_refs", "checks", "acceptance_criteria"), "checks": ("inputs",), "input_contracts": ("consumer_ids", "blocked_consumer_ids"), "output_contracts": ("consumer_ids",)}.get(collection, ())
            for field in list_fields:
                if field in record and not _strings(record[field]):
                    errors.append(prefix + f".{field}: expected unique string list")
            if collection == "tasks" and "execution_status" in record and (
                not isinstance(record["execution_status"], str)
                or record["execution_status"] not in {"NOT_STARTED", "RUNNING", "ACCEPTED", "FAILED", "BLOCKED", "NEEDS_RECHECK", "NOT_APPLICABLE"}
            ):
                errors.append(prefix + ".execution_status: invalid canonical state")
            if collection == "checks":
                if not _text(record.get("task_id")):
                    errors.append(prefix + ".task_id: expected nonempty string")
                for field in ("execution_status", "status"):
                    if field in record and not _text(record[field]):
                        errors.append(prefix + f".{field}: expected nonempty string")
                if "compatibility_contract_ids" in record and not _strings(record["compatibility_contract_ids"]):
                    errors.append(prefix + ".compatibility_contract_ids: expected unique string list")
            if collection in {"input_contracts", "output_contracts"}:
                for field in ("source_id", "accepted_hash", "version", "path", "producer_task_id", "output_id", "acceptance_check_id", "source", "owner", "availability_condition", "availability_status", "availability_evidence_id", "input_kind", "contract", "format"):
                    if field in record and not _text(record[field]):
                        errors.append(prefix + f".{field}: expected nonempty string")
    for key in ("requirements", "goal_ids", "stale_result_ids"):
        values = context.get(key, set())
        if not isinstance(values, (set, list, tuple)) or any(not isinstance(value, str) for value in values):
            errors.append(f"architecture context.{key}: expected string ids")
    return sorted(set(errors))


def validate_architecture_graph(graph: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Validate one hash-bound graph against canonical selected-plan context.

    Required context helpers: acyclic(nodes, pairs), reachable(pairs, start).
    plan_required=False admits future S/T/C and contract references at synthesis;
    all selected plan bindings are checked when plan_required=True.
    """
    errors: list[str] = list(context.get("context_errors", []))
    empty_report = {"structural_status": "FAIL", "model_integrity_status": "FAIL", "errors": errors,
                    "reachability_status": "BLOCKED", "blockers": [], "blocked_nodes": [], "unselected_nodes": [],
                    "readiness_status": "NOT_ASSESSED", "constructive_reachable": [], "required_nodes": [],
                    "unused_outputs": [], "semantic_validity": "NOT_ASSESSED", "implementation_readiness": "NOT_ASSESSED"}
    if not isinstance(graph, dict):
        errors.append("architecture graph: expected mapping")
        return empty_report
    errors.extend(_shape_errors(graph))
    errors.extend(_context_shape_errors(context))
    if errors:
        errors[:] = sorted(set(errors))
        return empty_report
    if graph.get("schema_version") != SCHEMA_VERSION:
        errors.append("architecture graph: unsupported schema_version")
    for field in ("id", "revision", "selected_mechanism_id"):
        if not _text(graph.get(field)):
            errors.append(f"architecture graph: missing {field}")
    if graph.get("project_kind") not in {"NEW", "EXISTING"}:
        errors.append("architecture graph: project_kind must be NEW or EXISTING")
    mechanisms = _records(graph.get("mechanisms"), "mechanisms", errors)
    chosen = graph.get("selected_mechanism_id")
    if [key for key, value in mechanisms.items() if value.get("status") == "SELECTED"] != [chosen]:
        errors.append("architecture graph: exactly selected_mechanism_id must have SELECTED status")
    if any(value.get("status") not in {"SELECTED", "ALTERNATIVE"} for value in mechanisms.values()):
        errors.append("architecture mechanisms: invalid status")
    nodes = _records(graph.get("nodes"), "nodes", errors)
    edge_map = _records(graph.get("edges"), "edges", errors)
    contracts = _records(graph.get("contracts"), "contracts", errors)
    selected = {key: node for key, node in nodes.items() if node.get("mechanism_id") == chosen}
    plan_required = context.get("plan_required", True)
    tasks, checks, stages = (context.get(key, {}) for key in ("tasks", "checks", "stages"))
    for identifier, node in nodes.items():
        kind = node.get("kind")
        if kind not in NODE_KINDS:
            errors.append(f"architecture node {identifier}: invalid kind")
        for field in ("label", "subsystem_id"):
            if not _text(node.get(field)):
                errors.append(f"architecture node {identifier}: missing {field}")
        if node.get("mechanism_id") not in mechanisms:
            errors.append(f"architecture node {identifier}: unknown mechanism_id")
        if node.get("required") not in {"true", "false"}:
            errors.append(f"architecture node {identifier}: required must be quoted true or false")
        if identifier not in selected:
            continue
        if kind == "G" and node.get("existing_id") not in context.get("goal_ids", set()):
            errors.append(f"architecture node {identifier}: G must bind exact existing goal vector id")
        if kind == "R" and identifier not in context.get("requirements", set()):
            errors.append(f"architecture node {identifier}: R must reuse original requirement id")
        if kind in {"S", "T", "C"}:
            canonical = {"S": stages, "T": tasks, "C": checks}[kind]
            if plan_required and identifier not in canonical:
                errors.append(f"architecture node {identifier}: no canonical {kind} binding")
            if "existing_id" in node and node["existing_id"] != identifier:
                errors.append(f"architecture node {identifier}: mirrored {kind} id is forbidden")
            if kind in {"T", "C"}:
                if node.get("stage_id") not in selected or selected.get(node.get("stage_id"), {}).get("kind") != "S":
                    errors.append(f"architecture node {identifier}: missing canonical stage node")
                expected_stage = context.get("task_stage" if kind == "T" else "check_stage", {}).get(identifier)
                if plan_required and expected_stage != node.get("stage_id"):
                    errors.append(f"architecture node {identifier}: stage binding differs from plan")
            if kind == "C":
                if selected.get(node.get("task_id"), {}).get("kind") != "T":
                    errors.append(f"architecture check {identifier}: missing canonical task node")
                if plan_required and checks.get(identifier, {}).get("task_id") != node.get("task_id"):
                    errors.append(f"architecture check {identifier}: task binding differs from plan")
        if kind == "DATA" and node.get("contract_id") not in contracts:
            errors.append(f"architecture data {identifier}: missing contract")
    selected_g = {node.get("existing_id") for node in selected.values() if node.get("kind") == "G"}
    for identifier in sorted(set(context.get("goal_ids", set())) - selected_g):
        errors.append(f"architecture goal {identifier}: absent from selected mechanism")
    selected_r = {key for key, node in selected.items() if node.get("kind") == "R"}
    for identifier in sorted(set(context.get("requirements", set())) - selected_r):
        errors.append(f"architecture requirement {identifier}: lost from selected mechanism")
    if not selected_g:
        errors.append("architecture graph: no selected goal")

    selected_edges: list[dict] = []
    pair_kinds: dict[tuple[str, str, str], str] = {}
    for identifier, edge in edge_map.items():
        source, target, kind = edge.get("from_id"), edge.get("to_id"), edge.get("kind")
        if source not in nodes or target not in nodes:
            errors.append(f"architecture edge {identifier}: dangling endpoint")
            continue
        if kind not in EDGE_KINDS:
            errors.append(f"architecture edge {identifier}: invalid kind")
            continue
        key = (source, target, kind)
        if key in pair_kinds:
            errors.append(f"architecture edge {identifier}: duplicate typed relation {pair_kinds[key]}")
        pair_kinds[key] = identifier
        for field in ("meaning",):
            if not _text(edge.get(field)):
                errors.append(f"architecture edge {identifier}: missing {field}")
        if not _strings(edge.get("conditions")) or not _strings(edge.get("check_ids")):
            errors.append(f"architecture edge {identifier}: conditions and check_ids must be unique string lists")
        for check_id in edge.get("check_ids", []) if isinstance(edge.get("check_ids"), list) else []:
            if nodes.get(check_id, {}).get("kind") != "C":
                errors.append(f"architecture edge {identifier}: unknown check {check_id}")
        expected_scope = "RESEARCH" if kind == "RESEARCH_FEEDBACK" else "EVIDENCE" if kind in {"EVIDENCE_SUPPORT", "VERIFIES"} else "EXECUTION"
        if edge.get("scope") != expected_scope:
            errors.append(f"architecture edge {identifier}: {kind} requires {expected_scope} scope")
        source_kind, target_kind = nodes[source].get("kind"), nodes[target].get("kind")
        allowed = {
            "REALIZES": (source_kind in {"COMPONENT", "ALGORITHM"} and target_kind == "CAPABILITY"),
            "IMPLEMENTS": (source_kind == "T" and target_kind in {"COMPONENT", "ALGORITHM"}),
            "PRODUCES": (source_kind in {"T", "COMPONENT", "ALGORITHM"} and target_kind == "DATA"),
            "CONSUMES": (source_kind == "DATA" and target_kind in {"COMPONENT", "ALGORITHM", "T", "C"}),
            "CONTRIBUTES_TO": (source_kind in {"CAPABILITY", "DATA", "C"} and target_kind in {"G", "R"}),
            "VERIFIES": (source_kind == "C" and target_kind not in {"S", "G", "R"}),
        }.get(kind, True)
        if not allowed:
            errors.append(f"architecture edge {identifier}: invalid {kind} orientation")
        if kind in EXECUTION_KINDS and (source_kind == "S" or target_kind == "S"):
            errors.append(f"architecture edge {identifier}: S grouping must reuse canonical stage DAG")
        if kind in EXECUTION_KINDS and nodes[source].get("mechanism_id") != nodes[target].get("mechanism_id"):
            errors.append(f"architecture edge {identifier}: alternative cannot feed selected execution")
        if source in selected and target in selected:
            selected_edges.append(edge)
    execution = [edge for edge in selected_edges if edge.get("kind") in EXECUTION_KINDS]
    pairs = [(edge["from_id"], edge["to_id"]) for edge in execution]
    provenance = [(edge["from_id"], edge["to_id"]) for edge in edge_map.values() if edge.get("kind") == "EVIDENCE_SUPPORT" and edge.get("from_id") in nodes and edge.get("to_id") in nodes]
    acyclic, reachable = _helpers(context)
    if not callable(acyclic) or not callable(reachable):
        errors.append("architecture context: canonical acyclic and reachable callbacks required")
    else:
        if not acyclic(selected, pairs):
            errors.append("architecture selected execution: cycle has no constructive finite order")
        if not acyclic(nodes, provenance):
            errors.append("architecture evidence support: self-justification cycle")

    for identifier, contract in contracts.items():
        for field in ("version", "format", "semantics", "availability", "error_behavior"):
            if not _text(contract.get(field)):
                errors.append(f"architecture contract {identifier}: missing {field}")
        for field in ("external_conditions", "acceptance_check_ids"):
            if not _strings(contract.get(field)):
                errors.append(f"architecture contract {identifier}: {field} must be unique string list")
        acceptance = contract.get("acceptance_check_ids", [])
        if not acceptance:
            errors.append(f"architecture contract {identifier}: finite acceptance check required")
        for check_id in acceptance if isinstance(acceptance, list) else []:
            if nodes.get(check_id, {}).get("kind") != "C":
                errors.append(f"architecture contract {identifier}: unknown acceptance check {check_id}")
        refs = contract.get("plan_refs")
        if not isinstance(refs, list):
            errors.append(f"architecture contract {identifier}: plan_refs must be list")
            refs = []
        selected_contract = any(node.get("contract_id") == identifier for node in selected.values()) or any(edge.get("contract_id") == identifier for edge in selected_edges)
        if plan_required and selected_contract and not refs:
            errors.append(f"architecture contract {identifier}: canonical plan_ref required")
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("kind") not in {"INPUT", "OUTPUT"} or not _text(ref.get("contract_id")) or not _text(ref.get("stage_id")):
                errors.append(f"architecture contract {identifier}: malformed plan_ref")
                continue
            if not plan_required:
                continue
            prefix = "input" if ref["kind"] == "INPUT" else "output"
            canonical = context.get(prefix + "_contracts", {}).get(ref["contract_id"])
            stage = context.get(prefix + "_contract_stage", {}).get(ref["contract_id"])
            if not isinstance(canonical, dict) or stage != ref["stage_id"]:
                errors.append(f"architecture contract {identifier}: unresolved canonical plan_ref {ref['contract_id']}")
                continue
            acceptance_check = canonical.get("acceptance_check_id")
            if isinstance(acceptance_check, str) and acceptance_check not in contract.get("acceptance_check_ids", []):
                errors.append(f"architecture contract {identifier}: canonical acceptance check omitted")
            if contract.get("semantics") != canonical.get("contract"):
                errors.append(f"architecture contract {identifier}: semantics diverges from canonical plan_ref {ref['contract_id']}")
            for field in ("version", "format"):
                if field in canonical and contract.get(field) != canonical[field]:
                    errors.append(f"architecture contract {identifier}: {field} diverges from canonical plan_ref {ref['contract_id']}")
            if ref["kind"] == "OUTPUT" and ref.get("path") != canonical.get("path"):
                errors.append(f"architecture contract {identifier}: output path diverges from canonical plan_ref")

    seeds: set[str] = set()
    # Canonical branch state is a machine fact, unlike descriptive conditions.
    # An unselected task is retained in the plan but cannot manufacture outputs.
    unselected = {key for key, task in tasks.items() if task.get("execution_status") == "NOT_APPLICABLE" and key in selected}
    withdrawn_branch_errors = {}
    blocker_roots: dict[str, str] = {
        key: "TASK_" + task["execution_status"] for key, task in tasks.items()
        if key in selected and task.get("execution_status") in {"BLOCKED", "FAILED", "NEEDS_RECHECK"}
    }
    input_contracts, output_contracts = context.get("input_contracts", {}), context.get("output_contracts", {})
    for identifier, node in selected.items():
        if node.get("kind") != "DATA":
            continue
        for field in ("version", "format"):
            if field in node and node[field] != contracts.get(node.get("contract_id"), {}).get(field):
                errors.append(f"architecture data {identifier}: {field} differs from its contract")
        availability = node.get("availability")
        producers = [edge["from_id"] for edge in execution if edge.get("kind") == "PRODUCES" and edge.get("to_id") == identifier]
        if availability == "EXISTING":
            source_id, digest = node.get("source_id"), node.get("accepted_hash")
            expected = context.get("results", {}).get(source_id, {}).get("content_hash") or context.get("immutable_inputs", {}).get(source_id)
            if not _text(source_id) or not isinstance(digest, str) or not _HASH.fullmatch(digest) or expected != digest:
                errors.append(f"architecture data {identifier}: existing input requires exact accepted/immutable hash")
            elif source_id in set(context.get("stale_result_ids", [])):
                errors.append(f"architecture data {identifier}: existing input is stale")
            else:
                seeds.add(identifier)
            if contracts.get(node.get("contract_id"), {}).get("version") != node.get("version"):
                errors.append(f"architecture data {identifier}: version differs from its contract")
            if not _text(node.get("version")):
                errors.append(f"architecture data {identifier}: existing input requires version")
            if producers:
                errors.append(f"architecture data {identifier}: existing resource cannot also be a planned output")
        elif availability == "PLANNED":
            if "accepted_hash" in node or "source_id" in node:
                errors.append(f"architecture data {identifier}: planned output must not fabricate accepted identity/hash")
            if len(producers) != 1:
                errors.append(f"architecture data {identifier}: planned output requires exactly one producer")
            ref = node.get("plan_ref")
            if not isinstance(ref, dict) or not _text(ref.get("stage_id")) or not _text(ref.get("output_id")):
                errors.append(f"architecture data {identifier}: planned output requires canonical output plan_ref")
            elif plan_required:
                canonical = output_contracts.get(ref["output_id"])
                if not isinstance(canonical, dict) or context.get("output_contract_stage", {}).get(ref["output_id"]) != ref["stage_id"]:
                    errors.append(f"architecture data {identifier}: output plan_ref does not resolve")
                else:
                    output_availability = canonical.get("availability", "UNCONDITIONAL")
                    producer_status = tasks.get(canonical.get("producer_task_id"), {}).get("execution_status")
                    if output_availability == "UNSELECTED_BRANCH":
                        unselected.add(identifier)
                    elif producer_status == "NOT_APPLICABLE":
                        unselected.add(identifier)
                        errors.append(f"architecture data {identifier}: NOT_APPLICABLE producer cannot declare selected/unconditional output")
                    if output_availability == "SELECTED_BRANCH" and (
                        canonical.get("branch_selection_status") != "SELECTED"
                        or not _reviewed_branch(canonical, context)
                    ):
                        blocker_roots[identifier] = "BRANCH_PROOF_UNAVAILABLE"
                        error = f"architecture data {identifier}: selected branch requires current independently reviewed E"
                        errors.append(error)
                        if (canonical.get("branch_selection_status") == "SELECTED"
                            and _reviewed_branch(canonical, {**context, "stale_result_ids": []})):
                            withdrawn_branch_errors[identifier] = error
                    producer_tasks = set().union(*(_producer_tasks(producer, selected, execution) for producer in producers)) if producers else set()
                    if producer_tasks != {canonical.get("producer_task_id")}:
                        errors.append(f"architecture data {identifier}: producer differs from canonical output")
                    contract = contracts.get(node.get("contract_id"), {})
                    if not any(isinstance(item, dict) and item.get("kind") == "OUTPUT" and item.get("contract_id") == ref["output_id"] and item.get("stage_id") == ref["stage_id"] for item in contract.get("plan_refs", [])):
                        errors.append(f"architecture data {identifier}: output contract lacks same canonical plan_ref")
        elif availability == "EXTERNAL":
            if "accepted_hash" in node:
                errors.append(f"architecture data {identifier}: external condition is not an accepted result hash")
            for field in ("source", "owner", "availability_condition"):
                if not _text(node.get(field)):
                    errors.append(f"architecture data {identifier}: external input missing {field}")
            if node.get("availability_status") not in {"AVAILABLE", "UNAVAILABLE"}:
                errors.append(f"architecture data {identifier}: external availability_status invalid")
            elif node.get("availability_status") == "AVAILABLE":
                if _accepted_evidence(node.get("availability_evidence_id"), context) is None:
                    errors.append(f"architecture data {identifier}: external availability requires accepted E")
                else:
                    seeds.add(identifier)
            elif "availability_evidence_id" in node:
                errors.append(f"architecture data {identifier}: unavailable external input cannot claim available evidence")
            else:
                blocker_roots[identifier] = "EXTERNAL_UNAVAILABLE"
            if producers:
                errors.append(f"architecture data {identifier}: external input cannot have local producer")
        else:
            errors.append(f"architecture data {identifier}: invalid availability")

    for edge in execution:
        if edge.get("kind") != "CONSUMES":
            continue
        source, target = edge["from_id"], edge["to_id"]
        producer_contract, consumer_contract = selected[source].get("contract_id"), edge.get("contract_id")
        if consumer_contract not in contracts:
            errors.append(f"architecture edge {edge['id']}: missing consumer contract")
        if producer_contract != consumer_contract:
            check_id = edge.get("compatibility_check_id")
            canonical_check = checks.get(check_id, {}) if plan_required else selected.get(check_id, {})
            if selected.get(check_id, {}).get("kind") != "C" or set(canonical_check.get("compatibility_contract_ids", [])) != {producer_contract, consumer_contract} or not _text(canonical_check.get("procedure")) or not _text(canonical_check.get("expected_observation")):
                errors.append(f"architecture edge {edge['id']}: producer/consumer contract mismatch without explicit compatibility check")
        if not plan_required:
            continue
        target_kind = selected[target].get("kind")
        if target_kind not in {"T", "C"}:
            continue
        canonical_consumer = (tasks if target_kind == "T" else checks).get(target, {})
        input_id = edge.get("input_id")
        resolved = resolve_plan_input(input_id, input_contracts, output_contracts)
        declared = [resolve_plan_input(reference, input_contracts, output_contracts)
                    for reference in canonical_consumer.get("inputs", [])]
        matched = [binding for binding in declared
                   if _input_key(binding) is not None and _input_key(binding) == _input_key(resolved)]
        label = "task" if target_kind == "T" else "check"
        if resolved is None or not matched:
            errors.append(f"architecture edge {edge['id']}: canonical {label} input binding missing")
            continue
        # Validate the declared INPUT wrapper as well as an equivalent OUTPUT
        # alias on the edge. An alias must never erase the wrapper's identity,
        # consumer permissions, type, version or external availability.
        bindings = {(binding["reference_kind"], binding["contract_id"]): binding
                    for binding in [resolved, *matched]}
        consumer = contracts.get(consumer_contract, {})
        data = selected[source]
        for binding in bindings.values():
            canonical_input = binding["contract"]
            permitted = target in canonical_input.get("consumer_ids", [])
            if (target_kind == "C" and binding["reference_kind"] == "OUTPUT"
                    and canonical_input.get("acceptance_check_id") == target):
                permitted = True
            if not permitted:
                errors.append(f"architecture edge {edge['id']}: canonical {label} input consumer binding missing")
            direction, contract_id = binding["reference_kind"], binding["contract_id"]
            canonical_stage = context.get("input_contract_stage" if direction == "INPUT" else "output_contract_stage", {}).get(contract_id)
            if not any(isinstance(item, dict) and item.get("kind") == direction and item.get("contract_id") == contract_id and item.get("stage_id") == canonical_stage for item in consumer.get("plan_refs", [])):
                errors.append(f"architecture edge {edge['id']}: consumer contract lacks same canonical {direction.lower()} plan_ref")
            if consumer.get("semantics") != canonical_input.get("contract"):
                errors.append(f"architecture edge {edge['id']}: semantics differs from canonical input")
            for field in ("format", "version"):
                if field in canonical_input and consumer.get(field) != canonical_input[field]:
                    errors.append(f"architecture edge {edge['id']}: {field} differs from canonical input")
            if binding["input_kind"] != data.get("availability"):
                errors.append(f"architecture edge {edge['id']}: availability differs from canonical input")
            if data.get("availability") == "EXISTING":
                if canonical_input.get("source_id") != data.get("source_id") or canonical_input.get("accepted_hash") != data.get("accepted_hash"):
                    errors.append(f"architecture edge {edge['id']}: existing identity differs from canonical input")
                # Explicit compatibility C may bridge producer and consumer
                # versions, but equal contract IDs cannot hide version drift.
                if producer_contract == consumer_contract and canonical_input.get("version") != data.get("version"):
                    errors.append(f"architecture edge {edge['id']}: version differs from canonical input")
            if data.get("availability") == "EXTERNAL":
                for field in ("source", "owner", "availability_condition", "availability_status", "availability_evidence_id"):
                    if canonical_input.get(field) != data.get(field):
                        errors.append(f"architecture edge {edge['id']}: external {field} differs from canonical input")
                if data.get("availability_condition") not in consumer.get("external_conditions", []):
                    errors.append(f"architecture edge {edge['id']}: external condition omitted from contract")
                if data.get("availability_status") == "UNAVAILABLE":
                    if set(canonical_input.get("blocked_consumer_ids", [])) != set(canonical_input.get("consumer_ids", [])):
                        errors.append(f"architecture edge {edge['id']}: unavailable canonical consumers must be explicitly blocked")
                    status = canonical_consumer.get("execution_status", canonical_consumer.get("status"))
                    if status in {"RUNNING", "ACCEPTED", "PASS"}:
                        errors.append(f"architecture edge {edge['id']}: unavailable consumer cannot claim executed status")
                elif canonical_input.get("blocked_consumer_ids"):
                    errors.append(f"architecture edge {edge['id']}: available input cannot retain blocked canonical consumers")
            if data.get("availability") == "PLANNED":
                if binding["output_id"] != data.get("plan_ref", {}).get("output_id"):
                    errors.append(f"architecture edge {edge['id']}: planned output differs from canonical input")
                output = output_contracts.get(binding.get("output_id"), {})
                if binding["reference_kind"] == "INPUT":
                    for field in ("producer_task_id", "acceptance_check_id"):
                        if canonical_input.get(field) != output.get(field):
                            errors.append(f"architecture edge {edge['id']}: planned {field} differs from canonical output")

    if plan_required:
        for kind, canonical in (("S", stages), ("T", tasks), ("C", checks)):
            represented_ids = {key for key, node in selected.items() if node.get("kind") == kind}
            for identifier in sorted(set(canonical) - represented_ids):
                errors.append(f"architecture plan {identifier}: canonical {kind} omitted from selected graph")
        for requirement in sorted(context.get("requirements", set())):
            if not any(requirement in task.get("r_refs", []) for task in tasks.values()):
                errors.append(f"architecture requirement {requirement}: absent from canonical task coverage")
        for identifier, node in selected.items():
            if node.get("kind") == "T":
                represented = {_input_key(resolve_plan_input(edge.get("input_id"), input_contracts, output_contracts))
                               for edge in execution if edge.get("kind") == "CONSUMES" and edge.get("to_id") == identifier}
                missing = {reference for reference in tasks.get(identifier, {}).get("inputs", [])
                           if _input_key(resolve_plan_input(reference, input_contracts, output_contracts)) is None
                           or _input_key(resolve_plan_input(reference, input_contracts, output_contracts)) not in represented}
                represented_outputs = {output_contracts[item.get("plan_ref", {}).get("output_id")].get("path") for item in selected.values() if item.get("kind") == "DATA" and isinstance(item.get("plan_ref"), dict) and item["plan_ref"].get("output_id") in output_contracts and output_contracts[item["plan_ref"]["output_id"]].get("producer_task_id") == identifier}
                if set(tasks.get(identifier, {}).get("outputs", [])) - represented_outputs:
                    errors.append(f"architecture task {identifier}: omitted canonical output")
                if missing:
                    errors.append(f"architecture task {identifier}: omitted mandatory plan inputs {','.join(sorted(missing))}")
            elif node.get("kind") == "C":
                represented = {_input_key(resolve_plan_input(edge.get("input_id"), input_contracts, output_contracts))
                               for edge in execution if edge.get("kind") == "CONSUMES" and edge.get("to_id") == identifier}
                missing = {reference for reference in checks.get(identifier, {}).get("inputs", [])
                           if _input_key(resolve_plan_input(reference, input_contracts, output_contracts)) is None
                           or _input_key(resolve_plan_input(reference, input_contracts, output_contracts)) not in represented}
                if missing:
                    errors.append(f"architecture check {identifier}: omitted mandatory plan inputs {','.join(sorted(missing))}")
    # The accepted graph must carry every canonical order obligation, including
    # local_execution_graph edges. A forward execution path is sufficient: all
    # intermediate prerequisites are conjunctive in reachability and participate
    # in impact/views. No duplicate task.dependencies declaration is required.
    normalized_order = normalize_plan_order(stages, tasks)
    task_pairs = normalized_order["task_edges"]
    complete_task_pairs = sorted(set(task_pairs) | set(normalized_order["stage_task_edges"]))
    supplied_pairs = context.get("task_order_edges")
    if supplied_pairs is not None:
        if (not isinstance(supplied_pairs, list)
                or any(not isinstance(pair, (tuple, list)) or len(pair) != 2
                       or any(not isinstance(item, str) or item not in tasks for item in pair)
                       for pair in supplied_pairs)):
            errors.append("architecture context.task_order_edges: expected canonical task pairs")
        elif set(map(tuple, supplied_pairs)) != set(task_pairs):
            errors.append("architecture context.task_order_edges: differs from canonical normalized order")
    if plan_required and callable(reachable):
        for source, target in complete_task_pairs:
            if source not in selected or target not in selected or target not in reachable(pairs, source):
                errors.append(f"architecture task {target}: omitted canonical dependency/order {source}")
        for source, target in normalized_order["stage_check_edges"]:
            if selected.get(source, {}).get("kind") != "C" or target not in selected or target not in reachable(pairs, source):
                errors.append(f"architecture task {target}: omitted canonical predecessor-stage acceptance {source}")
    if plan_required and callable(acyclic) and not acyclic(tasks, complete_task_pairs):
        errors.append("architecture canonical task order: cycle has no finite order")
    if plan_required and callable(reachable):
        for edge in execution:
            if edge.get("kind") != "CONSUMES" or selected[edge["from_id"]].get("availability") != "PLANNED":
                continue
            sources = [item["from_id"] for item in execution if item.get("kind") == "PRODUCES" and item.get("to_id") == edge["from_id"]]
            producer_tasks = set().union(*(_producer_tasks(item, selected, execution) for item in sources)) if sources else set()
            target = edge["to_id"]
            consumers = _producer_tasks(target, selected, execution) if selected[target].get("kind") != "C" else {selected[target].get("task_id")}
            for producer in sorted(producer_tasks):
                for consumer in sorted(item for item in consumers if isinstance(item, str)):
                    if consumer != producer and consumer not in reachable(complete_task_pairs, producer):
                        errors.append(f"architecture edge {edge['id']}: consumer precedes availability of producer {producer}")
            # Production and acceptance are different milestones. The
            # producer's own checks must be able to inspect its raw output;
            # subsequent tasks/checks consume the accepted artifact. Require
            # the accepted graph to express that dependency so graph-only
            # reachability, impact and views all observe the same obligation.
            output_ref = selected[edge["from_id"]].get("plan_ref", {})
            output = output_contracts.get(output_ref.get("output_id"), {}) if isinstance(output_ref, dict) else {}
            acceptance_id = output.get("acceptance_check_id")
            producer_id = output.get("producer_task_id")
            own_acceptance = selected[target].get("kind") == "C" and checks.get(target, {}).get("task_id") == producer_id
            if not own_acceptance and _text(acceptance_id):
                if selected.get(acceptance_id, {}).get("kind") != "C" or target not in reachable(pairs, acceptance_id):
                    errors.append(f"architecture edge {edge['id']}: consumer precedes acceptance check {acceptance_id}")
    for identifier, node in selected.items():
        if node.get("kind") in {"COMPONENT", "ALGORITHM"} and not _producer_tasks(identifier, selected, execution):
            errors.append(f"architecture mechanism {identifier}: no implementing canonical task")

    # Only the implementation owned entirely by an unselected task is excluded.
    # Branch-selection checks may still inspect an existing input and remain
    # constructive, even when their associated execution task is not applicable.
    changed = True
    while changed:
        changed = False
        for identifier, node in selected.items():
            if identifier in unselected:
                continue
            kind = node.get("kind")
            producers = set()
            if kind in {"COMPONENT", "ALGORITHM"}:
                producers = _producer_tasks(identifier, selected, execution)
            elif kind in {"DATA", "CAPABILITY"}:
                relation = "PRODUCES" if kind == "DATA" else "REALIZES"
                producers = {edge["from_id"] for edge in execution if edge.get("kind") == relation and edge["to_id"] == identifier}
            if producers and producers <= unselected:
                unselected.add(identifier)
                changed = True
    for edge in execution:
        if edge["from_id"] in unselected and edge["to_id"] not in unselected:
            errors.append(f"architecture edge {edge['id']}: unselected branch cannot discharge active dependency {edge['to_id']}")

    incoming: dict[str, set[str]] = {identifier: set() for identifier in selected}
    for source, target in pairs:
        incoming[target].add(source)
    for identifier in sorted(seeds):
        if incoming[identifier]:
            errors.append(f"architecture data {identifier}: initial resource has execution prerequisites")
    seeds = {identifier for identifier in seeds if not incoming[identifier]}
    constructed = set(seeds)
    # AND fixed point: a path from one available input cannot discharge another.
    changed = True
    while changed:
        changed = False
        for identifier, node in sorted(selected.items()):
            if identifier in constructed or identifier in unselected or identifier in blocker_roots or node.get("kind") == "S" or not incoming[identifier]:
                continue
            if incoming[identifier] <= constructed:
                constructed.add(identifier)
                changed = True
    roots = {key for key, node in selected.items() if key not in unselected and (node.get("kind") in {"G", "R"} or node.get("required") == "true" and node.get("kind") != "S")}
    # Acceptance is a canonical obligation, not an optional graph label. Keep
    # it separate from production: a future check may read the artifact that its
    # producer has yet to create. Making C a required root preserves that finite
    # order without manufacturing C -> producer admission cycles or demanding a
    # runtime PASS while the plan is being validated.
    mandatory_checks: set[str] = set()
    active_edges = [edge for edge in selected_edges
                    if edge["from_id"] not in unselected and edge["to_id"] not in unselected]
    used_nodes = roots | {edge[endpoint] for edge in active_edges
                          if edge.get("kind") in EXECUTION_KINDS
                          for endpoint in ("from_id", "to_id")}
    active_contracts = {selected[key].get("contract_id") for key in used_nodes
                        if selected[key].get("contract_id") in contracts}
    for edge in active_edges:
        mandatory_checks.update(edge.get("check_ids", []))
        if edge.get("contract_id") in contracts:
            active_contracts.add(edge["contract_id"])
        if _text(edge.get("compatibility_check_id")):
            mandatory_checks.add(edge["compatibility_check_id"])
    for contract_id in active_contracts:
        mandatory_checks.update(contracts[contract_id].get("acceptance_check_ids", []))
    if plan_required:
        for task_id, task in tasks.items():
            if task_id in selected and task_id not in unselected:
                for field in ("checks", "acceptance_criteria"):
                    mandatory_checks.update(task.get(field, []))
        for identifier, node in selected.items():
            if identifier in unselected or node.get("kind") != "DATA":
                continue
            ref = node.get("plan_ref", {})
            if node.get("availability") == "PLANNED" and isinstance(ref, dict):
                check_id = output_contracts.get(ref.get("output_id"), {}).get("acceptance_check_id")
                if _text(check_id):
                    mandatory_checks.add(check_id)
    for check_id in sorted(mandatory_checks):
        if selected.get(check_id, {}).get("kind") != "C" or check_id in unselected:
            errors.append(f"architecture acceptance {check_id}: mandatory check is not in selected active mechanism")
        else:
            roots.add(check_id)
    required = set(roots)
    if callable(reachable):
        reverse = [(target, source) for source, target in pairs]
        for root in sorted(roots):
            required.update(reachable(reverse, root))
    # Known missing resources and explicit canonical blocked task state constrain
    # reachability, while preserving an otherwise coherent conditional model.
    # Missing producers, cycles and unexplained disconnected nodes remain errors.
    blocked_by = {identifier: {identifier} for identifier in blocker_roots if identifier not in unselected}
    changed = True
    while changed:
        changed = False
        for identifier in sorted(selected):
            if identifier in constructed or identifier in unselected:
                continue
            causes = set().union(*(blocked_by.get(source, set()) for source in incoming[identifier])) if incoming[identifier] else set()
            combined = blocked_by.get(identifier, set()) | causes
            if combined and combined != blocked_by.get(identifier):
                blocked_by[identifier] = combined
                changed = True
    for identifier in sorted(required - constructed):
        if identifier not in blocked_by:
            errors.append(f"architecture node {identifier}: not constructively reachable from available inputs")
    goal_cone: set[str] = set()
    if callable(reachable):
        reverse = [(target, source) for source, target in pairs]
        for identifier, node in selected.items():
            if node.get("kind") in {"G", "R"}:
                goal_cone.update(reachable(reverse, identifier))
    realized_capabilities = {
        edge["to_id"] for edge in execution
        if edge.get("kind") == "REALIZES"
        and selected[edge["from_id"]].get("kind") in {"COMPONENT", "ALGORITHM"}
        and selected[edge["to_id"]].get("kind") == "CAPABILITY"
        and _producer_tasks(edge["from_id"], selected, execution)
    }
    for identifier in sorted(required):
        if selected[identifier].get("kind") == "CAPABILITY" and identifier not in realized_capabilities and identifier not in unselected:
            errors.append(f"architecture capability {identifier}: no REALIZES producer from task-bound component/algorithm")
    for identifier, node in selected.items():
        if node.get("kind") == "R":
            satisfiers = {
                edge["from_id"] for edge in execution
                if edge.get("kind") == "CONTRIBUTES_TO" and edge["to_id"] == identifier
                and selected[edge["from_id"]].get("kind") in {"CAPABILITY", "DATA", "C"}
            }
            if not (satisfiers & constructed) and (not satisfiers or identifier not in blocked_by):
                errors.append(f"architecture requirement {identifier}: no constructive CONTRIBUTES_TO satisfier")
        if node.get("kind") == "G":
            functional_caps = {
                edge["from_id"] for edge in execution
                if edge.get("kind") == "CONTRIBUTES_TO" and edge["to_id"] == identifier
                and selected[edge["from_id"]].get("kind") == "CAPABILITY"
            }
            if not (functional_caps & realized_capabilities & constructed) and (not (functional_caps & realized_capabilities) or identifier not in blocked_by):
                errors.append(f"architecture goal {identifier}: no complete functional mechanism path")
    unused = sorted(key for key, node in selected.items() if key not in unselected and node.get("kind") == "DATA" and node.get("availability") == "PLANNED" and (not any(edge["from_id"] == key for edge in execution) or key not in goal_cone))
    for identifier in unused:
        errors.append(f"architecture output {identifier}: produced result is unused in required G/R path")
    comparison = compare_architecture_implementation(graph, context=context)
    errors.extend(comparison["errors"])
    errors[:] = sorted(set(errors))
    blockers = [{"node_id": identifier, "reason": blocker_roots.get(identifier, "BLOCKED_PREREQUISITES"),
                 "blocked_by": sorted(blocked_by[identifier])} for identifier in sorted(required & blocked_by.keys())]
    return {"structural_status": "FAIL" if errors else "PASS", "model_integrity_status": "FAIL" if errors else "PASS",
            "errors": errors, "reachability_status": "BLOCKED" if required - constructed else "COMPLETE",
            "blockers": blockers, "blocked_nodes": sorted(blocked_by), "unselected_nodes": sorted(unselected),
            "readiness_status": "BLOCKED" if blockers else "NOT_ASSESSED", "constructive_reachable": sorted(constructed),
            "required_nodes": sorted(required), "unused_outputs": unused, "semantic_validity": "NOT_ASSESSED", "implementation_readiness": "NOT_ASSESSED",
            **({"withdrawn_branch_errors": withdrawn_branch_errors} if withdrawn_branch_errors else {})}


def compare_architecture_implementation(graph: dict[str, Any], implementation_map: Any = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive discrepancy coverage; never infer code behavior from graph shape."""
    context = context or {}
    _, selected, edges = _selection(graph)
    expected = {key for key, node in selected.items() if node.get("kind") in {"COMPONENT", "ALGORITHM"}} | {edge["id"] for edge in edges if edge.get("kind") in EXECUTION_KINDS and _text(edge.get("id"))}
    entries = graph.get("implementation_map") if implementation_map is None else implementation_map
    errors: list[str] = []
    if not isinstance(entries, list):
        errors.append("architecture implementation_map: expected list")
        entries = []
    seen: set[str] = set()
    discrepancies: list[dict[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not _text(entry.get("target_id")):
            errors.append(f"architecture implementation_map[{index}]: missing target_id")
            continue
        target, status = entry["target_id"], entry.get("status")
        if not isinstance(status, str):
            errors.append(f"architecture implementation {target}: status must be string")
            continue
        if target in seen:
            errors.append(f"architecture implementation {target}: duplicate target")
        seen.add(target)
        if status not in IMPLEMENTATION_STATUSES:
            errors.append(f"architecture implementation {target}: invalid status")
        if target not in expected and status != "EXTRA_UNJUSTIFIED":
            errors.append(f"architecture implementation {target}: extra must be EXTRA_UNJUSTIFIED")
        if target in expected and status == "EXTRA_UNJUSTIFIED":
            errors.append(f"architecture implementation {target}: normative target cannot be an extra")
        for field in ("evidence_scope", "required_action"):
            if not _text(entry.get(field)):
                errors.append(f"architecture implementation {target}: missing {field}")
        if not _strings(entry.get("evidence_ids")):
            errors.append(f"architecture implementation {target}: evidence_ids must be unique string list")
        files = entry.get("files")
        file_hashes: dict[str, str] = {}
        if not isinstance(files, list):
            errors.append(f"architecture implementation {target}: files must be list")
            files = []
        for record in files:
            if not isinstance(record, dict) or not _text(record.get("path")) or not isinstance(record.get("sha256"), str) or not _HASH.fullmatch(record["sha256"]):
                errors.append(f"architecture implementation {target}: exact file path and sha256 required")
                continue
            if record["path"] in file_hashes:
                errors.append(f"architecture implementation {target}: duplicate file path")
            file_hashes[record["path"]] = record["sha256"]
            observed = context.get("implementation_files", {}).get(record["path"])
            if observed is not None and observed != record["sha256"]:
                errors.append(f"architecture implementation {target}: observed file hash drift {record['path']}")
        evidence = []
        for identifier in entry.get("evidence_ids", []) if isinstance(entry.get("evidence_ids"), list) else []:
            doc = _accepted_evidence(identifier, context)
            if doc is None:
                errors.append(f"architecture implementation {target}: evidence {identifier} is not current accepted E")
            else:
                evidence.append(doc)
        if status in {"IMPLEMENTED_UNQUALIFIED", "CONTRACT_DIVERGENCE", "EXTRA_UNJUSTIFIED"} and (not file_hashes or not evidence):
            errors.append(f"architecture implementation {target}: observed implementation/discrepancy requires exact files and accepted E")
        basis = entry.get("verification_basis")
        if not isinstance(basis, str) or basis not in {"INTEGRATION_CHECK", "STATIC_ONLY", "NONE"}:
            errors.append(f"architecture implementation {target}: invalid verification_basis")
        if status == "IMPLEMENTED_VERIFIED":
            if basis != "INTEGRATION_CHECK" or not file_hashes or not evidence:
                errors.append(f"architecture implementation {target}: verified requires integration-check evidence and exact files")
            else:
                qualified = False
                for doc in evidence:
                    check = doc.get("integration_check")
                    if isinstance(check, dict) and check.get("status") == "PASS" and _text(check.get("check_id")) and _strings(check.get("target_ids")) and target in check["target_ids"] and isinstance(check.get("file_hashes"), dict) and check["file_hashes"] == file_hashes and _text(doc.get("procedure")) and _text(doc.get("observation")):
                        qualified = True
                if not qualified:
                    errors.append(f"architecture implementation {target}: accepted E lacks exact passing integration check for target/files")
        if status != "IMPLEMENTED_VERIFIED":
            discrepancies.append({"target_id": target, "status": str(status), "required_action": str(entry.get("required_action", ""))})
    missing = sorted(expected - seen) if graph.get("project_kind") == "EXISTING" else []
    for target in missing:
        errors.append(f"architecture implementation {target}: selected component/material edge missing from map")
    return {"errors": sorted(set(errors)), "coverage_missing": missing, "discrepancies": sorted(discrepancies, key=lambda value: value["target_id"]), "implementation_readiness": "NOT_ASSESSED", "limits": ["Mapping and accepted evidence do not prove global implementation or central-mechanism validity.", "Current file bytes are compared only when implementation_files observations are supplied."]}


def _view_selection(graph: dict[str, Any], subsystem_id: str | None, goal_path: bool) -> tuple[dict, list[dict]]:
    _, nodes, edges = _selection(graph)
    pairs = [(edge["from_id"], edge["to_id"]) for edge in edges if edge.get("kind") in EXECUTION_KINDS]
    included = set(nodes)
    if subsystem_id is not None:
        included = {key for key, node in nodes.items() if node.get("subsystem_id") == subsystem_id}
        if not included:
            raise ValueError(f"unknown selected architecture subsystem: {subsystem_id}")
    if goal_path:
        goals = [key for key, node in nodes.items() if node.get("kind") == "G"]
        goal_cone: set[str] = set()
        for key in goals:
            goal_cone.update(_projection_reachable([(target, source) for source, target in pairs], key))
        if subsystem_id is not None:
            descendants: set[str] = set()
            for key in included:
                descendants.update(_projection_reachable(pairs, key))
            included = descendants & goal_cone
        else:
            included &= goal_cone
    # A subsystem projection must expose its interfaces.  Keep one-hop external
    # canonical endpoints, without recursively expanding the external subsystem.
    # These are display ports, never new resources or canonical graph records.
    visible = set(included)
    if subsystem_id is not None:
        for edge in edges:
            if (edge["from_id"] in included) != (edge["to_id"] in included):
                visible.update((edge["from_id"], edge["to_id"]))
    return {key: nodes[key] for key in sorted(visible)}, [edge for edge in edges if edge["from_id"] in visible and edge["to_id"] in visible and (subsystem_id is None or edge["from_id"] in included or edge["to_id"] in included)]


def derive_architecture_views(graph: dict[str, Any], *, subsystem_id: str | None = None, goal_path: bool = False) -> dict[str, Any]:
    nodes, edges = _view_selection(graph, subsystem_id, goal_path)
    contracts = {record["id"]: record for record in graph.get("contracts", []) if isinstance(record, dict) and _text(record.get("id"))}
    internal = {key for key, node in nodes.items() if subsystem_id is None or node.get("subsystem_id") == subsystem_id}
    boundary = []
    for edge in edges:
        if (edge["from_id"] in internal) != (edge["to_id"] in internal):
            outbound = edge["from_id"] in internal
            boundary.append({**dict(edge), "direction": "OUTBOUND" if outbound else "INBOUND", "inside_node_id": edge["from_id"] if outbound else edge["to_id"], "outside_node_id": edge["to_id"] if outbound else edge["from_id"]})
    boundary.sort(key=lambda edge: edge.get("id", ""))
    boundary_ids = {edge["id"] for edge in boundary}
    boundary_nodes = {edge["outside_node_id"] for edge in boundary}
    kinds = {
        "functional": {"G", "R", "CAPABILITY", "COMPONENT", "ALGORITHM"},
        "data_contracts": {"DATA", "COMPONENT", "ALGORITHM", "T", "C"},
        "implementation": {"S", "T", "C", "COMPONENT", "ALGORITHM", "DATA"},
    }
    # The compact overview projects task/data dependencies onto their explicitly
    # implementing modules.  Reverse traversal is ownership-only and recorded in
    # every path; a VIEW_FLOW is not a new canonical execution edge.
    owners: dict[str, list[dict]] = {}
    producers: dict[str, list[dict]] = {}
    for edge in sorted(edges, key=lambda item: item.get("id", "")):
        if edge.get("kind") == "IMPLEMENTS" and nodes[edge["from_id"]].get("kind") == "T" and nodes[edge["to_id"]].get("kind") in {"COMPONENT", "ALGORITHM"}:
            owners.setdefault(edge["from_id"], []).append(edge)
        if edge.get("kind") == "PRODUCES":
            producers.setdefault(edge["to_id"], []).append(edge)
    flows: dict[tuple[str, str], list[dict]] = {}
    for edge in sorted(edges, key=lambda item: item.get("id", "")):
        paths = []
        if edge.get("kind") == "DEPENDS_ON":
            paths.append((edge["from_id"], edge["to_id"], [edge]))
        elif edge.get("kind") == "CONSUMES":
            for producer in producers.get(edge["from_id"], []):
                paths.append((producer["from_id"], edge["to_id"], [producer, edge]))
        for source, target, middle in paths:
            for first in owners.get(source, []):
                for last in owners.get(target, []):
                    pair = (first["to_id"], last["to_id"])
                    if pair[0] == pair[1]:
                        continue
                    flows.setdefault(pair, []).append({"node_ids": [pair[0], source] + [item["to_id"] for item in middle] + [pair[1]], "steps": [{"edge_id": first["id"], "direction": "REVERSE_OWNERSHIP"}] + [{"edge_id": item["id"], "direction": "FORWARD"} for item in middle] + [{"edge_id": last["id"], "direction": "FORWARD"}]})
    result = {}
    for name, allowed in kinds.items():
        # In a subsystem view interface visibility takes precedence over the
        # overview's kind filter.  Every crossing has its actual two endpoints.
        members = {key: node for key, node in nodes.items() if node.get("kind") in allowed or (bool(boundary) and (key in internal or key in boundary_nodes))}
        view_edges = [dict(edge) for edge in edges if edge["from_id"] in members and edge["to_id"] in members]
        if name == "data_contracts":
            view_edges = [edge for edge in view_edges if edge.get("kind") in {"PRODUCES", "CONSUMES", "VERIFIES", "DEPENDS_ON", "REQUIRES"} or edge.get("id") in boundary_ids]
        if name == "implementation":
            view_edges = [edge for edge in view_edges if edge.get("kind") in {"IMPLEMENTS", "PRODUCES", "CONSUMES", "VERIFIES", "DEPENDS_ON", "REQUIRES"} or edge.get("id") in boundary_ids]
        if name == "functional" and not boundary:
            reserved_ids = {edge.get("id") for edge in graph.get("edges", []) if isinstance(edge, dict)}
            for index, ((source, target), paths) in enumerate(sorted(flows.items())):
                if source in members and target in members:
                    identifier = f"VIEW-FLOW-{index + 1:03d}"
                    while identifier in reserved_ids:
                        identifier += "-DERIVED"
                    reserved_ids.add(identifier)
                    view_edges.append({"id": identifier, "kind": "VIEW_FLOW", "from_id": source, "to_id": target, "meaning": "Module dependency projected through canonical task ownership and execution/data edges", "scope": "DERIVED_VIEW", "derived": "true", "source_edge_ids": sorted({step["edge_id"] for path in paths for step in path["steps"]}), "paths": paths})
        used_contracts = {node.get("contract_id") for node in members.values()} | {edge.get("contract_id") for edge in view_edges}
        detail_contracts = {node.get("contract_id") for node in nodes.values()} | {edge.get("contract_id") for edge in edges}
        result[name] = {"graph_id": graph.get("id"), "graph_revision": graph.get("revision"), "nodes": [dict(members[key]) for key in sorted(members)], "edges": sorted(view_edges, key=lambda edge: edge.get("id", "")), "contracts": [dict(contracts[key]) for key in sorted(key for key in used_contracts if key in contracts)], "derived": "true", "internal_node_ids": sorted(internal & set(members)), "boundary_node_ids": sorted(boundary_nodes & set(members)), "boundary_edges": [dict(edge) for edge in boundary], "boundary_edge_count": len(boundary), "detail": {"nodes": [dict(nodes[key]) for key in sorted(nodes)], "edges": sorted((dict(edge) for edge in edges), key=lambda edge: edge.get("id", "")), "contracts": [dict(contracts[key]) for key in sorted(key for key in detail_contracts if key in contracts)]}, "projection_limits": ["Boundary ports reuse canonical nodes; VIEW_FLOW links are derived ownership projections, not new canonical execution edges."]}
        if name == "functional":
            result[name]["mechanisms"] = sorted((dict(item) for item in graph.get("mechanisms", [])), key=lambda item: item["id"])
            alternatives = []
            all_nodes, _, _ = _selection(graph)
            for mechanism in result[name]["mechanisms"]:
                if mechanism.get("status") != "ALTERNATIVE":
                    continue
                candidates = {key: node for key, node in all_nodes.items() if node.get("mechanism_id") == mechanism["id"] and (subsystem_id is None or node.get("subsystem_id") == subsystem_id)}
                alt_nodes = {key: node for key, node in candidates.items() if node.get("kind") in allowed}
                if not alt_nodes:
                    alt_nodes = candidates
                alt_edges = [dict(edge) for edge in graph.get("edges", []) if isinstance(edge, dict) and isinstance(edge.get("from_id"), str) and isinstance(edge.get("to_id"), str) and edge["from_id"] in alt_nodes and edge["to_id"] in alt_nodes]
                detail_edges = [dict(edge) for edge in graph.get("edges", []) if isinstance(edge, dict) and edge.get("from_id") in candidates and edge.get("to_id") in candidates]
                alternatives.append({"mechanism_id": mechanism["id"], "nodes": [dict(alt_nodes[key]) for key in sorted(alt_nodes)], "edges": sorted(alt_edges, key=lambda edge: edge.get("id", "")), "detail_nodes": [dict(candidates[key]) for key in sorted(candidates)], "detail_edges": sorted(detail_edges, key=lambda edge: edge.get("id", ""))})
            result[name]["alternatives"] = alternatives
    return result


def render_architecture_mermaid(graph: dict[str, Any], view: str, *, subsystem_id: str | None = None, goal_path: bool = False) -> str:
    if view == "data":
        view = "data_contracts"
    views = derive_architecture_views(graph, subsystem_id=subsystem_id, goal_path=goal_path)
    if view not in views:
        raise ValueError(f"unknown architecture view: {view}")
    data = views[view]
    aliases = {node["id"]: f"n{index}" for index, node in enumerate(data["nodes"])}
    contract_records = {contract["id"]: contract for contract in data["contracts"]}
    contract_aliases = {identifier: f"c{index}" for index, identifier in enumerate(sorted(contract_records))}
    boundary_nodes = set(data["boundary_node_ids"])
    boundary_directions = {edge["id"]: edge["direction"] for edge in data["boundary_edges"]}
    def label(value: Any) -> str:
        return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ").replace("\r", " ")
    lines = ["flowchart LR"]
    def node_line(node: dict, indent: str = "  ") -> str:
        details = f"{node['id']} [{node.get('kind')}] {node.get('label', '')}"
        if node.get("kind") == "DATA":
            contract_ref = contract_aliases.get(node.get("contract_id"), node.get("contract_id")) if view == "data_contracts" else node.get("contract_id")
            details += f" | {node.get('availability')} | {contract_ref}"
        if node["id"] in boundary_nodes:
            details += f" | EXTERNAL PORT: {node.get('subsystem_id')}"
        return f'{indent}{aliases[node["id"]]}["{label(details)}"]'
    grouped = set()
    if view == "implementation":
        for index, stage in enumerate(node for node in data["nodes"] if node.get("kind") == "S"):
            lines.append(f'  subgraph stage{index}["{label(stage["id"])} {label(stage.get("label", ""))}"]')
            lines.append(node_line(stage, "    "))
            grouped.add(stage["id"])
            for node in data["nodes"]:
                if node.get("stage_id") == stage["id"]:
                    lines.append(node_line(node, "    "))
                    grouped.add(node["id"])
            lines.append("  end")
    for node in data["nodes"]:
        if node["id"] not in grouped:
            lines.append(node_line(node))
    if view == "data_contracts" and contract_records:
        lines.append('  subgraph contract_catalog["Contract references; full cards in derived contracts"]')
        for identifier in sorted(contract_records):
            contract = contract_records[identifier]
            alias = contract_aliases[identifier]
            lines.append(f'    {alias}["{alias}: {label(identifier)} v{label(contract["version"])}"]')
        lines.append("  end")
    for edge in data["edges"]:
        arrow = "-.->" if edge.get("kind") in {"RESEARCH_FEEDBACK", "EVIDENCE_SUPPORT", "VERIFIES", "VIEW_FLOW"} or edge.get("id") in boundary_directions else "-->"
        details = f"{edge['id']} {edge['kind']}"
        if edge.get("kind") == "CONSUMES":
            details += f" | {contract_aliases.get(edge.get('contract_id'), edge.get('contract_id')) if view == 'data_contracts' else edge.get('contract_id')}"
        if edge.get("id") in boundary_directions:
            details += f" | {boundary_directions[edge['id']]}"
        lines.append(f'  {aliases[edge["from_id"]]} {arrow}|"{label(details)}"| {aliases[edge["to_id"]]}')
    if boundary_nodes:
        lines.append("  classDef external_port fill:#f3f3f3,stroke:#777,stroke-dasharray:5 5")
        lines.append("  class " + ",".join(aliases[key] for key in sorted(boundary_nodes)) + " external_port")
    for index, alternative in enumerate(data.get("alternatives", [])):
        lines.append(f'  subgraph alt{index}["ALTERNATIVE {label(alternative["mechanism_id"])}"]')
        alt_aliases = {node["id"]: f"a{index}n{offset}" for offset, node in enumerate(alternative["nodes"])}
        for node in alternative["nodes"]:
            lines.append(f'    {alt_aliases[node["id"]]}["{label(node["id"])} [{label(node.get("kind"))}] {label(node.get("label", ""))}"]')
        for edge in alternative["edges"]:
            lines.append(f'    {alt_aliases[edge["from_id"]]} -.->|"{label(edge.get("kind"))}"| {alt_aliases[edge["to_id"]]}')
        lines.append("  end")
    return "\n".join(lines) + "\n"


def architecture_impact(graph: dict[str, Any], changed_node_ids: Iterable[str]) -> dict[str, Any]:
    _, nodes, edges = _selection(graph)
    changed = set(changed_node_ids)
    unknown = changed - set(nodes)
    if unknown:
        raise ValueError("unknown selected architecture node(s): " + ",".join(sorted(unknown)))
    pairs = [(edge["from_id"], edge["to_id"]) for edge in edges if edge.get("kind") in EXECUTION_KINDS]
    # Ownership and verification produce recheck obligations, not execution edges.
    pairs += [(edge["to_id"], edge["from_id"]) for edge in edges if edge.get("kind") in {"IMPLEMENTS", "VERIFIES"}]
    pairs += [(node["stage_id"], key) for key, node in nodes.items() if isinstance(node.get("stage_id"), str) and node["stage_id"] in nodes and node.get("kind") in {"T", "C"}]
    affected: set[str] = set()
    for identifier in sorted(changed):
        affected.update(_projection_reachable(pairs, identifier))
    preserved = set(nodes) - affected
    return {"changed_node_ids": sorted(changed), "affected_node_ids": sorted(affected), "affected_task_ids": sorted(key for key in affected if nodes[key].get("kind") == "T"), "affected_check_ids": sorted(key for key in affected if nodes[key].get("kind") == "C"), "preserved_node_ids": sorted(preserved), "preserved_task_ids": sorted(key for key in preserved if nodes[key].get("kind") == "T"), "preserved_check_ids": sorted(key for key in preserved if nodes[key].get("kind") == "C"), "limits": ["Selected execution dependencies plus owning tasks and verifying checks; research feedback does not invalidate unrelated results."]}
