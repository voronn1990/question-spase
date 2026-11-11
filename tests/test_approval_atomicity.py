"""Rejected approval candidates never become immutable run inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


CLI = Path(__file__).resolve().parents[1] / "scripts/qs.py"


class ApprovalAtomicityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "workspace"
        self.run = self.root / "runs/APPROVAL-TEST"
        goal = self.base / "goal.txt"
        goal.write_text("Preserve source archive and plan a searchable local inventory.\n")
        statement = self.base / "authority.txt"
        statement.write_text("Fixture approval for isolated structural evaluation only.\n")
        self.bank = self.base / "bank.yaml"
        self.put(self.bank, {"requirements": ["R-001"], "questions": [{"id": "Q-001", "text": "How can the source be preserved?", "source_offset": "1"}]})
        init = self.call("init", "--mode", "BANK_TO_PLAN", "--goal", goal, "--bank", self.bank, "--external-bank", self.bank, "--material", statement, "--test-fixture")
        snapshot = init["input_snapshot"]
        assignment = {"assignment_id": "A-VECTOR", "attempt_id": "ATT-1", "role_id": "OP-VECTOR", "agent_id": "/fixture/vector", "input_snapshot": snapshot, "artifact_type": "OPERATOR", "output_path": "operators/VECTOR.yaml", "dependency_hashes": [], "result_id": "VECTOR", "lifecycle_scope": "PREP", "prep_stage": "PREP-1_VECTOR"}
        vector = {
            "schema_version": "question-space-v4", "id": "VECTOR", "run_id": "APPROVAL-TEST", "input_snapshot": snapshot,
            "author_role": "OP-VECTOR", "agent_id": "/fixture/vector", "knowledge_status": "DERIVED", "workflow_status": "ACCEPTED",
            "source_refs": [], "related_ids": [], "revision": "1", "created_at": "2026-10-23T12:00:00Z", "updated_at": "2026-10-23T12:00:00Z",
            "assignment_id": "A-VECTOR", "attempt_id": "ATT-1", "artifact_type": "OPERATOR", "lifecycle_scope": "PREP", "prep_stage": "PREP-1_VECTOR",
            "G": "Preserve sources and plan a useful searchable inventory", "NOT_G": ["Source modification"], "value_recipient": "Archive owner",
            "scope": "Local plan only", "success_criteria": ["Source preservation is checkable"], "requirements": ["R-001"],
            "hard_constraints": ["Read-only sources"], "changeable_assumptions": ["Inventory mechanism"], "current_state": "Ready bank supplied",
            "resources": "Local fixtures", "authority": "Isolated fixture only", "owner_decisions": [{"id": "OD-1", "decision_needed": "Approve fixture scope", "why_owner_only": "Defines evaluation authority", "known_context": "No archive operations", "answer_effect": "Continue structural evaluation"}],
            "open_research_questions": ["Which inventory representation meets the goal?"],
        }
        ap = self.base / "assignment.yaml"; rp = self.base / "vector.yaml"
        self.put(ap, assignment); self.put(rp, vector)
        self.call("assign", "--assignment", ap)
        accepted = self.call("commit", "--assignment", ap, "--result", rp)
        self.approval = {"schema_version": "question-space-v4", "approval_id": "APPROVAL-1", "run_id": "APPROVAL-TEST", "input_snapshot": snapshot, "vector_result_id": "VECTOR", "vector_sha256": accepted["content_hash"], "vector_revision": "1", "decision": "APPROVE", "approval_mode": "SIMULATED_TEST", "approver_id": "ROOT-AS-SIMULATED_TEST_OWNER", "authority_basis": "Isolated structural fixture", "observation_provenance": "tests/test_approval_atomicity.py:1", "statement_path": "input/materials/001-authority.txt", "statement_sha256": hashlib.sha256(statement.read_bytes()).hexdigest(), "created_at": "2026-10-23T12:00:00Z", "fixture_only": "YES"}
        self.approval_path = self.base / "approval.yaml"
        self.put(self.approval_path, self.approval)
        self.target = self.run / "input/vector-approval.yaml"

    @staticmethod
    def put(path: Path, value: dict) -> None:
        path.write_text(yaml.safe_dump(value, sort_keys=True, default_style='"'))

    def call(self, command: str, *args: object, ok: bool = True) -> dict:
        cp = subprocess.run([sys.executable, "-B", str(CLI), command, "--root", str(self.root), "--run-id", "APPROVAL-TEST", *map(str, args)], capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        result = json.loads(cp.stdout or cp.stderr)
        if ok:
            self.assertEqual(cp.returncode, 0, result)
        else:
            self.assertNotEqual(cp.returncode, 0, result)
        return result

    def test_rejected_candidate_does_not_poison_run_and_valid_retry_is_idempotent(self) -> None:
        invalid = {**self.approval, "vector_sha256": "0" * 64}
        invalid_path = self.base / "invalid-approval.yaml"
        self.put(invalid_path, invalid)
        before = {str(p.relative_to(self.run)): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        rejected = self.call("approve-vector", "--approval", invalid_path, ok=False)
        self.assertIn("vector_sha256:mismatch", rejected["error"])
        self.assertFalse(self.target.exists())
        after = {str(p.relative_to(self.run)): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        approved = self.call("approve-vector", "--approval", self.approval_path)
        self.assertEqual(approved["status"], "VECTOR_APPROVED")
        self.assertEqual(self.target.read_bytes(), self.approval_path.read_bytes())
        stored = self.target.read_bytes()
        repeated = self.call("approve-vector", "--approval", self.approval_path)
        self.assertEqual(repeated["approval_sha256"], approved["approval_sha256"])
        self.assertEqual(self.target.read_bytes(), stored)
        self.assertEqual(self.call("recover")["status"], "RECOVERED")

    def test_preexisting_invalid_approval_cannot_be_silently_rewritten_or_accepted(self) -> None:
        invalid = {**self.approval, "vector_sha256": "0" * 64}
        self.put(self.target, invalid)
        exact_invalid = self.target.read_bytes()
        blocked = self.call("approve-vector", "--approval", self.approval_path, ok=False)
        self.assertEqual(blocked["error"], "approval_rewrite_forbidden")
        blocked_same = self.call("approve-vector", "--approval", self.target, ok=False)
        self.assertIn("vector_sha256:mismatch", blocked_same["error"])
        self.assertEqual(self.target.read_bytes(), exact_invalid)


if __name__ == "__main__":
    unittest.main()
