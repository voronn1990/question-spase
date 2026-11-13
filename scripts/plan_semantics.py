"""Shared read-only normalization of canonical plan order and input references.

These helpers do not create dependencies, acceptance evidence or runtime state.
Callers validate record shapes and ownership; both plan and architecture use the
same resolution rules for the already supported INPUT ID / OUTPUT ID / path.
"""
from __future__ import annotations

from typing import Any


def _ids(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _edge(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        pair = (value.get("from", value.get("source")), value.get("to", value.get("target")))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        pair = tuple(value)
    else:
        return None
    return pair if all(isinstance(item, str) and item for item in pair) else None


def normalize_plan_order(stages: dict[str, Any], tasks: dict[str, Any] | None = None) -> dict[str, list[tuple[str, str]]]:
    """Derive the declared task edges (local edges plus dependencies) once.

    Logical stage keys are authoritative; accepted immutable stage IDs may be
    used by their predecessor/next references and are resolved to those keys.
    Entry/exit markers do not become tasks. The separately returned
    stage_task_edges and stage_check_edges express the already declared
    predecessor-stage completion/acceptance barrier, without changing the
    existing local/dependency task_edges API.
    """
    stages = stages if isinstance(stages, dict) else {}
    canonical_tasks = dict(tasks) if isinstance(tasks, dict) else {}
    aliases = {record.get("id"): key for key, record in stages.items()
               if isinstance(record, dict) and isinstance(record.get("id"), str)}
    stage_edges: set[tuple[str, str]] = set()
    local_edges: set[tuple[str, str]] = set()
    stage_tasks: dict[str, set[str]] = {}
    for stage_id, stage in stages.items():
        if not isinstance(stage, dict):
            continue
        rows = stage.get("tasks")
        local_ids: set[str] = set()
        for task in rows if isinstance(rows, list) else []:
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                local_ids.add(task["id"])
                canonical_tasks.setdefault(task["id"], task)
        stage_tasks[stage_id] = local_ids
        graph = stage.get("local_execution_graph")
        if not isinstance(graph, dict):
            continue
        for predecessor in _ids(graph.get("predecessors")):
            stage_edges.add((aliases.get(predecessor, predecessor), stage_id))
        for successor in _ids(graph.get("next_stages")):
            stage_edges.add((stage_id, aliases.get(successor, successor)))
        rows = graph.get("edges")
        for row in rows if isinstance(rows, list) else []:
            pair = _edge(row)
            if pair and pair[0] in local_ids and pair[1] in local_ids:
                local_edges.add(pair)
    for task_id, task in canonical_tasks.items():
        if isinstance(task, dict):
            local_edges.update((dependency, task_id) for dependency in _ids(task.get("dependencies")) if dependency in canonical_tasks)
    # A successor stage may start only after completion of its predecessor.
    # Retained NOT_APPLICABLE tasks do not become execution prerequisites of an
    # active branch; explicit local/task dependencies remain visible above.
    stage_task_edges = {
        (source, target)
        for predecessor, successor in stage_edges
        for source in stage_tasks.get(predecessor, set())
        for target in stage_tasks.get(successor, set())
        if canonical_tasks.get(source, {}).get("execution_status") != "NOT_APPLICABLE"
        and canonical_tasks.get(target, {}).get("execution_status") != "NOT_APPLICABLE"
    }
    stage_check_edges = {
        (check["id"], target)
        for predecessor, successor in stage_edges
        for check in (stages.get(predecessor, {}).get("acceptance_exit", [])
                      if isinstance(stages.get(predecessor, {}).get("acceptance_exit"), list) else [])
        if isinstance(check, dict) and isinstance(check.get("id"), str)
        and canonical_tasks.get(check.get("task_id"), {}).get("execution_status") != "NOT_APPLICABLE"
        for target in stage_tasks.get(successor, set())
        if canonical_tasks.get(target, {}).get("execution_status") != "NOT_APPLICABLE"
    }
    return {"task_edges": sorted(local_edges), "stage_edges": sorted(stage_edges),
            "stage_task_edges": sorted(stage_task_edges),
            "stage_check_edges": sorted(stage_check_edges)}


def resolve_plan_input(reference: Any, input_contracts: dict[str, Any], output_contracts: dict[str, Any], output_paths: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Resolve a supported reference without inventing an INPUT wrapper.

    Ambiguous aliases fail closed. The original contract mapping is returned
    unchanged so ownership, type, version and availability cannot be guessed.
    """
    if not isinstance(reference, str) or not reference:
        return None
    inputs = input_contracts if isinstance(input_contracts, dict) else {}
    outputs = output_contracts if isinstance(output_contracts, dict) else {}
    matches: set[str] = set()
    if reference in outputs:
        matches.add(reference)
    for identifier, contract in outputs.items():
        if isinstance(contract, dict) and contract.get("path") == reference:
            matches.add(identifier)
    if isinstance(output_paths, dict) and reference in output_paths:
        matches.add(output_paths[reference])
    direct_input = inputs.get(reference)
    if isinstance(direct_input, dict):
        if matches:
            return None
        return {"reference_kind": "INPUT", "reference_id": reference,
                "contract_id": reference, "contract": direct_input,
                "input_kind": direct_input.get("input_kind"),
                "output_id": direct_input.get("output_id")}
    if len(matches) != 1:
        return None
    identifier = next(iter(matches))
    contract = outputs.get(identifier)
    if not isinstance(contract, dict):
        return None
    return {"reference_kind": "OUTPUT", "reference_id": reference,
            "contract_id": identifier, "contract": contract,
            "input_kind": "PLANNED", "output_id": identifier}
