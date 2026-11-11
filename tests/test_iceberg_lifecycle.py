"""Public controller receipts for the Double-Iceberg lifecycle."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_iceberg_contracts import (
    CompleteIcebergModel,
    _refresh_hash_bindings,
)


HERE = Path(__file__).resolve().parents[1]
QS = HERE / "scripts" / "qs.py"
spec = importlib.util.spec_from_file_location("qs_iceberg_lifecycle", QS)
assert spec and spec.loader
qs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = qs
spec.loader.exec_module(qs)


def put(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(qs.canon_yaml(value))


class IcebergLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def cli(self, *args: str, ok: bool = True) -> dict:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        done = subprocess.run(
            [sys.executable, "-B", str(QS), *args], text=True,
            capture_output=True, env=env,
        )
        value = json.loads(done.stdout or done.stderr)
        self.assertEqual(done.returncode == 0, ok, value)
        return value

    def test_new_goal_and_bank_snapshots_pin_feature_while_legacy_is_unmarked(self) -> None:
        goal = self.base / "goal.md"
        goal.write_text("Deliver an inspectable plan.\n", encoding="utf-8")
        bank = self.base / "bank.yaml"
        put(bank, {"requirements": ["R-1"], "questions": [{"id": "Q-1", "text": "Check?", "source_offset": "1"}]})
        original = self.base / "original.md"
        original.write_text("Q source\n", encoding="utf-8")

        for run_id, extra in (
            ("RUN-GOAL", ["--mode", "GOAL_TO_PLAN", "--goal", str(goal)]),
            ("RUN-BANK", ["--mode", "BANK_TO_PLAN", "--goal", str(goal), "--bank", str(bank), "--external-bank", str(original)]),
        ):
            workspace = self.base / run_id
            self.cli("init", "--root", str(workspace), "--run-id", run_id, *extra)
            snapshot = qs.load_yaml(workspace / "runs" / run_id / "input" / "snapshot.yaml")
            self.assertEqual(snapshot["methodology_features"], ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"])

        legacy_workspace = self.base / "legacy"
        self.cli("init", "--root", str(legacy_workspace), "--run-id", "RUN-LEGACY", "--bank", str(bank), "--original-bank", str(original))
        legacy = qs.load_yaml(legacy_workspace / "runs" / "RUN-LEGACY" / "input" / "snapshot.yaml")
        self.assertNotIn("methodology_features", legacy)

    def marked_run(self) -> tuple[Path, dict, dict, str]:
        run = self.base / "RUN-ICE"
        for directory in ("input", "state", "transactions", "intents", "operators", "positions", "answers", "hypotheses", "evidence", "decisions", "reviews", "experiments", "synthesis", "plan", "audit", "gates"):
            (run / directory).mkdir(parents=True, exist_ok=True)
        goal = run / "input" / "goal.md"
        goal.write_text("Goal\n", encoding="utf-8")
        snapshot_doc = {
            "schema_version": qs.SCHEMA, "id": "SNAP-ICE", "run_id": "RUN-ICE",
            "input_mode": "GOAL_TO_PLAN", "goal_path": "input/goal.md",
            "goal_sha256": qs.sha(goal.read_bytes()), "materials": [],
            "bank_binding": "DEFERRED_TO_PREP_ADMISSION",
            "methodology_features": ["DOUBLE_ICEBERG_V1"],
        }
        put(run / "input" / "snapshot.yaml", snapshot_doc)
        snapshot = qs.sha((run / "input" / "snapshot.yaml").read_bytes())
        current = {"schema_version": qs.SCHEMA, "run_id": "RUN-ICE", "input_snapshot": snapshot, "input_mode": "GOAL_TO_PLAN", "revision": "0", "status": "PREP_OPEN"}
        state = {"schema_version": qs.SCHEMA, "run_id": "RUN-ICE", "input_snapshot": snapshot, "results": {}, "attempts": {}, "registered": {}, "current_attempts": {}}
        put(run / "state" / "current.yaml", current)
        put(run / "state" / "assignments.yaml", state)
        put(run / "state" / "launch-intents.yaml", {"schema_version": qs.SCHEMA, "run_id": "RUN-ICE", "input_snapshot": snapshot, "records": {}})
        return run, state, current, snapshot

    def core_blind_run(self) -> tuple[Path, Path, dict, dict, str, str, Path]:
        workspace = self.base / "core-context-work"
        run = workspace / "runs" / "RUN-CORE"
        for directory in ("input", "state", "transactions", "intents", "operators", "positions", "answers", "hypotheses", "evidence", "decisions", "reviews", "experiments", "synthesis", "plan", "audit", "gates"):
            (run / directory).mkdir(parents=True, exist_ok=True)
        goal_input = run / "input" / "goal.md"
        goal_input.write_text("Declared public Core context fixture.\n", encoding="utf-8")
        bank = {
            "requirements": ["R-1"],
            "questions": [{"id": "BANK-Q-SECRET", "text": "BANK_QUESTION_BYTES_MUST_STAY_HIDDEN", "source_offset": "BANK_SOURCE_LOCATOR_SECRET"}],
        }
        put(run / "input" / "bank.yaml", bank)
        snapshot_doc = {
            "schema_version": qs.SCHEMA, "id": "SNAP-CORE", "run_id": "RUN-CORE",
            "input_mode": "GOAL_TO_PLAN", "goal_path": "input/goal.md",
            "goal_sha256": qs.sha(goal_input.read_bytes()), "materials": [],
            "bank_binding": "DEFERRED_TO_PREP_ADMISSION",
            "methodology_features": [qs.ICEBERG_FEATURE],
        }
        put(run / "input" / "snapshot.yaml", snapshot_doc)
        snapshot = qs.sha((run / "input" / "snapshot.yaml").read_bytes())
        vector = {
            "schema_version": qs.SCHEMA, "id": "VECTOR-1", "run_id": "RUN-CORE", "input_snapshot": snapshot,
            "author_role": "OP-VECTOR", "role_id": "OP-VECTOR", "agent_id": "/root/vector", "artifact_type": "OPERATOR",
            "knowledge_status": "DERIVED", "workflow_status": "ACCEPTED", "source_refs": ["VECTOR_SOURCE_HASH_MARKER"],
            "related_ids": ["R-1"], "revision": "1", "created_at": "2026-10-23T00:00:00Z", "updated_at": "2026-10-23T00:00:00Z",
            "G": "approved goal", "NOT_G": ["unapproved outcome"],
            "scope": "approved scope for input/materials/002-declared-fixture.json and supplied bank count 1",
            "requirements": ["R-1"],
            "requirement_definitions": [{
                "id": "R-1", "text": "Preserve the approved bounded behavior.",
                "hard_requirement": "true", "approved_contract": "true", "working_assumption": "false",
                "source": "REQUIREMENT_SOURCE_LOCATOR_MUST_STAY_HIDDEN",
                "acceptance": "REQUIREMENT_ACCEPTANCE_LOCATOR_MUST_STAY_HIDDEN",
            }],
            "hard_constraints": ["APPROVED_POLICY_CONSTRAINT_MUST_BE_VISIBLE"],
            "authority": {"mode": "SIMULATED_TEST", "prohibited": ["external mutation"]},
            "value_recipient": "owner", "success_criteria": ["HIDDEN_SUCCESS_CRITERION"],
            "changeable_assumptions": [], "current_state": ["HIDDEN_CURRENT_ROUTE_ORACLE"],
            "resources": ["HIDDEN_RESOURCE_DETAIL"], "owner_decisions": [],
            "open_research_questions": ["HIDDEN_PREFERRED_MECHANISM"],
        }
        put(run / "operators" / "VECTOR-1.yaml", vector)
        vector_hash = qs.sha((run / "operators" / "VECTOR-1.yaml").read_bytes())
        approval = {"approval_id": "APP-1", "vector_result_id": "VECTOR-1", "vector_sha256": vector_hash}
        put(run / "input" / "vector-approval.yaml", approval)
        approval_hash = qs.sha((run / "input" / "vector-approval.yaml").read_bytes())
        rows = []
        for number in range(1, 11):
            role = f"P-{number:02d}"
            profile = {
                "role_id": role, "agent_id": "/root/" + role, "title": "title " + role,
                "competence": "competence " + role, "analysis_scope": "analysis " + role,
                "unique_contribution": "OWN_PROFILE_MARKER" if role == "P-01" else "FOREIGN_PROFILE_MARKER_" + role,
                "boundary": "boundary " + role, "plausible_blindspots": "blindspots " + role,
            }
            rows.append({**profile, "profile_sha256": qs.sha(qs.canon_yaml(profile))})
        perspective_map = {
            "schema_version": qs.SCHEMA, "id": "MAP-CORE", "run_id": "RUN-CORE", "input_snapshot": snapshot,
            "vector_result_id": "VECTOR-1", "vector_sha256": vector_hash, "approval_sha256": approval_hash,
            "selection_authority": "ROOT", "perspectives": rows,
        }
        map_path = run / "input" / "perspective-map.yaml"
        put(map_path, perspective_map)
        map_hash = qs.sha(map_path.read_bytes())
        admission = {
            "id": "ADMISSION-CORE", "run_id": "RUN-CORE", "input_snapshot": snapshot, "status": "ADMITTED",
            "vector_binding": {"result_id": "VECTOR-1", "sha256": vector_hash},
            "approval_binding": {"id": "APP-1", "sha256": approval_hash},
            "perspective_map_binding": {"id": "MAP-CORE", "sha256": map_hash},
        }
        put(run / "input" / "prep-admission.yaml", admission)
        admission_hash = qs.sha((run / "input" / "prep-admission.yaml").read_bytes())
        current = {
            "schema_version": qs.SCHEMA, "run_id": "RUN-CORE", "input_snapshot": snapshot,
            "input_mode": "GOAL_TO_PLAN", "revision": "0", "status": "CORE_ADMITTED",
            "prep_admission_sha256": admission_hash, "bank_sha256": qs.sha((run / "input" / "bank.yaml").read_bytes()),
        }
        state = {
            "schema_version": qs.SCHEMA, "run_id": "RUN-CORE", "input_snapshot": snapshot,
            "results": {"VECTOR-1": {"result_id": "VECTOR-1", "content_hash": vector_hash, "path": "operators/VECTOR-1.yaml", "role_id": "OP-VECTOR", "artifact_type": "OPERATOR", "input_snapshot": snapshot}},
            "attempts": {}, "registered": {}, "current_attempts": {},
        }
        put(run / "state" / "current.yaml", current)
        put(run / "state" / "assignments.yaml", state)
        put(run / "state" / "events.yaml", {"schema_version": qs.SCHEMA, "run_id": "RUN-CORE", "events": []})
        put(run / "state" / "launch-intents.yaml", {"schema_version": qs.SCHEMA, "run_id": "RUN-CORE", "input_snapshot": snapshot, "records": {}})
        assignment = {
            "assignment_id": "A-CORE-P01", "attempt_id": "TRY-1", "role_id": "P-01", "agent_id": "/root/P-01",
            "input_snapshot": snapshot, "artifact_type": "FRAMING", "output_path": "positions/CORE-P01.yaml",
            "dependency_hashes": [], "result_id": "CORE-P01", "lifecycle_scope": "CORE",
            "prep_admission_sha256": admission_hash, "goal_result_id": "VECTOR-1", "assigned_ids": {},
            "role_instruction_paths": ["references/roles/perspectives.md"],
            "allowed_reads": ["references/phases/01-intake.md", "references/invariants.md"],
            "blind_handoff": {
                "handoff_id": "HANDOFF-CORE-P01", "assignment_id": "A-CORE-P01", "attempt_id": "TRY-1",
                "role_id": "P-01", "input_snapshot": snapshot, "phase": "FRAMING",
                "objective": "produce one bounded own-origin position",
                "constraints": ["do not read the admitted source bank", "do not read foreign P results"],
            },
        }
        return workspace, run, assignment, rows[0], vector_hash, approval_hash, map_path

    def framing(self, snapshot: str, *, include_position: bool) -> tuple[dict, dict]:
        vector_hash = "a" * 64
        result = {
            "id": "FRAME-P01", "author_role": "P-01", "assignment_id": "ASSIGN-P01",
            "input_snapshot": snapshot, "vector_result_id": "VECTOR-1", "vector_sha256": vector_hash,
        }
        if include_position:
            result["iceberg_position"] = {
                "position_id": "ICEPOS-P01", "origin_role": "P-01",
                "origin_assignment_id": "ASSIGN-P01", "input_snapshot": snapshot,
                "elements": [{
                    "seed_id": "SEED-P01", "candidate_view": "CURRENT_REALITY",
                    "element_kind": "ASSUMPTION", "statement": "A material assumption",
                    "scope": "declared scope", "g_refs": ["VECTOR-1"], "r_refs": ["R-1"],
                    "materiality": "changes a decision", "knowledge_status": "HYPOTHESIS",
                    "source_refs": [], "finite_check": {"check_kind": "OBSERVATION", "procedure": "observe the boundary", "discriminating_outcomes": ["supported", "refuted"]},
                }],
                "relations": [],
            }
        entry = {"result_id": "FRAME-P01", "content_hash": qs.sha(qs.canon_yaml(result)), "path": "positions/FRAME-P01.yaml", "role_id": "P-01", "artifact_type": "FRAMING", "input_snapshot": snapshot}
        return result, entry

    def test_blind_position_is_rejected_before_commit_and_valid_position_passes(self) -> None:
        run, state, current, snapshot = self.marked_run()
        vector_hash = "a" * 64
        state["results"]["VECTOR-1"] = {"result_id": "VECTOR-1", "content_hash": vector_hash, "path": "operators/VECTOR-1.yaml", "role_id": "OP-VECTOR", "artifact_type": "OPERATOR", "input_snapshot": snapshot}
        put(run / "input" / "vector-approval.yaml", {"vector_result_id": "VECTOR-1", "vector_sha256": vector_hash})
        missing, missing_entry = self.framing(snapshot, include_position=False)
        with self.assertRaisesRegex(qs.QSError, "iceberg_position_invalid"):
            qs._validate_iceberg_candidate(run, state, current, missing, missing_entry)
        self.assertFalse((run / missing_entry["path"]).exists())
        valid, valid_entry = self.framing(snapshot, include_position=True)
        qs._validate_iceberg_candidate(run, state, current, valid, valid_entry)

    def test_core_blind_public_context_projects_only_approved_vector_and_own_profile(self) -> None:
        workspace, run, assignment, own_profile_row, vector_hash, approval_hash, map_path = self.core_blind_run()
        bad_agent = {**assignment, "assignment_id": "A-CORE-P01-BAD-AGENT", "result_id": "CORE-P01-BAD-AGENT", "output_path": "positions/CORE-P01-BAD-AGENT.yaml", "agent_id": "/root/not-p01"}
        bad_agent["blind_handoff"] = {**assignment["blind_handoff"], "handoff_id": "HANDOFF-CORE-P01-BAD-AGENT", "assignment_id": bad_agent["assignment_id"]}
        bad_agent_path = self.base / "core-p01-bad-agent.assignment.yaml"
        put(bad_agent_path, bad_agent)
        self.cli("assign", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(bad_agent_path))
        mismatch = self.cli("context", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(bad_agent_path), "--output", str(self.base / "bad-agent.context.yaml"), ok=False)
        self.assertEqual(mismatch["error"], "core_blind_profile_binding")

        assignment_path = self.base / "core-p01.assignment.yaml"
        put(assignment_path, assignment)
        self.cli("assign", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(assignment_path))
        original_map = map_path.read_bytes()
        tampered_map = qs.load_yaml(map_path)
        tampered_map["perspectives"][0]["title"] = "tampered title"
        tampered_profile = {key: tampered_map["perspectives"][0][key] for key in qs.PERSPECTIVE_PROFILE_FIELDS}
        tampered_map["perspectives"][0]["profile_sha256"] = qs.sha(qs.canon_yaml(tampered_profile))
        put(map_path, tampered_map)
        tampered = self.cli("context", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(assignment_path), "--output", str(self.base / "tampered-map.context.yaml"), ok=False)
        self.assertEqual(tampered["error"], "core_blind_perspective_map_binding")
        map_path.write_bytes(original_map)

        context_path = self.base / "core-p01.context.yaml"
        made = self.cli("context", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(assignment_path), "--output", str(context_path))
        packet = qs.load_yaml(context_path)
        self.assertEqual(made["status"], "CONTEXT_READY")
        self.assertEqual(packet["goal"]["requirements"], ["R-1"])
        self.assertEqual(packet["goal"]["requirement_definitions"], [{
            "id": "R-1", "text": "Preserve the approved bounded behavior.",
            "hard_requirement": "true", "approved_contract": "true", "working_assumption": "false",
        }])
        self.assertEqual(packet["goal"]["scope"], "approved scope for input/materials/002-declared-fixture.json and supplied bank count 1")
        self.assertEqual(packet["goal"]["hard_constraints"], ["APPROVED_POLICY_CONSTRAINT_MUST_BE_VISIBLE"])
        self.assertEqual(packet["goal"]["authority"], {"mode": "SIMULATED_TEST", "prohibited": ["external mutation"]})
        self.assertEqual(packet["perspective_profile"], {key: own_profile_row[key] for key in qs.PERSPECTIVE_PROFILE_FIELDS})
        self.assertEqual(packet["perspective_profile_hash"], own_profile_row["profile_sha256"])
        self.assertEqual((packet["vector_sha256"], packet["approval_sha256"]), (vector_hash, approval_hash))
        encoded = context_path.read_text(encoding="utf-8")
        for forbidden in (
            "BANK-Q-SECRET", "BANK_QUESTION_BYTES_MUST_STAY_HIDDEN", "BANK_SOURCE_LOCATOR_SECRET",
            "REQUIREMENT_SOURCE_LOCATOR_MUST_STAY_HIDDEN", "REQUIREMENT_ACCEPTANCE_LOCATOR_MUST_STAY_HIDDEN",
            "VECTOR_SOURCE_HASH_MARKER", "HIDDEN_SUCCESS_CRITERION", "HIDDEN_CURRENT_ROUTE_ORACLE",
            "HIDDEN_RESOURCE_DETAIL", "HIDDEN_PREFERRED_MECHANISM", "FOREIGN_PROFILE_MARKER_P-02",
            qs.sha((run / "input" / "bank.yaml").read_bytes()),
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertNotIn("perspectives", packet)

        result = {
            "schema_version": qs.SCHEMA, "id": "CORE-P01", "run_id": "RUN-CORE", "input_snapshot": assignment["input_snapshot"],
            "author_role": "P-01", "agent_id": "/root/P-01", "knowledge_status": "HYPOTHESIS", "workflow_status": "ACCEPTED",
            "source_refs": [], "related_ids": [], "revision": "1", "created_at": "2026-10-23T00:00:00Z", "updated_at": "2026-10-23T00:00:00Z",
            "assignment_id": "A-CORE-P01", "attempt_id": "TRY-1", "artifact_type": "FRAMING", "lifecycle_scope": "CORE",
            "prep_admission_sha256": assignment["prep_admission_sha256"], "context_packet_sha256": made["sha256"],
            "vector_result_id": "VECTOR-1", "vector_sha256": vector_hash, "approval_sha256": approval_hash,
            "perspective_profile_hash": own_profile_row["profile_sha256"],
            "iceberg_position": {
                "position_id": "ICEPOS-CORE-P01", "origin_role": "P-01", "origin_assignment_id": "A-CORE-P01",
                "input_snapshot": assignment["input_snapshot"], "relations": [],
                "elements": [{
                    "seed_id": "SEED-CORE-P01", "candidate_view": "CURRENT_REALITY", "element_kind": "ASSUMPTION",
                    "statement": "A bounded own-origin assumption", "scope": packet["goal"]["scope"],
                    "g_refs": [packet["goal"]["result_id"]], "r_refs": [packet["goal"]["requirements"][0]],
                    "materiality": "changes one bounded decision", "knowledge_status": "HYPOTHESIS", "source_refs": [],
                    "finite_check": {"check_kind": "OBSERVATION", "procedure": "observe the declared boundary", "discriminating_outcomes": ["supports", "refutes"]},
                }],
            },
        }
        result_path = self.base / "core-p01.result.yaml"
        put(result_path, result)
        accepted = self.cli("accept", "--root", str(workspace), "--run-id", "RUN-CORE", "--assignment", str(assignment_path), "--result", str(result_path))
        self.assertEqual(accepted["status"], "ACCEPTED")

    def test_blind_materials_require_explicit_brief_and_reject_hidden_bank_bytes(self) -> None:
        run = self.base / "material-run"
        brief = run / "input" / "materials" / "001-brief.md"
        bank = run / "input" / "bank.yaml"
        hidden = run / "input" / "materials" / "002-hidden-bank.yaml"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text("Root-approved bounded case facts.\n", encoding="utf-8")
        bank.parent.mkdir(parents=True, exist_ok=True)
        bank.write_text("questions: [hidden oracle]\n", encoding="utf-8")
        hidden.write_bytes(bank.read_bytes())
        snapshot = {
            "materials": [
                {"path": "input/materials/001-brief.md", "sha256": qs.sha(brief.read_bytes())},
                {"path": "input/materials/002-hidden-bank.yaml", "sha256": qs.sha(hidden.read_bytes())},
            ],
            "bank_path": "input/bank.yaml", "bank_sha256": qs.sha(bank.read_bytes()),
        }
        self.assertEqual(qs.prep_framing_material_paths(run, snapshot, []), [])
        self.assertEqual(
            qs.prep_framing_material_paths(run, snapshot, ["input/materials/001-brief.md"]),
            ["input/materials/001-brief.md"],
        )
        with self.assertRaisesRegex(qs.QSError, "prep_framing_material_forbidden"):
            qs.prep_framing_material_paths(run, snapshot, ["input/materials/002-hidden-bank.yaml"])

    def add_view(self, run: Path, state: dict, snapshot: str, result_id: str, kind: str, revision: str, *, supersedes_result: str | None = None, supersedes_view: str | None = None, source_id: str = "E-1") -> dict:
        view_id = kind + "-" + revision
        view = {"view_id": view_id, "view_kind": kind, "revision": revision, "input_snapshot": snapshot, "workflow_status": "ACCEPTED", "source_bindings": [{"source_kind": "ACCEPTED_RESULT", "source_id": source_id, "source_sha256": "b" * 64, "source_path": "evidence/" + source_id + ".yaml"}]}
        if supersedes_view is not None:
            view["supersedes_view_id"] = supersedes_view
        if supersedes_result is not None:
            predecessor = state["results"][supersedes_result]
            view["source_bindings"].append({"source_kind": "ACCEPTED_RESULT", "source_id": supersedes_result, "source_sha256": predecessor["content_hash"], "source_path": predecessor["path"]})
        doc = {"id": result_id, "iceberg_view": view}
        if supersedes_result is not None:
            doc["supersedes"] = supersedes_result
        role = qs.ICEBERG_VIEW_OWNERS[kind]
        path = "operators/" + result_id + ".yaml"
        put(run / path, doc)
        state["results"][result_id] = {"result_id": result_id, "content_hash": qs.sha((run / path).read_bytes()), "path": path, "role_id": role, "artifact_type": "OPERATOR", "input_snapshot": snapshot}
        return doc

    def test_revision_heads_branch_rejection_core_filter_and_historical_tamper(self) -> None:
        run, state, current, snapshot = self.marked_run()
        self.add_view(run, state, snapshot, "CUR-1", "CURRENT_REALITY", "1")
        self.add_view(run, state, snapshot, "DES-1", "DESIRED_REALITY", "1", source_id="E-2")
        current2 = self.add_view(run, state, snapshot, "CUR-2", "CURRENT_REALITY", "2", supersedes_result="CUR-1", supersedes_view="CURRENT_REALITY-1")
        current["stale_result_ids"] = ["CUR-1"]
        projection, history = qs.iceberg_projection_for_run(run, state, current, allow_partial=False)
        self.assertEqual(projection["CURRENT_REALITY"]["result_id"], "CUR-2")
        self.assertEqual(history, {"CUR-1"})

        files = {str(path.relative_to(run)) for path in qs.core_files(run, state, snapshot, current)}
        self.assertNotIn("operators/CUR-1.yaml", files)
        self.assertIn("operators/CUR-2.yaml", files)
        self.assertIn("operators/DES-1.yaml", files)

        branch = {**current2, "id": "CUR-BRANCH", "supersedes": "CUR-1", "iceberg_view": {**current2["iceberg_view"], "view_id": "CURRENT-BRANCH"}}
        branch_entry = {"result_id": "CUR-BRANCH", "content_hash": qs.sha(qs.canon_yaml(branch)), "path": "operators/CUR-BRANCH.yaml", "role_id": "OP-PROJECTION", "artifact_type": "OPERATOR", "input_snapshot": snapshot}
        with self.assertRaisesRegex(qs.QSError, "iceberg_view_invalid"):
            qs._validate_iceberg_candidate(run, state, current, branch, branch_entry)
        self.assertFalse((run / "operators" / "CUR-BRANCH.yaml").exists())

        put(run / "state" / "assignments.yaml", state)
        put(run / "state" / "current.yaml", current)
        qs.accepted(run)
        (run / "operators" / "CUR-1.yaml").write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(qs.QSError, "immutable_result_tamper:CUR-1"):
            qs.accepted(run)

    def test_changed_evidence_invalidates_only_exact_consumers(self) -> None:
        results = {item: {} for item in ("E-1", "E-2", "H-1", "Q-1", "D-1", "CUR", "DES")}
        docs = {
            "H-1": {"dependency_edges": [{"from_id": "E-1", "to_id": "H-1"}]},
            "Q-1": {"dependency_edges": [{"from_id": "H-1", "to_id": "Q-1"}]},
            "D-1": {"dependency_edges": [{"from_id": "E-2", "to_id": "D-1"}]},
            "CUR": {"iceberg_view": {"source_bindings": [{"source_kind": "ACCEPTED_RESULT", "source_id": "E-1"}]}},
            "DES": {"iceberg_view": {"source_bindings": [{"source_kind": "ACCEPTED_RESULT", "source_id": "E-2"}]}},
        }
        self.assertEqual(qs.stale_result_closure(results, docs, ["E-1"]), {"E-1", "H-1", "Q-1", "CUR"})

    def test_historical_view_is_nonblocking_only_without_current_consumer(self) -> None:
        docs = {
            "SYN-OLD": {"iceberg_view_bindings": {"CURRENT_REALITY": {"result_id": "CUR-1"}}},
            "SYN-CURRENT": {"iceberg_view_bindings": {"CURRENT_REALITY": {"result_id": "CUR-2"}}},
        }
        self.assertEqual(qs.current_iceberg_consumer_result_ids(docs, {"SYN-OLD"}), {"CUR-2"})
        self.assertEqual(qs.current_iceberg_consumer_result_ids(docs, set()), {"CUR-1", "CUR-2"})

    def iceberg_model_with_historical_current_view(
        self, *, malformed_current: bool = False
    ) -> CompleteIcebergModel:
        model = CompleteIcebergModel(self.base / "history-model")
        current_id = "VIEW-CURRENT-RESULT"
        old_id = "VIEW-CURRENT-OLD"
        old_view = copy.deepcopy(model.docs[current_id]["iceberg_view"])
        old_view["view_id"] = "ICE-CURRENT-000"
        old_view["revision"] = "1"
        model.add_result(
            old_id,
            "OPERATOR",
            "OP-PROJECTION",
            "/root/OP-PROJECTION",
            "operators/VIEW-CURRENT-OLD.yaml",
            {
                "iceberg_view": old_view,
                "semantic_edges": [{"id": "MALFORMED-HISTORICAL-EDGE"}],
            },
        )
        current = model.docs[current_id]
        current["supersedes"] = old_id
        current["iceberg_view"]["revision"] = "2"
        current["iceberg_view"]["supersedes_view_id"] = "ICE-CURRENT-000"
        current["iceberg_view"]["source_bindings"].append({
            "source_kind": "ACCEPTED_RESULT",
            "source_id": old_id,
            "source_sha256": model.results[old_id]["content_hash"],
            "source_path": model.results[old_id]["path"],
        })
        model.projection["CURRENT_REALITY"]["revision"] = "2"
        if malformed_current:
            current["semantic_edges"] = [{"id": "MALFORMED-CURRENT-EDGE"}]
        _refresh_hash_bindings(model)
        return model

    def test_malformed_historical_edge_is_ignored_after_valid_successor(self) -> None:
        model = self.iceberg_model_with_historical_current_view()
        self.assertEqual(model.validate("core"), [])

    def test_same_malformed_edge_on_current_head_still_fails(self) -> None:
        model = self.iceberg_model_with_historical_current_view(
            malformed_current=True
        )
        errors = model.validate("core")
        self.assertIn("iceberg_edge:index-0:contract", errors)
        self.assertIn(
            "semantic_edge:None:None->None:None:contract", errors
        )

    def test_nonview_synthesis_semantic_edge_is_never_filtered(self) -> None:
        model = self.iceberg_model_with_historical_current_view()
        model.docs["SYNTHESIS-001"]["semantic_edges"][0]["from_id"] = "H-MISSING"
        _refresh_hash_bindings(model)
        errors = model.validate("core")
        self.assertIn("iceberg_edge:EDGE-AND-1:from_binding", errors)
        self.assertIn(
            "semantic_edge:H:H-MISSING->G:VECTOR-001:missing_from", errors
        )


if __name__ == "__main__":
    unittest.main()
