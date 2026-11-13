#!/usr/bin/env python3
"""Pure structural validation for the Scope 05 preparation/admission bridge.

The caller supplies parsed mappings and exact hashes.  This module performs no
I/O, parsing, clock access, dispatch, or persistence.  It verifies addressable
structure and binding only; Root remains the authority for semantic findings,
owner identity, perspective necessity, and research sufficiency.
"""
from __future__ import annotations

import base64
import hashlib
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Iterable

import yaml


PERSPECTIVE_ROLES = tuple(f"P-{number:02d}" for number in range(1, 11))
PERSPECTIVE_PROFILE_FIELDS = (
    "role_id", "agent_id", "title", "competence", "analysis_scope",
    "unique_contribution", "boundary", "plausible_blindspots",
)
OPERATOR_ROLES = (
    "OP-VECTOR",
    "OP-PROJECTION",
    "OP-DIVERGENCE",
    "OP-REVERSE",
    "OP-INDULGENCE",
    "OP-SYNTHESIS",
)
ALL_ROLES = frozenset((*PERSPECTIVE_ROLES, *OPERATOR_ROLES, "PLAN-AUTHOR", "FINAL-AUDITOR"))
PRESERVED_COUNTS = {
    "roles": 18,
    "perspectives": 10,
    "operators": 6,
    "planners": 1,
    "final_auditors": 1,
    "methods": 14,
    "core_phases": 6,
    "audit_areas": 8,
    "root_stage": "S-000",
}
SOURCE_CHECK_IDS = (
    "VECTOR_NO_DRIFT",
    "PERSPECTIVES_DISTINCT_NECESSARY",
    "EACH_R",
    "EACH_MATERIAL_NOT_G",
    "ALTERNATIVES",
    "COMMON_UNPROVED_ASSUMPTIONS",
    "INTERACTIONS",
    "RESEARCHABILITY",
    "CHANGEABLE_DECISIONS",
    "CENTRAL_MECHANISM_SUFFICIENCY",
)
CHECK_OUTCOMES = frozenset({"PASS", "NONCRITICAL_GAP_WITH_RATIONALE", "CRITICAL_GAP"})
LINEAGE_OPERATIONS = frozenset(
    {
        "INITIAL_KEPT",
        "SEMANTIC_MERGE",
        "HIDDEN_ASSUMPTION",
        "DERIVED_UNKNOWN",
        "CROSS_DISCIPLINE_CONTRADICTION",
        "NOT_G_PROJECTION",
        "ALTERNATIVE_FRAMING",
        "FUNCTIONAL_GAP",
        "ROLE_BLINDSPOT",
    }
)
ADMISSION_STATES = (
    "LEGACY_CORE",
    "PREP_OPEN",
    "ADMISSION_REPLAYABLE",
    "CORE_ADMITTED",
    "INVALID",
)
ADMISSION_ACTIONS = frozenset(
    {"NONE", "VERIFY_EXISTING", "REPLAY_ADMISSION", "REPLAY_CURRENT_TRANSITIONS", "FAIL"}
)
CURRENT_MUTATIONS = (
    "APPROVED_MAP_SET",
    "APPROVED_MAP_REVISE",
    "PLAN_PROJECTION_SELECT",
    "APPLICABILITY_FRONTIER_ADVANCE",
    "STALE_SET_EXTEND",
    "SOURCE_OUTCOMES_SELECT",
    "FINAL_AUDIT_SELECT",
    "RELEASE_SEAL",
)
CURRENT_STABLE_FIELDS = (
    "schema_version",
    "run_id",
    "input_snapshot",
    "input_mode",
    "admission_transaction_id",
    "bank_sha256",
    "prep_admission_sha256",
)
CURRENT_MUTABLE_FIELDS = frozenset(
    {
        "revision",
        "status",
        "approved_map_sha256",
        "approved_map_lineage",
        "current_plan_projection",
        "current_plan_projection_sha256",
        "applicability_frontier",
        "stale_result_ids",
        "current_source_outcomes",
        "current_final_audit",
        "release_id",
        "release_sha256",
        "verdict",
    }
)
CURRENT_ALLOWED_DIFFS = {
    "APPROVED_MAP_SET": frozenset(
        {"revision", "approved_map_sha256", "current_plan_projection", "current_plan_projection_sha256"}
    ),
    "APPROVED_MAP_REVISE": frozenset(
        {
            "revision",
            "approved_map_sha256",
            "approved_map_lineage",
            "current_plan_projection",
            "current_plan_projection_sha256",
        }
    ),
    "PLAN_PROJECTION_SELECT": frozenset(
        {"revision", "current_plan_projection", "current_plan_projection_sha256"}
    ),
    "APPLICABILITY_FRONTIER_ADVANCE": frozenset({"revision", "applicability_frontier"}),
    "STALE_SET_EXTEND": frozenset({"revision", "status", "stale_result_ids"}),
    "SOURCE_OUTCOMES_SELECT": frozenset({"revision", "current_source_outcomes", "stale_result_ids"}),
    "FINAL_AUDIT_SELECT": frozenset({"revision", "current_final_audit", "stale_result_ids"}),
    "RELEASE_SEAL": frozenset({"revision", "status", "release_id", "release_sha256", "verdict"}),
}

_HASH = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
_MEANINGLESS = frozenset(
    {"", "pass", "passed", "ok", "accepted", "complete", "completed", "done", "none", "n/a", "na", "not applicable"}
)


class _CanonDumper(yaml.SafeDumper):
    """Exact controlled equivalent of qs.canon_yaml; dumping only, no loading."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _quoted(dumper: yaml.Dumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


_CanonDumper.add_representer(str, _quoted)


def canonical_yaml_bytes(value: Any) -> bytes:
    """Serialize exactly like the controller's independently gated canon_yaml."""
    return yaml.dump(
        value,
        Dumper=_CanonDumper,
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
    ).encode("utf-8")


def _mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return value


def _list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() not in _MEANINGLESS


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_HASH.fullmatch(value))


def _strings(value: Any, *, nonempty: bool = False) -> list[str] | None:
    if not isinstance(value, list) or (nonempty and not value):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return list(value)


def _safe_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _canonical_hash(value: Any, *, omit: Iterable[str] = ()) -> str:
    if isinstance(value, dict) and omit:
        value = {key: item for key, item in value.items() if key not in set(omit)}
    return hashlib.sha256(canonical_yaml_bytes(value)).hexdigest()


def perspective_profile_hash(profile: Any) -> str | None:
    """Hash exactly the eight normative fields of a PREP-2 profile."""
    if not isinstance(profile, dict) or set(profile) != set(PERSPECTIVE_PROFILE_FIELDS):
        return None
    if any(not isinstance(value, str) or not value for value in profile.values()):
        return None
    return _canonical_hash(profile)


def _required(value: Any, fields: Iterable[str], prefix: str, errors: list[str]) -> bool:
    mapping = _mapping(value)
    if mapping is None:
        errors.append(f"{prefix}:not_mapping")
        return False
    clean = True
    for field in fields:
        if field not in mapping:
            errors.append(f"{prefix}:{field}:missing")
            clean = False
    return clean


def _registries(registry: Any, docs: Any, errors: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_registry = _mapping(registry)
    raw_docs = _mapping(docs)
    if raw_registry is None:
        errors.append("prep:registry:not_mapping")
        raw_registry = {}
    if raw_docs is None:
        errors.append("prep:docs:not_mapping")
        raw_docs = {}
    if isinstance(raw_registry.get("results"), dict):
        raw_registry = raw_registry["results"]
    clean_registry: dict[str, Any] = {}
    for result_id in sorted(raw_registry):
        entry = raw_registry[result_id]
        if not isinstance(entry, dict):
            errors.append(f"prep:registry:{result_id}:not_mapping")
            continue
        if entry.get("status") not in {None, "ACCEPTED"}:
            continue
        clean_registry[result_id] = entry
    clean_docs = {key: value for key, value in raw_docs.items() if isinstance(value, dict)}
    return clean_registry, clean_docs


def _input_records(exact_inputs: Any, errors: list[str]) -> dict[str, dict[str, Any]]:
    supplied = _mapping(exact_inputs)
    if supplied is None:
        errors.append("prep:exact_inputs:not_mapping")
        return {}
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(supplied):
        raw = supplied[path]
        if not _safe_path(path):
            errors.append(f"prep:input:{path}:unsafe_path")
            continue
        if isinstance(raw, str):
            record = {"sha256": raw}
        elif isinstance(raw, dict):
            record = dict(raw)
            if "sha256" not in record and _hash(record.get("content_hash")):
                record["sha256"] = record["content_hash"]
        else:
            errors.append(f"prep:input:{path}:invalid_record")
            continue
        if not _hash(record.get("sha256")):
            errors.append(f"prep:input:{path}:invalid_sha256")
            continue
        encoded = record.get("bytes_b64")
        if encoded is not None:
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                errors.append(f"prep:input:{path}:invalid_bytes_b64")
            else:
                if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                    errors.append(f"prep:input:{path}:bytes_hash_mismatch")
        records[path] = record
    return records


def _input_hash(records: dict[str, dict[str, Any]], path: Any) -> str | None:
    return records.get(path, {}).get("sha256") if isinstance(path, str) else None


def _input_doc(records: dict[str, dict[str, Any]], path: Any) -> dict[str, Any] | None:
    if not isinstance(path, str):
        return None
    record = records.get(path, {})
    for key in ("document", "parsed", "value"):
        if isinstance(record.get(key), dict):
            return record[key]
    return None


def _object_hash(value: Any, inputs: dict[str, dict[str, Any]], default_path: str | None = None) -> str | None:
    """Return caller-rehashed exact bytes without hashing a parsed document."""
    if not isinstance(value, dict):
        return None
    path = value.get("path", default_path)
    exact = _input_hash(inputs, path)
    if exact is not None:
        return exact
    for field in ("_sha256", "sha256", "content_hash"):
        if _hash(value.get(field)):
            return value[field]
    return None


def _entry_hash(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    for field in ("content_hash", "sha256"):
        if _hash(entry.get(field)):
            return entry[field]
    return None


def _accepted_result(
    result_id: Any,
    registry: dict[str, Any],
    docs: dict[str, Any],
    prefix: str,
    errors: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(result_id, str) or not result_id:
        errors.append(f"{prefix}:result_id:missing")
        return None, None
    entry = registry.get(result_id)
    doc = docs.get(result_id)
    if entry is None:
        errors.append(f"{prefix}:{result_id}:not_accepted")
    if doc is None:
        errors.append(f"{prefix}:{result_id}:document_missing")
    if entry is not None and doc is not None:
        if doc.get("id") not in {None, result_id}:
            errors.append(f"{prefix}:{result_id}:document_id_mismatch")
        entry_type = entry.get("artifact_type")
        if entry_type is not None and doc.get("artifact_type") != entry_type:
            errors.append(f"{prefix}:{result_id}:artifact_type_mismatch")
    return entry, doc


def _snapshot_hash(snapshot: dict[str, Any], inputs: dict[str, dict[str, Any]]) -> str | None:
    for field in ("input_snapshot", "sha256"):
        if _hash(snapshot.get(field)):
            return snapshot[field]
    path = snapshot.get("path", "input/snapshot.yaml")
    return _input_hash(inputs, path)


def _validate_snapshot(snapshot: Any, inputs: dict[str, dict[str, Any]], errors: list[str]) -> tuple[dict[str, Any], str | None]:
    required = (
        "schema_version",
        "id",
        "run_id",
        "input_mode",
        "goal_path",
        "goal_sha256",
        "materials",
        "bank_binding",
    )
    if not _required(snapshot, required, "prep:snapshot", errors):
        return snapshot if isinstance(snapshot, dict) else {}, None
    value = snapshot
    mode = value.get("input_mode")
    if mode not in {"GOAL_TO_PLAN", "BANK_TO_PLAN"}:
        errors.append(f"prep:snapshot:input_mode:{mode}:invalid")
    features=value.get("methodology_features")
    if features is not None and features not in (["DOUBLE_ICEBERG_V1"], ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"]):
        errors.append("prep:snapshot:methodology_features:invalid")
    goal_path = value.get("goal_path")
    if not _safe_path(goal_path):
        errors.append("prep:snapshot:goal_path:unsafe")
    elif _input_hash(inputs, goal_path) != value.get("goal_sha256"):
        errors.append("prep:snapshot:goal_sha256:mismatch")
    if not _hash(value.get("goal_sha256")):
        errors.append("prep:snapshot:goal_sha256:invalid")
    materials = _list(value.get("materials"))
    if materials is None:
        errors.append("prep:snapshot:materials:not_list")
    else:
        seen: set[str] = set()
        for index, item in enumerate(materials):
            prefix = f"prep:snapshot:material:{index}"
            if not isinstance(item, dict):
                errors.append(f"{prefix}:not_mapping")
                continue
            path, expected = item.get("path"), item.get("sha256")
            if not _safe_path(path) or path in seen:
                errors.append(f"{prefix}:path:invalid_or_duplicate")
            else:
                seen.add(path)
                if _input_hash(inputs, path) != expected:
                    errors.append(f"{prefix}:sha256:mismatch")
    snapshot_hash = _snapshot_hash(value, inputs)
    if snapshot_hash is None:
        errors.append("prep:snapshot:exact_hash:unavailable")
    return value, snapshot_hash


def _validate_vector(vector: Any, prefix: str, errors: list[str]) -> None:
    fields = (
        "G",
        "NOT_G",
        "value_recipient",
        "scope",
        "success_criteria",
        "requirements",
        "hard_constraints",
        "changeable_assumptions",
        "current_state",
        "resources",
        "authority",
        "owner_decisions",
        "open_research_questions",
    )
    if not _required(vector, fields, prefix, errors):
        return
    if vector.get("artifact_type") != "OPERATOR":
        errors.append(f"{prefix}:artifact_type:not_OPERATOR")
    if vector.get("role_id", vector.get("author_role")) != "OP-VECTOR":
        errors.append(f"{prefix}:role:not_OP-VECTOR")
    decisions = _list(vector.get("owner_decisions"))
    if decisions is None:
        errors.append(f"{prefix}:owner_decisions:not_list")
    else:
        for index, decision in enumerate(decisions):
            _required(
                decision,
                ("id", "decision_needed", "why_owner_only", "known_context", "answer_effect"),
                f"{prefix}:owner_decision:{index}",
                errors,
            )


def _validate_authority(approval: dict[str, Any], prefix: str, fixture: bool, errors: list[str]) -> None:
    mode = approval.get("approval_mode")
    if mode not in {"ACTUAL_OWNER", "SIMULATED_TEST"}:
        errors.append(f"{prefix}:approval_mode:invalid")
        return
    if not _text(approval.get("authority_basis")):
        errors.append(f"{prefix}:authority_basis:missing")
    if not _text(approval.get("observation_provenance")):
        errors.append(f"{prefix}:observation_provenance:missing")
    if mode == "ACTUAL_OWNER":
        provenance = approval.get("observation_provenance")
        if not isinstance(provenance, str) or not any(char.isdigit() for char in provenance):
            errors.append(f"{prefix}:observation_provenance:not_addressable")
    if mode == "SIMULATED_TEST":
        if approval.get("approver_id") != "ROOT-AS-SIMULATED_TEST_OWNER":
            errors.append(f"{prefix}:approver_id:not_simulated_owner")
        if approval.get("fixture_only") != "YES" or not fixture:
            errors.append(f"{prefix}:simulated_test:not_fixture_only")


def _validate_current_approval(
    approval: Any,
    snapshot: dict[str, Any],
    snapshot_hash: str | None,
    registry: dict[str, Any],
    docs: dict[str, Any],
    inputs: dict[str, dict[str, Any]],
    fixture: bool,
    errors: list[str],
) -> tuple[str | None, str | None]:
    required = (
        "schema_version",
        "approval_id",
        "run_id",
        "input_snapshot",
        "vector_result_id",
        "vector_sha256",
        "vector_revision",
        "decision",
        "approval_mode",
        "approver_id",
        "authority_basis",
        "observation_provenance",
        "statement_path",
        "statement_sha256",
        "created_at",
    )
    prefix = f"prep:approval:{approval.get('approval_id', 'UNKNOWN')}" if isinstance(approval, dict) else "prep:approval:UNKNOWN"
    if not _required(approval, required, prefix, errors):
        return None, None
    if approval.get("decision") != "APPROVE":
        errors.append(f"{prefix}:decision:not_APPROVE")
    if approval.get("run_id") != snapshot.get("run_id"):
        errors.append(f"{prefix}:run_id:mismatch")
    if approval.get("input_snapshot") != snapshot_hash:
        errors.append(f"{prefix}:input_snapshot:mismatch")
    if not _safe_path(approval.get("statement_path")) or _input_hash(inputs, approval.get("statement_path")) != approval.get("statement_sha256"):
        errors.append(f"{prefix}:statement_sha256:exact_input_mismatch")
    entry, vector = _accepted_result(approval.get("vector_result_id"), registry, docs, "prep:vector", errors)
    if vector is not None:
        _validate_vector(vector, f"prep:vector:{approval.get('vector_result_id')}", errors)
        if vector.get("revision") != approval.get("vector_revision"):
            errors.append(f"{prefix}:vector_revision:mismatch")
        if vector.get("run_id") not in {None, snapshot.get("run_id")}:
            errors.append(f"{prefix}:vector_run_id:mismatch")
        if vector.get("input_snapshot") not in {None, snapshot_hash}:
            errors.append(f"{prefix}:vector_input_snapshot:mismatch")
    if _entry_hash(entry) != approval.get("vector_sha256"):
        errors.append(f"{prefix}:vector_sha256:mismatch")
    _validate_authority(approval, prefix, fixture, errors)
    return approval.get("vector_result_id"), approval.get("vector_sha256")


def _validate_foreign_import(
    snapshot: dict[str, Any],
    supplied_approval: Any,
    inputs: dict[str, dict[str, Any]],
    fixture: bool,
    errors: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    binding = snapshot.get("import_binding")
    required = (
        "schema_version",
        "import_id",
        "current_run_id",
        "source_run_id",
        "source_input_snapshot",
        "source_vector_result_id",
        "source_vector_sha256",
        "source_vector_revision",
        "source_vector_author_role",
        "source_vector_agent_id",
        "vector_path",
        "vector_sha256",
        "source_approval_id",
        "source_approval_decision",
        "source_approval_mode",
        "source_approver_id",
        "source_authority_basis",
        "source_observation_provenance",
        "approval_path",
        "approval_sha256",
        "statement_path",
        "statement_sha256",
        "import_payload_sha256",
    )
    import_id = binding.get("import_id", "UNKNOWN") if isinstance(binding, dict) else "UNKNOWN"
    prefix = f"prep:import:{import_id}"
    if not _required(binding, required, prefix, errors):
        return None, None, binding if isinstance(binding, dict) else None
    if "current_input_snapshot" in binding:
        errors.append(f"{prefix}:current_input_snapshot:prohibited")
    if binding.get("current_run_id") != snapshot.get("run_id"):
        errors.append(f"{prefix}:current_run_id:mismatch")
    root = f"input/imports/{import_id}/"
    for field in ("vector_path", "approval_path", "statement_path"):
        path = binding.get(field)
        if not _safe_path(path) or not path.startswith(root):
            errors.append(f"{prefix}:{field}:outside_import_root")
    for path_field, hash_field in (
        ("vector_path", "vector_sha256"),
        ("approval_path", "approval_sha256"),
        ("statement_path", "statement_sha256"),
    ):
        if _input_hash(inputs, binding.get(path_field)) != binding.get(hash_field):
            errors.append(f"{prefix}:{hash_field}:exact_input_mismatch")
    if binding.get("source_vector_sha256") != binding.get("vector_sha256"):
        errors.append(f"{prefix}:source_vector_sha256:mismatch")
    payload_hash = _canonical_hash(binding, omit=("import_payload_sha256",))
    if binding.get("import_payload_sha256") != payload_hash:
        errors.append(f"{prefix}:import_payload_sha256:mismatch")

    vector = _input_doc(inputs, binding.get("vector_path"))
    foreign_approval = _input_doc(inputs, binding.get("approval_path"))
    if vector is None:
        errors.append(f"{prefix}:vector:parsed_document_missing")
    else:
        _validate_vector(vector, f"{prefix}:vector", errors)
    if foreign_approval is None:
        errors.append(f"{prefix}:approval:parsed_document_missing")
        return vector, None, binding
    if isinstance(supplied_approval, dict) and supplied_approval != foreign_approval:
        errors.append(f"prep:foreign_approval:{binding.get('source_approval_id')}:supplied_copy_mismatch")
    approval_id = foreign_approval.get("approval_id", "UNKNOWN")
    approval_prefix = f"prep:foreign_approval:{approval_id}"
    crosslinks = (
        (vector is not None and vector.get("artifact_type") == "OPERATOR", "vector_artifact_type"),
        (vector is not None and vector.get("role_id", vector.get("author_role")) == "OP-VECTOR", "vector_author_role"),
        (vector is not None and vector.get("run_id") == foreign_approval.get("run_id") == binding.get("source_run_id"), "run_id"),
        (
            vector is not None
            and vector.get("input_snapshot") == foreign_approval.get("input_snapshot") == binding.get("source_input_snapshot"),
            "input_snapshot",
        ),
        (
            vector is not None
            and foreign_approval.get("vector_result_id") == vector.get("id") == binding.get("source_vector_result_id"),
            "vector_result_id",
        ),
        (
            foreign_approval.get("vector_sha256")
            == binding.get("source_vector_sha256")
            == binding.get("vector_sha256"),
            "vector_sha256",
        ),
        (
            vector is not None
            and foreign_approval.get("vector_revision") == vector.get("revision") == binding.get("source_vector_revision"),
            "vector_revision",
        ),
        (
            foreign_approval.get("statement_sha256")
            == binding.get("statement_sha256")
            == _input_hash(inputs, binding.get("statement_path")),
            "statement_sha256",
        ),
        (foreign_approval.get("approval_id") == binding.get("source_approval_id"), "approval_id"),
        (foreign_approval.get("approval_mode") == binding.get("source_approval_mode"), "approval_mode"),
        (foreign_approval.get("approver_id") == binding.get("source_approver_id"), "approver_id"),
        (foreign_approval.get("authority_basis") == binding.get("source_authority_basis"), "authority_basis"),
        (
            foreign_approval.get("observation_provenance") == binding.get("source_observation_provenance"),
            "observation_provenance",
        ),
    )
    for valid, name in crosslinks:
        if not valid:
            errors.append(f"{approval_prefix}:{name}:crosslink_mismatch")
    if foreign_approval.get("decision") != "APPROVE" or binding.get("source_approval_decision") != "APPROVE":
        errors.append(f"{approval_prefix}:decision:not_APPROVE")
    _validate_authority(foreign_approval, approval_prefix, fixture, errors)
    return vector, foreign_approval, binding


def approved_import_for_framing(
    snapshot: dict[str, Any], inputs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """Resolve the approved source for a blind pre-admission BANK review."""
    if snapshot.get("input_mode") != "BANK_TO_PLAN" or snapshot.get("bank_entry_kind") != "APPROVED_RUN_IMPORT":
        return None, None, ["prep:import:framing_requires_approved_bank_import"]
    errors: list[str] = []
    vector, _, binding = _validate_foreign_import(
        snapshot, None, inputs, snapshot.get("fixture_only") == "YES", errors,
    )
    return vector, binding, errors


def _validate_external_bank(snapshot: dict[str, Any], inputs: dict[str, dict[str, Any]], errors: list[str]) -> dict[str, Any] | None:
    binding=snapshot.get("external_binding")
    required=("schema_version","source_kind","current_run_id","original_path","original_sha256","normalized_path","normalized_sha256","external_payload_sha256")
    prefix="prep:external_bank"
    if not _required(binding,required,prefix,errors): return binding if isinstance(binding,dict) else None
    if binding.get("source_kind")!="EXTERNAL_READY_BANK" or binding.get("current_run_id")!=snapshot.get("run_id"):
        errors.append(f"{prefix}:identity:mismatch")
    if binding.get("normalized_path")!="input/bank.yaml" or binding.get("normalized_sha256")!=snapshot.get("bank_sha256"):
        errors.append(f"{prefix}:normalized_binding:mismatch")
    for path_field,hash_field in (("original_path","original_sha256"),("normalized_path","normalized_sha256")):
        path=binding.get(path_field)
        if not _safe_path(path) or _input_hash(inputs,path)!=binding.get(hash_field): errors.append(f"{prefix}:{hash_field}:exact_input_mismatch")
    if binding.get("external_payload_sha256")!=_canonical_hash(binding,omit=("external_payload_sha256",)):
        errors.append(f"{prefix}:external_payload_sha256:mismatch")
    return binding


def _validate_perspective_map(
    perspective_map: Any,
    snapshot: dict[str, Any],
    snapshot_hash: str | None,
    vector_result_id: str | None,
    vector_sha256: str | None,
    approval_sha256: str | None,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    required = (
        "schema_version",
        "id",
        "run_id",
        "input_snapshot",
        "vector_result_id",
        "vector_sha256",
        "approval_sha256",
        "selection_authority",
        "perspectives",
    )
    if not _required(perspective_map, required, "prep:perspective_map", errors):
        return {}
    if perspective_map.get("run_id") != snapshot.get("run_id"):
        errors.append("prep:perspective_map:run_id:mismatch")
    if perspective_map.get("input_snapshot") != snapshot_hash:
        errors.append("prep:perspective_map:input_snapshot:mismatch")
    if perspective_map.get("vector_result_id") != vector_result_id:
        errors.append("prep:perspective_map:vector_result_id:mismatch")
    if perspective_map.get("vector_sha256") != vector_sha256:
        errors.append("prep:perspective_map:vector_sha256:mismatch")
    if perspective_map.get("approval_sha256") != approval_sha256:
        errors.append("prep:perspective_map:approval_sha256:mismatch")
    if perspective_map.get("selection_authority") != "ROOT":
        errors.append("prep:perspective_map:selection_authority:not_ROOT")
    rows = _list(perspective_map.get("perspectives"))
    if rows is None:
        errors.append("prep:perspective_map:perspectives:not_list")
        return {}
    by_role: dict[str, dict[str, Any]] = {}
    agent_ids: list[str] = []
    for index, row in enumerate(rows):
        role = row.get("role_id", f"INDEX-{index}") if isinstance(row, dict) else f"INDEX-{index}"
        prefix = f"prep:perspective:{role}"
        if not _required(
            row,
            PERSPECTIVE_PROFILE_FIELDS,
            prefix,
            errors,
        ):
            continue
        if role in by_role:
            errors.append(f"{prefix}:duplicate_role")
        else:
            by_role[role] = row
        if not _text(row.get("agent_id")):
            errors.append(f"{prefix}:agent_id:missing")
        else:
            agent_ids.append(row["agent_id"])
        for field in ("title", "competence", "analysis_scope", "unique_contribution", "boundary", "plausible_blindspots"):
            if not _text(row.get(field)) and _strings(row.get(field), nonempty=True) is None:
                errors.append(f"{prefix}:{field}:empty")
        if "preferred_mechanism" in row:
            errors.append(f"{prefix}:preferred_mechanism:prohibited")
    if set(by_role) != set(PERSPECTIVE_ROLES) or len(rows) != 10:
        errors.append("prep:perspective_map:roles:not_exact_P01_P10")
    if len(agent_ids) != len(set(agent_ids)):
        errors.append("prep:perspective_map:agent_ids:not_distinct")
    return by_role


def _binding_hash(binding: Any) -> str | None:
    if not isinstance(binding, dict):
        return None
    for field in ("sha256", "content_hash"):
        if _hash(binding.get(field)):
            return binding[field]
    return None


def _canonical_p_result_role(
    doc: dict[str, Any],
    entry: dict[str, Any] | None,
    expected_role: Any,
    prefix: str,
    errors: list[str],
) -> str | None:
    """Resolve a P result only from its canonical author_role identity."""
    author_role = doc.get("author_role")
    if not _text(author_role):
        errors.append(f"{prefix}:author_role:missing")
        return None
    if author_role not in PERSPECTIVE_ROLES:
        errors.append(f"{prefix}:author_role:not_admitted_P_role")
    if expected_role is not None and author_role != expected_role:
        errors.append(f"{prefix}:author_role:mismatch")
    for alias in ("role_id", "reviewer_role"):
        if alias in doc and doc.get(alias) != author_role:
            errors.append(f"{prefix}:{alias}:contradicts_author_role")
    if isinstance(entry, dict) and "role_id" in entry and entry.get("role_id") != author_role:
        errors.append(f"{prefix}:registry_role_id:contradicts_author_role")
    return author_role


def _validate_prep_framing_profiles(
    registry: dict[str, Any],
    docs: dict[str, Any],
    perspectives: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for result_id, entry in registry.items():
        doc = docs.get(result_id, {})
        if entry.get("artifact_type", doc.get("artifact_type")) != "FRAMING":
            continue
        if entry.get("lifecycle_scope") != "PREP" and doc.get("prep_stage") != "PREP-2_PERSPECTIVES":
            continue
        prefix = f"prep:result:{result_id}"
        role = entry.get("role_id", doc.get("author_role"))
        row = perspectives.get(role) if isinstance(role, str) else None
        if row is None:
            errors.append(f"{prefix}:perspective_profile:missing_map_role")
            continue
        expected = {field: row[field] for field in PERSPECTIVE_PROFILE_FIELDS}
        if doc.get("perspective_profile") != expected:
            errors.append(f"{prefix}:perspective_profile:mismatch")
        digest = perspective_profile_hash(expected)
        if digest is None or row.get("profile_sha256") != digest or doc.get("perspective_profile_hash") != digest:
            errors.append(f"{prefix}:perspective_profile_hash:mismatch")


def _validate_initial_framings(
    admission: dict[str, Any],
    registry: dict[str, Any],
    docs: dict[str, Any],
    perspectives: dict[str, dict[str, Any]],
    vector_result_id: str | None,
    vector_sha256: str | None,
    approval_sha256: str | None,
    snapshot_hash: str | None,
    errors: list[str],
) -> tuple[set[str], dict[str, str]]:
    bindings = _list(admission.get("initial_set_bindings"))
    if bindings is None:
        errors.append("prep:admission:initial_set_bindings:not_list")
        return set(), {}
    if len(bindings) != 10:
        errors.append("prep:admission:initial_set_bindings:count_not_10")
    all_questions: set[str] = set()
    result_roles: dict[str, str] = {}
    observed_roles: list[str] = []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            errors.append(f"prep:initial_set:{index}:binding_not_mapping")
            continue
        result_id = binding.get("result_id")
        role = binding.get("role_id")
        prefix = f"prep:initial_set:{role or index}"
        entry, doc = _accepted_result(result_id, registry, docs, prefix, errors)
        if doc is None:
            continue
        if _entry_hash(entry) != binding.get("sha256"):
            errors.append(f"{prefix}:sha256:mismatch")
        if doc.get("artifact_type") != "FRAMING":
            errors.append(f"{prefix}:artifact_type:not_FRAMING")
        if role not in perspectives:
            errors.append(f"{prefix}:binding_role_id:not_admitted_P_role")
        canonical_role = _canonical_p_result_role(doc, entry, role, prefix, errors)
        if canonical_role == role and role in perspectives:
            observed_roles.append(role)
            if doc.get("agent_id") != perspectives[role].get("agent_id") or binding.get("agent_id") != perspectives[role].get("agent_id"):
                errors.append(f"{prefix}:agent_id:mismatch")
            expected_profile = perspectives[role].get("profile_sha256", _canonical_hash(perspectives[role]))
            if doc.get("perspective_profile_hash") != expected_profile:
                errors.append(f"{prefix}:perspective_profile_hash:mismatch")
        result_roles[str(result_id)] = str(role)
        for field, expected in (
            ("vector_result_id", vector_result_id),
            ("vector_sha256", vector_sha256),
            ("approval_sha256", approval_sha256),
        ):
            if doc.get(field) != expected:
                errors.append(f"{prefix}:{field}:mismatch")
        if not _hash(doc.get("context_packet_sha256")):
            errors.append(f"{prefix}:context_packet_sha256:invalid")
        if doc.get("input_snapshot") not in {None, snapshot_hash}:
            errors.append(f"{prefix}:input_snapshot:mismatch")
        blind = doc.get("blind_input")
        if not isinstance(blind, dict):
            errors.append(f"{prefix}:blind_input:missing")
        else:
            for field in ("contains_bank", "contains_foreign_p_results", "contains_preferred_solution"):
                if blind.get(field) != "NO":
                    errors.append(f"{prefix}:blind_input:{field}:not_NO")
            allowed = _strings(blind.get("allowed"), nonempty=True)
            if allowed is None:
                errors.append(f"{prefix}:blind_input:allowed:invalid")
        questions = _list(doc.get("initial_questions"))
        if questions is None or len(questions) != 10:
            errors.append(f"{prefix}:initial_questions:count_not_10")
            continue
        for q_index, question in enumerate(questions):
            qid = question.get("id", f"INDEX-{q_index}") if isinstance(question, dict) else f"INDEX-{q_index}"
            q_prefix = f"prep:initial_question:{qid}"
            if not _required(
                question,
                ("id", "unknown", "why_material", "changeable_decision", "applicability", "evidence_needed", "g_refs", "r_refs", "not_g_refs", "origin_role"),
                q_prefix,
                errors,
            ):
                continue
            if qid in all_questions:
                errors.append(f"{q_prefix}:duplicate_id")
            all_questions.add(qid)
            if question.get("origin_role") != role:
                errors.append(f"{q_prefix}:origin_role:mismatch")
            for field in ("unknown", "why_material", "changeable_decision", "applicability", "evidence_needed"):
                if not _text(question.get(field)):
                    errors.append(f"{q_prefix}:{field}:empty")
            if _strings(question.get("g_refs"), nonempty=True) is None:
                errors.append(f"{q_prefix}:g_refs:invalid")
            for field in ("r_refs", "not_g_refs"):
                if _strings(question.get(field)) is None:
                    errors.append(f"{q_prefix}:{field}:invalid")
    if Counter(observed_roles) != Counter(PERSPECTIVE_ROLES):
        errors.append("prep:initial_sets:roles:not_exact_once")
    if len(all_questions) != 100:
        errors.append(f"prep:initial_sets:question_total:{len(all_questions)}:not_100")
    return all_questions, result_roles


def _requirement_ids(bank: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in bank.get("requirements", []) if isinstance(bank.get("requirements"), list) else []:
        if isinstance(item, str) and item:
            result.add(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result.add(item["id"])
    return result


def _validate_bank(
    bank: Any,
    question_count: int | None,
    mode: str,
    known_results: set[str],
    imported_result_id: str | None,
    initial_questions: set[str],
    errors: list[str],
) -> tuple[set[str], set[str]]:
    if not isinstance(bank, dict):
        errors.append("prep:bank:not_mapping")
        return set(), set()
    questions = _list(bank.get("questions"))
    requirements = _requirement_ids(bank)
    if questions is None:
        errors.append("prep:bank:questions:not_list")
        return set(), requirements
    qids: set[str] = set()
    unknown_keys: set[str] = set()
    covered_initial: set[str] = set()
    covered_requirements: set[str] = set()
    allowed_result_ids = set(known_results)
    if imported_result_id:
        allowed_result_ids.add(imported_result_id)
    for index, question in enumerate(questions):
        qid = question.get("id", f"INDEX-{index}") if isinstance(question, dict) else f"INDEX-{index}"
        prefix = f"prep:bank_question:{qid}"
        if not _required(
            question,
            (
                "id",
                "text",
                "source_offset",
                "unknown_key",
                "mechanism",
                "applicability",
                "changeable_decision",
                "evidence_needed",
                "lineage",
                "g_refs",
                "r_refs",
                "not_g_refs",
            ),
            prefix,
            errors,
        ):
            continue
        if qid in qids:
            errors.append(f"{prefix}:duplicate_id")
        qids.add(qid)
        unknown_key = question.get("unknown_key")
        if not _text(unknown_key) or unknown_key in unknown_keys:
            errors.append(f"{prefix}:unknown_key:empty_or_duplicate")
        else:
            unknown_keys.add(unknown_key)
        for field in ("text", "source_offset", "mechanism", "applicability", "changeable_decision", "evidence_needed"):
            if not _text(question.get(field)):
                errors.append(f"{prefix}:{field}:empty")
        if _strings(question.get("g_refs"), nonempty=True) is None:
            errors.append(f"{prefix}:g_refs:invalid")
        r_refs = _strings(question.get("r_refs"))
        if r_refs is None:
            errors.append(f"{prefix}:r_refs:invalid")
            r_refs = []
        unknown_r = sorted(set(r_refs) - requirements)
        if unknown_r:
            errors.append(f"{prefix}:r_refs:unknown:{','.join(unknown_r)}")
        covered_requirements.update(r_refs)
        if _strings(question.get("not_g_refs")) is None:
            errors.append(f"{prefix}:not_g_refs:invalid")
        lineage = question.get("lineage")
        if not _required(lineage, ("operation", "source_question_ids", "source_result_ids", "source_roles", "reason"), f"{prefix}:lineage", errors):
            continue
        if lineage.get("operation") not in LINEAGE_OPERATIONS:
            errors.append(f"{prefix}:lineage:operation:invalid")
        source_q = _strings(lineage.get("source_question_ids"))
        source_results = _strings(lineage.get("source_result_ids"), nonempty=True)
        source_roles = _strings(lineage.get("source_roles"), nonempty=True)
        if source_q is None:
            errors.append(f"{prefix}:lineage:source_question_ids:invalid")
            source_q = []
        if source_results is None:
            errors.append(f"{prefix}:lineage:source_result_ids:invalid")
            source_results = []
        if source_roles is None or any(role not in ALL_ROLES for role in source_roles):
            errors.append(f"{prefix}:lineage:source_roles:invalid")
        if not _text(lineage.get("reason")):
            errors.append(f"{prefix}:lineage:reason:empty")
        unresolved = sorted(set(source_results) - allowed_result_ids)
        if unresolved:
            errors.append(f"{prefix}:lineage:source_result_ids:unresolved:{','.join(unresolved)}")
        if mode == "GOAL_TO_PLAN":
            unknown_sources = sorted(set(source_q) - initial_questions)
            if unknown_sources:
                errors.append(f"{prefix}:lineage:source_question_ids:unknown:{','.join(unknown_sources)}")
            covered_initial.update(source_q)
            if lineage.get("operation") == "INITIAL_KEPT" and not source_q:
                errors.append(f"{prefix}:lineage:initial_kept_without_source")
    if requirements - covered_requirements:
        errors.append(f"prep:bank:requirements:uncovered:{','.join(sorted(requirements - covered_requirements))}")
    if mode == "GOAL_TO_PLAN" and initial_questions - covered_initial:
        errors.append(f"prep:bank:initial_questions:uncovered:{','.join(sorted(initial_questions - covered_initial))}")
    if question_count is not None and question_count != len(questions):
        errors.append("prep:admission:question_count:mismatch")
    return qids, requirements


def _size_kind(admission: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    value = admission.get("size_assessment")
    if isinstance(value, str):
        return value, admission
    if isinstance(value, dict):
        return value.get("classification", value.get("status")), value
    return None, {}


def _question_count(admission: dict[str, Any], errors: list[str]) -> int | None:
    """Parse the canonical serialized count only for count-dependent checks."""
    value = admission.get("question_count")
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        errors.append("prep:admission:question_count:invalid")
        return None
    try:
        return int(value)
    except ValueError:
        errors.append("prep:admission:question_count:invalid")
        return None


def _validate_size(admission: dict[str, Any], count: int | None, errors: list[str]) -> None:
    if count is None:
        return
    kind, details = _size_kind(admission)
    expected = "BELOW_TARGET_WITH_RATIONALE" if count < 200 else "WITHIN_TARGET" if count <= 300 else "ABOVE_TARGET_WITH_RESOURCE_PLAN"
    if kind != expected:
        errors.append(f"prep:admission:size_assessment:{kind}:expected_{expected}")
        return
    if kind == "BELOW_TARGET_WITH_RATIONALE":
        rationale = details.get("rationale", admission.get("size_rationale"))
        if not _text(rationale):
            errors.append("prep:admission:size_assessment:below:rationale_missing")
    if kind == "ABOVE_TARGET_WITH_RESOURCE_PLAN":
        plan = details.get("resource_plan", admission.get("resource_plan"))
        if not _text(plan) and not isinstance(plan, dict):
            errors.append("prep:admission:size_assessment:above:resource_plan_missing")


def _reference_set(registry: dict[str, Any], docs: dict[str, Any], inputs: dict[str, Any]) -> set[str]:
    refs = set(registry) | set(inputs)
    for result_id, doc in docs.items():
        refs.add(result_id)
        if isinstance(doc.get("id"), str):
            refs.add(doc["id"])
    return refs


def _validate_refs(refs: Any, allowed: set[str], prefix: str, errors: list[str], *, nonempty: bool = True) -> list[str]:
    values = _strings(refs, nonempty=nonempty)
    if values is None:
        errors.append(f"{prefix}:invalid")
        return []
    unresolved = sorted(set(values) - allowed)
    if unresolved:
        errors.append(f"{prefix}:unresolved:{','.join(unresolved)}")
    return values


def _validate_perspective_check(row: dict[str, Any], perspectives: dict[str, Any], allowed_refs: set[str], errors: list[str]) -> None:
    prefix = "prep:source_check:PERSPECTIVES_DISTINCT_NECESSARY"
    for field in ("root_semantic_outcome", "perspective_assessments", "material_overlap_assessments"):
        if field not in row:
            errors.append(f"{prefix}:{field}:missing")
    if row.get("root_semantic_outcome") != row.get("outcome"):
        errors.append(f"{prefix}:root_semantic_outcome:incoherent")
    assessments = _list(row.get("perspective_assessments"))
    if assessments is None:
        errors.append(f"{prefix}:perspective_assessments:not_list")
        return
    roles: list[str] = []
    for index, assessment in enumerate(assessments):
        role = assessment.get("role_id", index) if isinstance(assessment, dict) else index
        item_prefix = f"prep:perspective:{role}:assessment"
        if not _required(
            assessment,
            ("role_id", "competence", "analysis_scope", "unique_contribution", "boundary", "necessity", "basis_refs"),
            item_prefix,
            errors,
        ):
            continue
        roles.append(assessment["role_id"])
        source = perspectives.get(assessment["role_id"])
        if source is None:
            errors.append(f"{item_prefix}:unknown_role")
        else:
            for field in ("competence", "analysis_scope", "unique_contribution", "boundary"):
                if assessment.get(field) != source.get(field):
                    errors.append(f"{item_prefix}:{field}:profile_mismatch")
        if not _text(assessment.get("necessity")):
            errors.append(f"{item_prefix}:necessity:empty")
        _validate_refs(assessment.get("basis_refs"), allowed_refs, f"{item_prefix}:basis_refs", errors)
    if Counter(roles) != Counter(PERSPECTIVE_ROLES):
        errors.append(f"{prefix}:perspective_assessments:not_exact_10")
    overlaps = _list(row.get("material_overlap_assessments"))
    if overlaps is None:
        errors.append(f"{prefix}:material_overlap_assessments:not_list")
    else:
        for index, overlap in enumerate(overlaps):
            item_prefix = f"{prefix}:overlap:{index}"
            if not _required(overlap, ("roles", "disposition", "basis_refs"), item_prefix, errors):
                continue
            roles_value = _strings(overlap.get("roles"), nonempty=True)
            if roles_value is None or len(set(roles_value)) < 2 or any(role not in perspectives for role in roles_value):
                errors.append(f"{item_prefix}:roles:invalid")
            if not _text(overlap.get("disposition")):
                errors.append(f"{item_prefix}:disposition:empty")
            _validate_refs(overlap.get("basis_refs"), allowed_refs, f"{item_prefix}:basis_refs", errors)


def _validate_assumptions_check(row: dict[str, Any], allowed_refs: set[str], errors: list[str]) -> None:
    prefix = "prep:source_check:COMMON_UNPROVED_ASSUMPTIONS"
    for field in ("root_semantic_outcome", "detected_assumptions", "none_with_basis"):
        if field not in row:
            errors.append(f"{prefix}:{field}:missing")
    if row.get("root_semantic_outcome") != row.get("outcome"):
        errors.append(f"{prefix}:root_semantic_outcome:incoherent")
    assumptions = _list(row.get("detected_assumptions"))
    if assumptions is None:
        errors.append(f"{prefix}:detected_assumptions:not_list")
        return
    ids: set[str] = set()
    for index, item in enumerate(assumptions):
        assumption_id = item.get("assumption_id", index) if isinstance(item, dict) else index
        item_prefix = f"prep:assumption:{assumption_id}"
        if not _required(item, ("assumption_id", "detected_in_refs", "disposition", "target_refs", "basis"), item_prefix, errors):
            continue
        if assumption_id in ids:
            errors.append(f"{item_prefix}:duplicate")
        ids.add(assumption_id)
        _validate_refs(item.get("detected_in_refs"), allowed_refs, f"{item_prefix}:detected_in_refs", errors)
        if item.get("disposition") not in {"ADMITTED_RESEARCHABLE_Q", "RESOLVED_ACCEPTED_EVIDENCE", "CRITICAL_GAP_REPAIR"}:
            errors.append(f"{item_prefix}:disposition:invalid")
        _validate_refs(item.get("target_refs"), allowed_refs, f"{item_prefix}:target_refs", errors)
        if not _text(item.get("basis")):
            errors.append(f"{item_prefix}:basis:empty")
    none = row.get("none_with_basis")
    if assumptions:
        if none not in {None, ""}:
            errors.append(f"{prefix}:none_with_basis:present_with_detected")
    else:
        if not isinstance(none, dict) or none.get("outcome") != "NONE_WITH_BASIS":
            errors.append(f"{prefix}:none_with_basis:invalid")
        else:
            _validate_refs(none.get("basis_refs"), allowed_refs, f"{prefix}:none_with_basis:basis_refs", errors)


def _validate_source_checks(
    admission: dict[str, Any],
    perspectives: dict[str, Any],
    allowed_refs: set[str],
    errors: list[str],
) -> None:
    rows = _list(admission.get("source_checks"))
    if rows is None:
        errors.append("prep:admission:source_checks:not_list")
        return
    observed: list[str] = []
    for index, row in enumerate(rows):
        check_id = row.get("check_id", f"INDEX-{index}") if isinstance(row, dict) else f"INDEX-{index}"
        prefix = f"prep:source_check:{check_id}"
        if not _required(row, ("check_id", "outcome", "basis", "evidence_refs", "targeted_repair_assignments"), prefix, errors):
            continue
        observed.append(check_id)
        outcome = row.get("outcome")
        if outcome not in CHECK_OUTCOMES:
            errors.append(f"{prefix}:outcome:invalid")
        if not _text(row.get("basis")):
            errors.append(f"{prefix}:basis:empty")
        _validate_refs(row.get("evidence_refs"), allowed_refs, f"{prefix}:evidence_refs", errors)
        repairs = _strings(row.get("targeted_repair_assignments"))
        if repairs is None:
            errors.append(f"{prefix}:targeted_repair_assignments:invalid")
            repairs = []
        if outcome == "CRITICAL_GAP" and not repairs:
            errors.append(f"{prefix}:critical_without_repairs")
        if outcome != "CRITICAL_GAP" and repairs:
            errors.append(f"{prefix}:repairs_without_critical_gap")
        if outcome == "NONCRITICAL_GAP_WITH_RATIONALE" and not _text(row.get("rationale")):
            errors.append(f"{prefix}:rationale:missing")
        if check_id == "PERSPECTIVES_DISTINCT_NECESSARY":
            _validate_perspective_check(row, perspectives, allowed_refs, errors)
        if check_id == "COMMON_UNPROVED_ASSUMPTIONS":
            _validate_assumptions_check(row, allowed_refs, errors)
    if tuple(observed) != SOURCE_CHECK_IDS:
        errors.append("prep:admission:source_checks:not_exact_order")
    if len(observed) != len(set(observed)):
        errors.append("prep:admission:source_checks:duplicate")
    if any(isinstance(row, dict) and row.get("outcome") == "CRITICAL_GAP" for row in rows):
        if admission.get("status") == "ADMITTED":
            errors.append("prep:admission:status:critical_gap_cannot_admit")


def _validate_applicability(
    result_id: Any,
    expected_hash: Any,
    snapshot: dict[str, Any],
    snapshot_hash: str | None,
    binding: dict[str, Any],
    admission: dict[str, Any],
    bank: dict[str, Any],
    registry: dict[str, Any],
    docs: dict[str, Any],
    errors: list[str],
) -> None:
    prefix = f"prep:applicability:{result_id}"
    entry, doc = _accepted_result(result_id, registry, docs, prefix, errors)
    if doc is None:
        return
    payload_field="external_payload_sha256" if "external_payload_sha256" in binding else "import_payload_sha256"
    required = (
        "current_run_id",
        "current_input_snapshot",
        payload_field,
        "current_goal_sha256",
        "current_material_hashes",
        "current_bank_sha256",
        "current_requirements_sha256",
        "g_comparison",
        "requirement_comparisons",
        "authority_comparison",
        "source_check_outcomes",
        "overall",
    )
    _required(doc, required, prefix, errors)
    if _entry_hash(entry) != expected_hash:
        errors.append(f"{prefix}:sha256:mismatch")
    if doc.get("artifact_type") != "OPERATOR" or doc.get("role_id", doc.get("author_role")) != "OP-VECTOR":
        errors.append(f"{prefix}:producer:not_OP-VECTOR")
    if doc.get("operator_contract") != "BANK_APPLICABILITY":
        errors.append(f"{prefix}:operator_contract:mismatch")
    expected = {
        "current_run_id": snapshot.get("run_id"),
        "current_input_snapshot": snapshot_hash,
        payload_field: binding.get(payload_field),
        "current_goal_sha256": snapshot.get("goal_sha256"),
        "current_bank_sha256": admission.get("bank_sha256"),
        "current_requirements_sha256": admission.get("requirements_sha256"),
    }
    for field, value in expected.items():
        if doc.get(field) != value:
            errors.append(f"{prefix}:{field}:mismatch")
    if doc.get("current_material_hashes") != snapshot.get("materials"):
        errors.append(f"{prefix}:current_material_hashes:mismatch")
    if doc.get("overall") != "UNCHANGED":
        errors.append(f"{prefix}:overall:not_UNCHANGED")
    for component in ("g_comparison", "authority_comparison"):
        value = doc.get(component)
        outcome = value.get("outcome") if isinstance(value, dict) else value
        if outcome != "UNCHANGED":
            errors.append(f"{prefix}:{component}:not_UNCHANGED")
    comparisons = _list(doc.get("requirement_comparisons"))
    requirement_ids = _requirement_ids(bank)
    observed: list[str] = []
    if comparisons is None:
        errors.append(f"{prefix}:requirement_comparisons:not_list")
    else:
        for index, comparison in enumerate(comparisons):
            if not isinstance(comparison, dict):
                errors.append(f"{prefix}:requirement_comparison:{index}:not_mapping")
                continue
            source_id = comparison.get("source_requirement_id")
            current_id = comparison.get("current_requirement_id")
            if source_id != current_id or comparison.get("outcome") != "UNCHANGED":
                errors.append(f"{prefix}:requirement_comparison:{index}:not_one_to_one_unchanged")
            if isinstance(current_id, str):
                observed.append(current_id)
        if Counter(observed) != Counter(requirement_ids):
            errors.append(f"{prefix}:requirement_comparisons:not_complete_one_to_one")
    outcomes = doc.get("source_check_outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != set(SOURCE_CHECK_IDS):
        errors.append(f"{prefix}:source_check_outcomes:not_exact_10")
    elif any(value not in {"PASS", "NONCRITICAL_GAP_WITH_RATIONALE"} for value in outcomes.values()):
        errors.append(f"{prefix}:source_check_outcomes:not_eligible")
    else:
        admitted_outcomes = {
            row.get("check_id"): row.get("outcome")
            for row in admission.get("source_checks", [])
            if isinstance(row, dict)
        }
        if outcomes != admitted_outcomes:
            errors.append(f"{prefix}:source_check_outcomes:admission_mismatch")


def _blob(record: Any, prefix: str, errors: list[str]) -> tuple[str | None, dict[str, Any] | None]:
    if not _required(record, ("path", "sha256", "bytes_b64"), prefix, errors):
        return None, None
    if not _safe_path(record.get("path")):
        errors.append(f"{prefix}:path:unsafe")
    if not _hash(record.get("sha256")):
        errors.append(f"{prefix}:sha256:invalid")
    try:
        payload = base64.b64decode(record.get("bytes_b64"), validate=True)
    except (ValueError, TypeError):
        errors.append(f"{prefix}:bytes_b64:invalid")
    else:
        if hashlib.sha256(payload).hexdigest() != record.get("sha256"):
            errors.append(f"{prefix}:bytes_hash_mismatch")
    document = record.get("document") if isinstance(record.get("document"), dict) else None
    return record.get("sha256"), document


def _transaction_collection(transactions: Any, errors: list[str]) -> dict[str, dict[str, Any]]:
    supplied = _mapping(transactions)
    if supplied is None:
        errors.append("prep:transactions:not_mapping")
        return {}
    if isinstance(supplied.get("transactions"), dict):
        supplied = supplied["transactions"]
    clean: dict[str, dict[str, Any]] = {}
    for key in sorted(supplied):
        value = supplied[key]
        if isinstance(value, dict) and isinstance(value.get("record"), dict):
            record = dict(value["record"])
            if isinstance(value.get("before_current"), dict):
                record["__before_current"] = value["before_current"]
            if isinstance(value.get("after_current"), dict):
                record["__after_current"] = value["after_current"]
            clean[key] = record
        elif isinstance(value, dict):
            clean[key] = value
        else:
            errors.append(f"prep:transaction:{key}:not_mapping")
    return clean


def _admission_transactions(transactions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [value for value in transactions.values() if value.get("transaction_type") == "PREP_ADMISSION"]


def _transition_transactions(transactions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [value for value in transactions.values() if value.get("transaction_type") == "CURRENT_TRANSITION"]


def _validate_admission_transaction(
    transaction: dict[str, Any],
    mode: str,
    snapshot: dict[str, Any],
    snapshot_hash: str | None,
    admission: dict[str, Any],
    inputs: dict[str, dict[str, Any]],
    errors: list[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    txid = transaction.get("transaction_id", "UNKNOWN")
    prefix = f"prep:admission_transaction:{txid}"
    required = (
        "schema_version",
        "transaction_type",
        "transaction_id",
        "admission_id",
        "run_id",
        "input_snapshot",
        "input_mode",
        "status",
        "bank",
        "admission",
        "expected_current",
        "admission_next_current",
        "transaction_payload_sha256",
    )
    _required(transaction, required, prefix, errors)
    for field, expected in (
        ("transaction_type", "PREP_ADMISSION"),
        ("status", "COMMITTED"),
        ("run_id", snapshot.get("run_id")),
        ("input_snapshot", snapshot_hash),
        ("input_mode", mode),
        ("admission_id", admission.get("id")),
    ):
        if transaction.get(field) != expected:
            errors.append(f"{prefix}:{field}:mismatch")
    external=mode=="BANK_TO_PLAN" and snapshot.get("bank_entry_kind")=="EXTERNAL_READY_BANK"
    required_mode=(("current_vector_result_id","current_vector_sha256","current_approval_sha256","external_payload_sha256","applicability_result_id","applicability_sha256") if external
                   else ("current_vector_result_id", "current_vector_sha256", "current_approval_sha256") if mode == "GOAL_TO_PLAN"
                   else ("import_payload_sha256", "applicability_result_id", "applicability_sha256"))
    _required(transaction, required_mode, prefix, errors)
    bank_hash, _ = _blob(transaction.get("bank"), f"{prefix}:bank", errors)
    admission_hash, _ = _blob(transaction.get("admission"), f"{prefix}:admission", errors)
    _, expected_doc = _blob(transaction.get("expected_current"), f"{prefix}:expected_current", errors)
    _, next_doc = _blob(transaction.get("admission_next_current"), f"{prefix}:admission_next_current", errors)
    if transaction.get("bank", {}).get("install_policy") != ("CREATE_IF_ABSENT" if mode == "GOAL_TO_PLAN" else "VERIFY_EXISTING"):
        errors.append(f"{prefix}:bank:install_policy:mismatch")
    if bank_hash != admission.get("bank_sha256"):
        errors.append(f"{prefix}:bank:sha256:mismatch")
    installed_admission_hash = _object_hash(admission, inputs, "input/prep-admission.yaml")
    if installed_admission_hash is not None and admission_hash != installed_admission_hash:
        errors.append(f"{prefix}:admission:sha256:mismatch")
    if transaction.get("transaction_payload_sha256") != _canonical_hash(transaction, omit=("transaction_payload_sha256",)):
        errors.append(f"{prefix}:transaction_payload_sha256:mismatch")
    return expected_doc, next_doc


def _revision(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        return int(value)
    return None


def _validate_current_chain(
    current: dict[str, Any],
    admission_tx: dict[str, Any],
    admission: dict[str, Any],
    transitions: list[dict[str, Any]],
    errors: list[str],
) -> bool:
    txid = admission_tx.get("transaction_id")
    expected_stable = {
        "run_id": admission.get("run_id"),
        "input_snapshot": admission.get("input_snapshot"),
        "input_mode": admission.get("input_mode"),
        "admission_transaction_id": txid,
        "bank_sha256": admission.get("bank_sha256"),
        "prep_admission_sha256": admission_tx.get("admission", {}).get("sha256"),
    }
    valid = True
    for field, expected in expected_stable.items():
        if current.get(field) != expected:
            errors.append(f"prep:current_transition:{current.get('revision', 'UNKNOWN')}:{field}:stable_mismatch")
            valid = False
    unknown = sorted(set(current) - set(CURRENT_STABLE_FIELDS) - CURRENT_MUTABLE_FIELDS - {"sha256"})
    if unknown:
        errors.append(f"prep:current_transition:{current.get('revision', 'UNKNOWN')}:unknown_fields:{','.join(unknown)}")
        valid = False
    current_revision = _revision(current.get("revision"))
    if current_revision is None or current_revision < 1:
        errors.append("prep:current_transition:UNKNOWN:revision:invalid")
        return False
    projection = current.get("current_plan_projection")
    projection_hash = current.get("current_plan_projection_sha256")
    if projection is not None or projection_hash is not None:
        if not isinstance(projection, dict) or projection_hash != hashlib.sha256(canonical_yaml_bytes(projection)).hexdigest():
            errors.append(f"prep:current_transition:{current.get('revision', 'UNKNOWN')}:current_plan_projection_sha256:mismatch")
            valid = False
    by_to: dict[int, dict[str, Any]] = {}
    for transition in transitions:
        to_revision = _revision(transition.get("to_revision"))
        from_revision = _revision(transition.get("from_revision"))
        label = transition.get("to_revision", "UNKNOWN")
        prefix = f"prep:current_transition:{label}"
        _required(
            transition,
            (
                "schema_version",
                "transaction_type",
                "transaction_id",
                "current_path",
                "run_id",
                "input_snapshot",
                "admission_transaction_id",
                "prep_admission_sha256",
                "mutation_type",
                "from_revision",
                "to_revision",
                "before_current_sha256",
                "after_current_sha256",
                "after_current_bytes_b64",
                "backing_records",
                "status",
                "transaction_payload_sha256",
            ),
            prefix,
            errors,
        )
        if transition.get("status") != "COMMITTED" or transition.get("current_path") != "state/current.yaml":
            errors.append(f"{prefix}:status_or_path:invalid")
            valid = False
        mutation = transition.get("mutation_type")
        if mutation not in CURRENT_MUTATIONS:
            errors.append(f"{prefix}:mutation_type:invalid")
            valid = False
        if from_revision is None or to_revision is None or to_revision != from_revision + 1:
            errors.append(f"{prefix}:revision:not_contiguous")
            valid = False
        elif to_revision in by_to:
            errors.append(f"{prefix}:duplicate_or_branch")
            valid = False
        else:
            by_to[to_revision] = transition
        for field in ("before_current_sha256", "after_current_sha256"):
            if not _hash(transition.get(field)):
                errors.append(f"{prefix}:{field}:invalid")
        try:
            after_bytes = base64.b64decode(transition.get("after_current_bytes_b64"), validate=True)
        except (ValueError, TypeError):
            errors.append(f"{prefix}:after_current_bytes_b64:invalid")
        else:
            if hashlib.sha256(after_bytes).hexdigest() != transition.get("after_current_sha256"):
                errors.append(f"{prefix}:after_current_sha256:mismatch")
        if transition.get("transaction_payload_sha256") != _canonical_hash(
            transition,
            omit=("transaction_payload_sha256", "__before_current", "__after_current"),
        ):
            errors.append(f"{prefix}:transaction_payload_sha256:mismatch")
        if transition.get("admission_transaction_id") != txid or transition.get("prep_admission_sha256") != admission_tx.get("admission", {}).get("sha256"):
            errors.append(f"{prefix}:admission_binding:mismatch")
        if transition.get("run_id") != admission.get("run_id") or transition.get("input_snapshot") != admission.get("input_snapshot"):
            errors.append(f"{prefix}:run_binding:mismatch")
        backing = _list(transition.get("backing_records"))
        if backing is None or not backing:
            errors.append(f"{prefix}:backing_records:not_list")
        else:
            for index, record in enumerate(backing):
                if not _required(record, ("kind", "path", "sha256"), f"{prefix}:backing:{index}", errors):
                    continue
                if not _safe_path(record.get("path")) or not _hash(record.get("sha256")):
                    errors.append(f"{prefix}:backing:{index}:invalid_path_or_hash")
        before_doc = transition.get("__before_current")
        after_doc = transition.get("__after_current")
        if not isinstance(before_doc, dict) or not isinstance(after_doc, dict):
            errors.append(f"{prefix}:parsed_current_images:missing")
            valid = False
        else:
            before_hash = hashlib.sha256(canonical_yaml_bytes(before_doc)).hexdigest()
            after_hash = hashlib.sha256(canonical_yaml_bytes(after_doc)).hexdigest()
            if before_hash != transition.get("before_current_sha256"):
                errors.append(f"{prefix}:before_current_sha256:parsed_mismatch")
                valid = False
            if after_hash != transition.get("after_current_sha256"):
                errors.append(f"{prefix}:after_current_sha256:parsed_mismatch")
                valid = False
            if _revision(before_doc.get("revision")) != from_revision or _revision(after_doc.get("revision")) != to_revision:
                errors.append(f"{prefix}:parsed_revision:mismatch")
                valid = False
            for field in CURRENT_STABLE_FIELDS:
                if before_doc.get(field) != after_doc.get(field) or after_doc.get(field) != current.get(field):
                    errors.append(f"{prefix}:{field}:parsed_stable_mismatch")
                    valid = False
            changed = {
                field
                for field in set(before_doc) | set(after_doc)
                if before_doc.get(field) != after_doc.get(field)
            }
            allowed = CURRENT_ALLOWED_DIFFS.get(mutation, frozenset())
            if "revision" not in changed or changed - allowed:
                errors.append(f"{prefix}:changed_fields:invalid:{','.join(sorted(changed))}")
                valid = False
            if mutation == "APPROVED_MAP_SET":
                if before_doc.get("approved_map_sha256") is not None or not _hash(after_doc.get("approved_map_sha256")):
                    errors.append(f"{prefix}:approved_map_set:invalid")
                    valid = False
            elif mutation == "APPROVED_MAP_REVISE":
                lineage = after_doc.get("approved_map_lineage")
                if not _hash(before_doc.get("approved_map_sha256")) or not _hash(after_doc.get("approved_map_sha256")):
                    errors.append(f"{prefix}:approved_map_revise:hash_invalid")
                    valid = False
                if not isinstance(lineage, list) or not lineage or lineage[-1] != before_doc.get("approved_map_sha256"):
                    errors.append(f"{prefix}:approved_map_revise:lineage_invalid")
                    valid = False
            elif mutation == "PLAN_PROJECTION_SELECT":
                if not plan_projection_transition_valid(before_doc, after_doc):
                    errors.append(f"{prefix}:plan_projection_select:invalid")
                    valid = False
            elif mutation == "APPLICABILITY_FRONTIER_ADVANCE":
                if not frontier_transition_valid(before_doc, after_doc, transition.get("frontier_command")):
                    errors.append(f"{prefix}:applicability_frontier:invalid")
                    valid = False
            elif mutation == "STALE_SET_EXTEND":
                before_stale = set(_strings(before_doc.get("stale_result_ids", [])) or [])
                after_stale = set(_strings(after_doc.get("stale_result_ids", [])) or [])
                if not before_stale.issubset(after_stale) or after_stale == before_stale:
                    errors.append(f"{prefix}:stale_set:not_strict_extension")
                    valid = False
            elif mutation in {"SOURCE_OUTCOMES_SELECT","FINAL_AUDIT_SELECT"}:
                field="current_source_outcomes" if mutation=="SOURCE_OUTCOMES_SELECT" else "current_final_audit"
                selector=after_doc.get(field)
                if not isinstance(selector,dict) or set(selector)!={"result_id","sha256","path","assignment_id","attempt_id"} or any(not isinstance(item,str) or not item for item in selector.values()):
                    errors.append(f"{prefix}:{field}:invalid")
                    valid=False
            elif mutation == "RELEASE_SEAL":
                if after_doc.get("status") != "SEALED" or not _text(after_doc.get("release_id")) or not _hash(after_doc.get("release_sha256")):
                    errors.append(f"{prefix}:release_seal:invalid")
                    valid = False
    base_hash = admission_tx.get("admission_next_current", {}).get("sha256")
    if not _hash(base_hash):
        errors.append("prep:current_transition:1:admission_next_hash_invalid")
        valid = False
    previous_hash = base_hash
    for revision in range(2, current_revision + 1):
        if revision not in by_to:
            errors.append(f"prep:current_transition:{revision}:missing")
            valid = False
            continue
        transition = by_to[revision]
        if transition.get("before_current_sha256") != previous_hash:
            errors.append(f"prep:current_transition:{revision}:before_hash_chain_mismatch")
            valid = False
        previous_hash = transition.get("after_current_sha256")
    if current.get("sha256") != previous_hash:
        errors.append(f"prep:current_transition:{current_revision}:current_hash_mismatch")
        valid = False
    for revision in sorted(by_to):
        if revision > current_revision + 1:
            errors.append(f"prep:current_transition:{revision}:future_gap")
            valid = False
        elif revision == current_revision + 1 and by_to[revision].get("before_current_sha256") != current.get("sha256"):
            errors.append(f"prep:current_transition:{revision}:pending_before_hash_mismatch")
            valid = False
    return valid


def _legacy_positive(snapshot: Any, current: Any, exact_inputs: dict[str, Any], bank: Any) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "question-space-v4":
        return False
    if "input_mode" in snapshot or "bank_binding" in snapshot or "import_binding" in snapshot:
        return False
    required = {
        "schema_version",
        "id",
        "run_id",
        "bank_path",
        "bank_sha256",
        "requirements_sha256",
        "question_ids",
        "original_bank",
    }
    if set(snapshot) - {"path", "sha256"} != required:
        return False
    snapshot_hash = _snapshot_hash(snapshot, exact_inputs)
    if not isinstance(current, dict) or current.get("run_id") != snapshot.get("run_id") or current.get("input_snapshot") != snapshot_hash:
        return False
    if _input_hash(exact_inputs, snapshot.get("bank_path")) != snapshot.get("bank_sha256"):
        return False
    original = snapshot.get("original_bank")
    if not isinstance(original, dict) or set(original) != {"path", "sha256"}:
        return False
    if _input_hash(exact_inputs, original.get("path")) != original.get("sha256"):
        return False
    if not isinstance(bank, dict):
        return False
    qids = [item.get("id") for item in bank.get("questions", []) if isinstance(item, dict)]
    if qids != snapshot.get("question_ids"):
        return False
    return hashlib.sha256(canonical_yaml_bytes(bank.get("requirements", []))).hexdigest() == snapshot.get("requirements_sha256")


def resolve_admission_state(
    snapshot: Any,
    current: Any,
    exact_inputs: Any,
    bank: Any,
    admission: Any,
    transactions: Any,
) -> dict[str, Any]:
    """Resolve the exact five-state admission state without mutating inputs."""
    errors: list[str] = []
    inputs = _input_records(exact_inputs, errors)
    txs = _transaction_collection(transactions, errors)
    if _legacy_positive(snapshot, current, inputs, bank):
        return {"state": "LEGACY_CORE", "action": "NONE", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    if not isinstance(snapshot, dict) or snapshot.get("input_mode") not in {"GOAL_TO_PLAN", "BANK_TO_PLAN"}:
        errors.append("prep:admission_state:INVALID:not_positive_legacy_or_new_mode")
        return {"state": "INVALID", "action": "FAIL", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    admission_txs = _admission_transactions(txs)
    if len(admission_txs) > 1:
        errors.append("prep:admission_state:INVALID:multiple_admission_transactions")
        return {"state": "INVALID", "action": "FAIL", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    partial_current = isinstance(current, dict) and any(field in current for field in ("admission_transaction_id", "prep_admission_sha256"))
    if not admission_txs:
        if admission is not None or partial_current:
            errors.append("prep:admission_state:INVALID:orphan_admission_output")
            return {"state": "INVALID", "action": "FAIL", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
        action = "VERIFY_EXISTING" if snapshot.get("input_mode") == "BANK_TO_PLAN" else "NONE"
        return {"state": "PREP_OPEN", "action": action, "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    tx = admission_txs[0]
    if tx.get("status") != "COMMITTED" or tx.get("transaction_type") != "PREP_ADMISSION":
        errors.append("prep:admission_state:INVALID:admission_transaction_not_committed")
        return {"state": "INVALID", "action": "FAIL", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    expected = tx.get("expected_current") if isinstance(tx.get("expected_current"), dict) else {}
    next_current = tx.get("admission_next_current") if isinstance(tx.get("admission_next_current"), dict) else {}
    current_hash = current.get("sha256") if isinstance(current, dict) else None
    if admission is None or current is None or current_hash == expected.get("sha256"):
        return {
            "state": "ADMISSION_REPLAYABLE",
            "action": "REPLAY_ADMISSION",
            "stable_binding": {},
            "current_revision": _revision(current.get("revision")) if isinstance(current, dict) else None,
            "errors": sorted(set(errors)),
        }
    if not isinstance(admission, dict) or not isinstance(current, dict):
        errors.append("prep:admission_state:INVALID:admission_or_current_malformed")
        return {"state": "INVALID", "action": "FAIL", "stable_binding": {}, "current_revision": None, "errors": sorted(set(errors))}
    local_errors: list[str] = []
    valid_chain = _validate_current_chain(current, tx, admission, _transition_transactions(txs), local_errors)
    if not valid_chain:
        errors.extend(local_errors)
        errors.append("prep:admission_state:INVALID:current_lineage_invalid")
        return {
            "state": "INVALID",
            "action": "FAIL",
            "stable_binding": {field: current.get(field) for field in CURRENT_STABLE_FIELDS},
            "current_revision": _revision(current.get("revision")),
            "errors": sorted(set(errors)),
        }
    errors.extend(local_errors)
    revision = _revision(current.get("revision"))
    pending = any(_revision(item.get("to_revision")) == (revision or 0) + 1 for item in _transition_transactions(txs))
    action = "REPLAY_CURRENT_TRANSITIONS" if pending else "NONE"
    return {
        "state": "CORE_ADMITTED",
        "action": action,
        "stable_binding": {field: current.get(field) for field in CURRENT_STABLE_FIELDS},
        "current_revision": revision,
        "errors": sorted(set(errors)),
    }


def _validate_admission_bindings(
    admission: dict[str, Any],
    snapshot: dict[str, Any],
    snapshot_hash: str | None,
    mode: str,
    inputs: dict[str, Any],
    bank: dict[str, Any],
    perspective_map: dict[str, Any],
    vector_result_id: str | None,
    vector_sha256: str | None,
    approval_sha256: str | None,
    errors: list[str],
) -> None:
    required = (
        "schema_version",
        "id",
        "run_id",
        "input_snapshot",
        "input_mode",
        "goal_sha256",
        "vector_binding",
        "approval_binding",
        "perspective_map_binding",
        "initial_set_bindings",
        "expansion_bindings",
        "normalization_decisions",
        "bank_path",
        "bank_sha256",
        "requirements_sha256",
        "question_count",
        "size_assessment",
        "source_checks",
        "uncovered_areas",
        "generation_performed",
        "status",
    )
    _required(admission, required, "prep:admission", errors)
    for field, expected in (
        ("run_id", snapshot.get("run_id")),
        ("input_snapshot", snapshot_hash),
        ("input_mode", mode),
        ("goal_sha256", snapshot.get("goal_sha256")),
        ("status", "ADMITTED"),
    ):
        if admission.get(field) != expected:
            errors.append(f"prep:admission:{field}:mismatch")
    expected_generation = "YES" if mode == "GOAL_TO_PLAN" else "NO"
    if admission.get("generation_performed") != expected_generation:
        errors.append("prep:admission:generation_performed:mismatch")
    if not _safe_path(admission.get("bank_path")) or _input_hash(inputs, admission.get("bank_path")) != admission.get("bank_sha256"):
        errors.append("prep:admission:bank_sha256:exact_input_mismatch")
    expected_requirements_hash = bank.get("requirements_sha256", _canonical_hash(bank.get("requirements", [])))
    if admission.get("requirements_sha256") != expected_requirements_hash:
        errors.append("prep:admission:requirements_sha256:mismatch")
    vector_binding = admission.get("vector_binding")
    if not isinstance(vector_binding, dict) or vector_binding.get("result_id") != vector_result_id or _binding_hash(vector_binding) != vector_sha256:
        errors.append("prep:admission:vector_binding:mismatch")
    approval_binding = admission.get("approval_binding")
    if not isinstance(approval_binding, dict) or _binding_hash(approval_binding) != approval_sha256:
        errors.append("prep:admission:approval_binding:mismatch")
    map_binding = admission.get("perspective_map_binding")
    perspective_hash = _object_hash(perspective_map, inputs, "input/perspective-map.yaml")
    if not isinstance(map_binding, dict) or map_binding.get("id") != perspective_map.get("id") or _binding_hash(map_binding) != perspective_hash:
        errors.append("prep:admission:perspective_map_binding:mismatch")
    for field in ("expansion_bindings", "normalization_decisions", "uncovered_areas"):
        if not isinstance(admission.get(field), list):
            errors.append(f"prep:admission:{field}:not_list")


def _phase_guard(phase: Any, state: str, errors: list[str]) -> None:
    if not isinstance(phase, str) or not phase:
        errors.append("prep:phase:invalid")
        return
    core_operations = {
        "core",
        "final",
        "assign-core",
        "launch-intent-core",
        "context-core",
        "accept-core",
        "commit-core",
        "validate-core",
        "validate-final",
        "seal-prepare",
        "seal-finalize",
        "verify",
    }
    if phase in core_operations and state not in {"CORE_ADMITTED", "LEGACY_CORE"}:
        errors.append(f"prep:admission_state:{state}:phase_{phase}_requires_core")
    if state == "INVALID":
        errors.append(f"prep:admission_state:INVALID:phase_{phase}_blocked")


def _validate_identity_reuse(
    perspectives: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    """Bind any present CORE P artifact to the admitted role-agent identity."""
    for result_id in sorted(docs):
        doc = docs[result_id]
        artifact_type = doc.get("artifact_type")
        if artifact_type not in {"PRIMARY", "REVIEW"}:
            continue
        if artifact_type == "REVIEW" and doc.get("review_kind") not in {"PEER", None}:
            continue
        entry = registry.get(result_id)
        registered_role = entry.get("role_id") if isinstance(entry, dict) else None
        role = _canonical_p_result_role(
            doc,
            entry,
            registered_role,
            f"prep:result:{result_id}",
            errors,
        )
        if role not in perspectives:
            continue
        agent = doc.get("agent_id", doc.get("reviewer_agent_id", doc.get("author_agent_id")))
        if agent != perspectives[role].get("agent_id"):
            errors.append(f"prep:perspective:{role}:core_identity_mismatch:{result_id}")


def frontier_transition_valid(before: Any, after: Any, command: Any) -> bool:
    if not isinstance(before,dict) or not isinstance(after,dict) or not isinstance(command,dict):
        return False
    revision=_revision(before.get("revision"))
    changed={key for key in before.keys()|after.keys() if before.get(key)!=after.get(key)}
    if (revision is None or after.get("revision")!=str(revision+1)
        or changed!=CURRENT_ALLOWED_DIFFS["APPLICABILITY_FRONTIER_ADVANCE"]
        or set(command)!={"path","sha256","bytes_b64","evidence_result_id"}):
        return False
    try:
        raw=base64.b64decode(command["bytes_b64"],validate=True)
    except (ValueError,TypeError):
        return False
    prior=before.get("applicability_frontier",{})
    frontier=after.get("applicability_frontier")
    rid=command["evidence_result_id"]
    if not isinstance(prior,dict) or not isinstance(frontier,dict) or not _text(rid):
        return False
    anchor=frontier.get(rid)
    if not isinstance(anchor,dict) or set(anchor)!={"position","sha256","affected_result_ids"}:
        return False
    position=_revision(anchor["position"])
    prior_positions=[_revision(row.get("position")) if isinstance(row,dict) else None for row in prior.values()]
    affected=_strings(anchor["affected_result_ids"])
    return (
        position is not None and 0<position<10**8
        and all(value is not None and value<position for value in prior_positions)
        and affected is not None and bool(affected) and affected==sorted(set(affected))
        and anchor["sha256"]==command["sha256"]==hashlib.sha256(raw).hexdigest()
        and command["path"]==f"transactions/commands/{position:08d}.yaml"
        and frontier=={**prior,rid:anchor}
    )


def plan_projection_transition_valid(before: Any, after: Any) -> bool:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    revision = _revision(before.get("revision"))
    projection = after.get("current_plan_projection")
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    return (
        revision is not None and after.get("revision") == str(revision + 1)
        and changed == CURRENT_ALLOWED_DIFFS["PLAN_PROJECTION_SELECT"]
        and isinstance(projection, dict) and set(projection) == {"plan_version", "stages"}
        and _text(projection.get("plan_version")) and isinstance(projection.get("stages"), list)
        and bool(projection["stages"])
        and after.get("current_plan_projection_sha256") == _canonical_hash(projection)
    )


def _validate_plan_projection(
    current: Any,
    registry: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if not isinstance(current, dict):
        return
    projection = current.get("current_plan_projection")
    projection_hash = current.get("current_plan_projection_sha256")
    if projection is None and projection_hash is None:
        return
    if not isinstance(projection, dict):
        errors.append("prep:current_transition:projection:not_mapping")
        return
    if projection_hash != hashlib.sha256(canonical_yaml_bytes(projection)).hexdigest():
        errors.append("prep:current_transition:projection:sha256_mismatch")
    if set(projection) != {"plan_version", "stages"} or not _text(projection.get("plan_version")):
        errors.append("prep:current_transition:projection:shape_invalid")
    rows = _list(projection.get("stages"))
    if rows is None or not rows:
        errors.append("prep:current_transition:projection:stages_invalid")
        return
    stage_ids: set[str] = set()
    result_ids: set[str] = set()
    stale = set(_strings(current.get("stale_result_ids", [])) or [])
    for index, row in enumerate(rows):
        prefix = f"prep:current_transition:projection:stage:{index}"
        required = {"logical_stage_id", "result_id", "sha256", "assignment_id", "attempt_id"}
        if not isinstance(row, dict) or set(row) != required:
            errors.append(f"{prefix}:shape_invalid")
            continue
        if any(not isinstance(row[field], str) or not row[field] for field in required):
            errors.append(f"{prefix}:field_invalid")
            continue
        if not _hash(row["sha256"]):
            errors.append(f"{prefix}:sha256_invalid")
        if row["logical_stage_id"] in stage_ids or row["result_id"] in result_ids:
            errors.append(f"{prefix}:duplicate_stage_or_result")
        stage_ids.add(row["logical_stage_id"])
        result_ids.add(row["result_id"])
        entry, doc = _accepted_result(row["result_id"], registry, docs, prefix, errors)
        if entry is None or doc is None:
            continue
        if entry.get("artifact_type") != "PLAN" or doc.get("artifact_type") != "PLAN":
            errors.append(f"{prefix}:not_PLAN")
        for field in ("assignment_id", "attempt_id"):
            if entry.get(field) != row[field]:
                errors.append(f"{prefix}:{field}:registry_mismatch")
        if _entry_hash(entry) != row["sha256"]:
            errors.append(f"{prefix}:sha256:registry_mismatch")
        if doc.get("plan_version") != projection.get("plan_version"):
            errors.append(f"{prefix}:plan_version:mismatch")
        identity = doc.get("identity_result")
        if not isinstance(identity, dict) or identity.get("stage_id") != row["logical_stage_id"]:
            errors.append(f"{prefix}:logical_stage_id:document_mismatch")
        if row["result_id"] in stale:
            errors.append(f"{prefix}:selected_result_stale")
        dependency_ids = _strings(entry.get("dependency_result_ids", [])) or []
        stale_dependencies = sorted(set(dependency_ids) & stale)
        if stale_dependencies:
            errors.append(f"{prefix}:selected_dependency_stale:{','.join(stale_dependencies)}")


def validate_prep(
    snapshot: Any,
    current: Any,
    registry: Any,
    docs: Any,
    exact_inputs: Any,
    bank: Any,
    approval: Any,
    perspective_map: Any,
    admission: Any,
    transactions: Any,
    phase: Any,
) -> list[str]:
    """Return deterministic, addressable structural preparation errors."""
    errors: list[str] = []
    inputs = _input_records(exact_inputs, errors)
    accepted, accepted_docs = _registries(registry, docs, errors)
    state = resolve_admission_state(snapshot, current, exact_inputs, bank, admission, transactions)
    errors.extend(state["errors"])
    _phase_guard(phase, state["state"], errors)
    if state["state"] == "LEGACY_CORE":
        return sorted(set(errors))
    snapshot_value, snapshot_hash = _validate_snapshot(snapshot, inputs, errors)
    mode = snapshot_value.get("input_mode")
    if mode not in {"GOAL_TO_PLAN", "BANK_TO_PLAN"}:
        return sorted(set(errors))
    fixture = bool(snapshot_value.get("test_fixture") is True or snapshot_value.get("fixture_only") == "YES")

    vector_result_id: str | None = None
    vector_sha256: str | None = None
    approval_sha256: str | None = None
    import_binding: dict[str, Any] | None = None
    external_binding: dict[str, Any] | None = None
    if mode == "GOAL_TO_PLAN":
        if snapshot_value.get("bank_binding") != "DEFERRED_TO_PREP_ADMISSION":
            errors.append("prep:snapshot:bank_binding:not_deferred")
        if "import_binding" in snapshot_value:
            errors.append("prep:snapshot:import_binding:prohibited_for_goal")
        if approval is not None:
            vector_result_id, vector_sha256 = _validate_current_approval(
                approval, snapshot_value, snapshot_hash, accepted, accepted_docs, inputs, fixture, errors
            )
            approval_sha256 = _object_hash(approval, inputs, "input/vector-approval.yaml")
            if not _hash(approval_sha256):
                errors.append("prep:approval:sha256:invalid")
    else:
        if snapshot_value.get("bank_binding") != "BOUND_AT_INIT":
            errors.append("prep:snapshot:bank_binding:not_bound_at_init")
        kind=snapshot_value.get("bank_entry_kind","APPROVED_RUN_IMPORT" if "import_binding" in snapshot_value else None)
        if kind=="EXTERNAL_READY_BANK":
            if "import_binding" in snapshot_value: errors.append("prep:snapshot:import_binding:prohibited_for_external")
            external_binding=_validate_external_bank(snapshot_value,inputs,errors)
            if approval is not None:
                vector_result_id,vector_sha256=_validate_current_approval(approval,snapshot_value,snapshot_hash,accepted,accepted_docs,inputs,fixture,errors)
                approval_sha256=_object_hash(approval,inputs,"input/vector-approval.yaml")
                if not _hash(approval_sha256): errors.append("prep:approval:sha256:invalid")
        elif kind=="APPROVED_RUN_IMPORT":
            vector, foreign_approval, import_binding = _validate_foreign_import(
                snapshot_value, approval, inputs, fixture, errors
            )
            if import_binding is not None:
                vector_result_id = import_binding.get("source_vector_result_id")
                vector_sha256 = import_binding.get("vector_sha256")
                approval_sha256 = import_binding.get("approval_sha256")
        else:
            errors.append("prep:snapshot:bank_entry_kind:invalid")

    if admission is None:
        return sorted(set(errors))
    if not isinstance(admission, dict):
        errors.append("prep:admission:not_mapping")
        return sorted(set(errors))
    if not isinstance(bank, dict):
        errors.append("prep:bank:not_mapping")
        return sorted(set(errors))

    perspectives = _validate_perspective_map(
        perspective_map,
        snapshot_value,
        snapshot_hash,
        vector_result_id,
        vector_sha256,
        approval_sha256,
        errors,
    )
    _validate_prep_framing_profiles(accepted, accepted_docs, perspectives, errors)
    _validate_identity_reuse(perspectives, accepted, accepted_docs, errors)
    _validate_admission_bindings(
        admission,
        snapshot_value,
        snapshot_hash,
        mode,
        inputs,
        bank,
        perspective_map if isinstance(perspective_map, dict) else {},
        vector_result_id,
        vector_sha256,
        approval_sha256,
        errors,
    )
    initial_questions: set[str] = set()
    result_roles: dict[str, str] = {}
    if mode == "GOAL_TO_PLAN":
        initial_questions, result_roles = _validate_initial_framings(
            admission,
            accepted,
            accepted_docs,
            perspectives,
            vector_result_id,
            vector_sha256,
            approval_sha256,
            snapshot_hash,
            errors,
        )
    elif admission.get("initial_set_bindings") != []:
        errors.append("prep:admission:initial_set_bindings:bank_mode_must_be_empty")

    known_results = set(accepted)
    question_count = _question_count(admission, errors)
    qids, requirements = _validate_bank(
        bank,
        question_count,
        mode,
        known_results,
        vector_result_id if mode == "BANK_TO_PLAN" else None,
        initial_questions,
        errors,
    )
    _validate_size(admission, question_count, errors)
    allowed_refs = _reference_set(accepted, accepted_docs, inputs)
    allowed_refs.update(qids)
    allowed_refs.update(requirements)
    _validate_source_checks(admission, perspectives, allowed_refs, errors)

    txs = _transaction_collection(transactions, errors)
    admission_txs = _admission_transactions(txs)
    if len(admission_txs) != 1:
        errors.append(f"prep:admission_transaction:cardinality:{len(admission_txs)}:expected_1")
    else:
        tx = admission_txs[0]
        _validate_admission_transaction(tx, mode, snapshot_value, snapshot_hash, admission, inputs, errors)
        if mode == "GOAL_TO_PLAN":
            if tx.get("current_vector_result_id") != vector_result_id or tx.get("current_vector_sha256") != vector_sha256:
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:vector_binding:mismatch")
            if tx.get("current_approval_sha256") != approval_sha256:
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:approval_binding:mismatch")
        elif external_binding is not None:
            if tx.get("current_vector_result_id")!=vector_result_id or tx.get("current_vector_sha256")!=vector_sha256:
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:vector_binding:mismatch")
            if tx.get("current_approval_sha256")!=approval_sha256:
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:approval_binding:mismatch")
            if tx.get("external_payload_sha256")!=external_binding.get("external_payload_sha256"):
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:external_payload_sha256:mismatch")
            _validate_applicability(tx.get("applicability_result_id"),tx.get("applicability_sha256"),snapshot_value,snapshot_hash,external_binding,admission,bank,accepted,accepted_docs,errors)
        elif import_binding is not None:
            if tx.get("import_payload_sha256") != import_binding.get("import_payload_sha256"):
                errors.append(f"prep:admission_transaction:{tx.get('transaction_id')}:import_payload_sha256:mismatch")
            _validate_applicability(
                tx.get("applicability_result_id"),
                tx.get("applicability_sha256"),
                snapshot_value,
                snapshot_hash,
                import_binding,
                admission,
                bank,
                accepted,
                accepted_docs,
                errors,
            )

    if isinstance(current, dict) and state["state"] == "CORE_ADMITTED":
        if current.get("input_snapshot") != snapshot_hash:
            errors.append("prep:core_binding:current:input_snapshot:mismatch")
        admission_hash = admission_txs[0].get("admission", {}).get("sha256") if len(admission_txs) == 1 else None
        if current.get("prep_admission_sha256") != admission_hash:
            errors.append("prep:core_binding:current:prep_admission_sha256:mismatch")
        _validate_plan_projection(current, accepted, accepted_docs, errors)
    return sorted(set(errors))


__all__ = [
    "ADMISSION_ACTIONS",
    "ADMISSION_STATES",
    "ALL_ROLES",
    "PERSPECTIVE_ROLES",
    "PRESERVED_COUNTS",
    "SOURCE_CHECK_IDS",
    "canonical_yaml_bytes",
    "resolve_admission_state",
    "validate_prep",
]
