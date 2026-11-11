"""Public C02 controls over one connected temporary run."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_architecture_integration import attach_architecture
from test_iceberg_contracts import CompleteIcebergModel, _replace_exact
from test_model_contracts import write
import test_qs
from test_qs import put, qs


class ConnectedRun:
    """Reuse the structural donor; selected PLAN and SYNTHESIS commit through CLI."""

    def __init__(self, case, *, incomplete_goal=False):
        self.case = case
        self.tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "work"
        donor = CompleteIcebergModel(self.workspace)
        self.run = donor.run
        original = self.run / "input/original.md"
        original.write_text("# C02 source\n", encoding="utf-8")
        snapshot = self.run / "input/snapshot.yaml"
        write(snapshot, {
            "schema_version": "question-space-v4", "run_id": self.run.name,
            "bank_sha256": qs.sha((self.run / "input/bank.yaml").read_bytes()),
            "requirements_sha256": qs.sha(qs.canon_yaml(donor.bank["requirements"])),
            "original_bank": {"provided": "true", "path": "input/original.md", "sha256": qs.sha(original.read_bytes())},
            "methodology_features": ["DOUBLE_ICEBERG_V1", "ARCHITECTURE_GRAPH_V1"],
        })
        digest = qs.sha(snapshot.read_bytes())
        self.init = {"input_snapshot": digest}
        original_snapshot = donor.snapshot
        original_vector_hash = donor.results["VECTOR-001"]["content_hash"]
        approval_path = self.run / "input/vector-approval.yaml"
        original_approval_hash = qs.sha(approval_path.read_bytes())
        donor.snapshot = digest
        donor.docs["VECTOR-001"].update({"G": "a bounded full goal", "NOT_G": ["a subcase alone"]})
        for identifier, document in donor.docs.items():
            donor.docs[identifier] = _replace_exact(document, original_snapshot, digest)
            donor.results[identifier]["input_snapshot"] = digest
            donor.sync(identifier)
        vector_hash = donor.results["VECTOR-001"]["content_hash"]
        approval = qs.load_yaml(approval_path)
        approval.update({"input_snapshot": digest, "vector_sha256": vector_hash})
        approval_hash = write(approval_path, approval)
        for identifier, document in donor.docs.items():
            document = _replace_exact(document, original_vector_hash, vector_hash)
            donor.docs[identifier] = _replace_exact(document, original_approval_hash, approval_hash)
            donor.sync(identifier)
        outcomes = donor.docs["SOURCE-OUTCOMES-001"]
        for identifier, question in (("DISCOVERED-Q", "Q-NEW"), ("RETIRED-Q", "Q-RETIRE")):
            outcomes["records"].append({
                "id": identifier, "record_kind": "DERIVED_Q", "q_id": question,
                "parent_source_ids": ["Q-001"], "materiality": "private derived fixture text",
            })
        donor.sync("SOURCE-OUTCOMES-001")
        self.seed_outcomes = deepcopy(outcomes)
        donor.docs["D-001"]["grounds"] = ["E-001"]
        donor.docs["D-001"]["alternatives"] = ["retain existing branch", "switch bounded mechanism"]
        donor.docs["D-001"]["reconsideration_trigger"] = "the independent discriminator rejects the selected cause"
        donor.sync("D-001")
        donor.docs["E-001"].update({"scope": "fixture observation under condition A",
                                    "observation": "supports the current bounded cause"})
        donor.sync("E-001")
        donor.add_result("E-COMPETING", "EVIDENCE", "P-06", "/root/competing", "evidence/E-COMPETING.yaml", {
            "knowledge_status": "OBSERVED", "source_refs": ["input/snapshot.yaml"],
            "scope": "independent fixture observation under condition B",
            "observation": "supports the competing bounded cause", "changed": "UNCHANGED",
        })
        donor.add_review("REVIEW-COMPETING", 7, "E-COMPETING", "SUBJECT", "COMPETING")
        donor.docs["REVIEW-D-SYSTEM"]["objections"] = ["test the alternative before switching"]
        donor.sync("REVIEW-D-SYSTEM")
        donor.docs["SYNTHESIS-001"]["semantic_edges"].extend([
            donor.edge("EDGE-C02-Q", "ENABLES", "Q", "Q-NEW", "D", "D-001"),
            donor.edge("EDGE-C02-E", "ENABLES", "E", "E-001", "D", "D-001",
                       distinguishing_check_id="C-000"),
            donor.edge("EDGE-C02-COMPETING", "ENABLES", "E", "E-COMPETING", "Q", "Q-NEW"),
        ])
        donor.docs["SYNTHESIS-001"]["execution_edges"].extend([
            {"from_type": "E", "from_id": "E-COMPETING", "to_type": "D", "to_id": "D-001"},
            {"from_type": "E", "from_id": "E-COMPETING", "to_type": "H", "to_id": "H-001"},
        ])
        for stage in ("S-000", "S-001"):
            for contract in donor.docs[stage]["inputs_contracts"]["inputs"]:
                if contract["input_kind"] == "EXISTING":
                    contract["accepted_hash"] = donor.results[contract["source_id"]]["content_hash"]
        attach_architecture(donor)
        if incomplete_goal:
            graph = donor.docs["SYNTHESIS-001"]["architecture_graph"]
            kinds = {node["id"]: node["kind"] for node in graph["nodes"]}
            graph["edges"] = [edge for edge in graph["edges"] if not (
                edge["kind"] == "CONTRIBUTES_TO" and kinds[edge["from_id"]] == "CAPABILITY"
                and kinds[edge["to_id"]] == "G")]
            case.assertTrue(any(edge["kind"] == "CONTRIBUTES_TO" and kinds[edge["from_id"]] == "C"
                                 and kinds[edge["to_id"]] == "G" for edge in graph["edges"]))
            surviving = {edge["id"] for edge in graph["edges"]} | set(kinds)
            graph["implementation_map"] = [row for row in graph["implementation_map"]
                                           if row["target_id"] in surviving]
        case.assertEqual(qs.sha(snapshot.read_bytes()), digest)

        self.current = self.run / "state/current.yaml"
        self.index = self.run / "state/assignments.yaml"
        self.current.parent.mkdir(exist_ok=True)
        head = {"schema_version": "question-space-v4", "run_id": self.run.name, "input_snapshot": digest}
        put(self.current, {**head, "revision": "1", "status": "INITIALIZED"})
        selected_ids = {"SYNTHESIS-001", "S-000", "S-001"}
        put(self.index, {**head, "registered": {}, "attempts": {}, "current_attempts": {},
                         "results": {rid: entry for rid, entry in donor.results.items() if rid not in selected_ids}})
        put(self.run / "state/events.yaml", {**head, "events": []})
        put(self.run / "state/launch-intents.yaml", {**head, "records": {}})
        self.seed_stages = {rid: deepcopy(donor.docs[rid]) for rid in ("S-000", "S-001")}
        self.seed_synthesis = deepcopy(donor.docs["SYNTHESIS-001"])
        for identifier in selected_ids:
            (self.run / donor.results[identifier]["path"]).unlink()
        self.cli("recover", "--bootstrap-legacy")
        if incomplete_goal:
            result = self.publish(self.seed_synthesis, "synthesis/SYNTHESIS-001.yaml", ok=False)
            case.assertIn("no complete functional mechanism path", result["error"])
            case.assertFalse((self.run / "synthesis/SYNTHESIS-001.yaml").exists())
            return
        self.publish(self.seed_synthesis, "synthesis/SYNTHESIS-001.yaml")
        reference = self.seed_stages["S-000"]["architecture_ref"]
        self.reference = {**reference, "synthesis_sha256": qs.sha((self.run / "synthesis/SYNTHESIS-001.yaml").read_bytes())}
        self.stage_ids = self.publish_plan("1")
        self.selection = self.selection_request()
        self.selection_receipt = self.cli("select-plan", "--selection", str(self.selection))
        self.reader = self.make_reader("A-GRAPH", ["Q-001", "Q-NEW", "Q-RETIRE", "Q-FOREIGN"], ["R-001"],
                                       ["D-001"], ["E-001", "E-COMPETING"],
                                       [*self.stage_ids, "H-001", "REVIEW-H-001", "REVIEW-COMPETING",
                                        "REVIEW-D-SUBJECT", "REVIEW-D-SYSTEM"])
        self.unaffected = self.make_reader("A-B", ["Q-001"], ["R-001"], [], [], [])

    def cli(self, operation, *arguments, ok=True):
        return test_qs.QsTest.call(
            self.case, operation, "--root", str(self.workspace), "--run-id", self.run.name, *arguments, ok=ok
        )

    def publish(self, document, output, *, ok=True):
        rid = document["id"]
        assignment = {
            "assignment_id": "ASG-" + rid, "attempt_id": "ATT-1", "role_id": document["author_role"],
            "agent_id": document["agent_id"], "input_snapshot": self.init["input_snapshot"],
            "artifact_type": document["artifact_type"], "output_path": output,
            "dependency_hashes": [], "result_id": rid,
        }
        if document["artifact_type"] == "PLAN":
            assignment["selection_authority"] = "ROOT"
        body = {**document, "assignment_id": assignment["assignment_id"], "attempt_id": "ATT-1"}
        assignment_path = self.base / (rid + ".assignment.yaml")
        result_path = self.base / (rid + ".result.yaml")
        put(assignment_path, assignment)
        put(result_path, body)
        self.cli("assign", "--assignment", str(assignment_path))
        return self.cli("commit", "--assignment", str(assignment_path), "--result", str(result_path), ok=ok)

    def publish_plan(self, version, *, invalid_local=False, invalid_obligations=False, branch_evidence=None):
        suffix = "" if version == "1" else "-R" + version
        documents = {}
        for logical, seed in self.seed_stages.items():
            rid = logical + suffix
            stage = deepcopy(seed)
            stage.update({"id": rid, "revision": version, "plan_version": version, "architecture_ref": self.reference})
            stage["identity_result"]["plan_version"] = version
            if suffix:
                stage["supersedes"] = logical if version == "2" else logical + "-R" + str(int(version) - 1)
            documents[logical] = stage
        root = documents["S-000"]
        root["local_execution_graph"]["stage_dag"] = [
            {"stage_id": logical, "result_id": stage["id"], "path": "plan/" + stage["id"] + ".yaml",
             "task_refs": [task["id"] for task in stage["tasks"]],
             "check_refs": [check["id"] for check in stage["acceptance_exit"]],
             **{key: stage["local_execution_graph"][key] for key in ("entry", "exit", "predecessors", "next_stages")}}
            for logical, stage in documents.items()
        ]
        if branch_evidence:
            root["inputs_contracts"]["outputs"][0].update({
                "availability": "SELECTED_BRANCH", "branch_selection_status": "SELECTED",
                "branch_selection_evidence_id": branch_evidence,
            })
        if invalid_local:
            root["local_execution_graph"]["edges"] = []
            return self.publish(root, "plan/" + root["id"] + ".yaml", ok=False)
        if invalid_obligations:
            # Keep every existing graph edge and add one canonical mandatory
            # check input with no corresponding edge. OR evidence cannot stand
            # in for the omitted AND input.
            check = root["acceptance_exit"][0]
            check["inputs"].append(next(
                reference for stage in documents.values() for task in stage["tasks"]
                for reference in task["inputs"] if reference not in check["inputs"]
            ))
            root["inputs_contracts"]["outputs"][0].update({
                "availability": "SELECTED_BRANCH", "branch_selection_status": "SELECTED",
                "branch_selection_evidence_id": "E-001",
            })
        for stage in documents.values():
            self.publish(stage, "plan/" + stage["id"] + ".yaml")
        return [stage["id"] for stage in documents.values()]

    def selection_request(self):
        state = qs.load_yaml(self.index)
        current = qs.load_yaml(self.current)
        rows = []
        for logical, rid in zip(("S-000", "S-001"), self.stage_ids, strict=True):
            result = state["results"][rid]
            rows.append({"logical_stage_id": logical, "result_id": rid, "sha256": result["content_hash"],
                         "assignment_id": result["assignment_id"], "attempt_id": result["attempt_id"]})
        root = rows[0]
        authority = state["registered"][root["assignment_id"] + "\0" + root["attempt_id"]]
        version = qs.load_yaml(self.run / state["results"][root["result_id"]]["path"])["plan_version"]
        request = self.base / ("select-" + version + ".yaml")
        put(request, {
            "actor": "ROOT", "authority_sha256": authority["assignment_sha256"],
            "run_id": self.run.name, "input_snapshot": self.init["input_snapshot"],
            "expected_revision": current["revision"], "expected_head_sha256": qs.sha(self.current.read_bytes()),
            "architecture_ref": self.reference, "plan_version": version,
            "current_plan_graph": {"plan_version": version, "stages": rows},
        })
        return request

    def make_reader(self, identifier, questions, requirements, decisions, evidence, dependencies):
        state = qs.load_yaml(self.index)
        assignment = {
            "assignment_id": identifier, "attempt_id": "ATT-1", "role_id": "P-01", "agent_id": "/root/reader",
            "input_snapshot": self.init["input_snapshot"], "artifact_type": "PRIMARY",
            "output_path": "positions/" + identifier + ".yaml", "result_id": "RESULT-" + identifier,
            "goal_result_id": "VECTOR-001", "dependency_result_ids": dependencies,
            "dependency_hashes": [state["results"][rid]["content_hash"] for rid in dependencies],
            "assigned_ids": {"questions": questions, "requirements": requirements, "decisions": decisions, "evidence": evidence},
        }
        source = self.base / (identifier + ".yaml")
        put(source, assignment)
        self.cli("assign", "--assignment", str(source))
        return source

    def query(self, duty, ref, *, assignment=None, ok=True):
        return self.cli("graph-query", "--duty", duty, "--ref", ref,
                        "--assignment", str(assignment or self.reader), ok=ok)


class GraphConsumersTest(unittest.TestCase):
    def test_full_goal_link_required_at_ordinary_synthesis_intake(self):
        ConnectedRun(self, incomplete_goal=True)

    def test_withdrawn_selected_or_requires_explicit_reviewed_replacement(self):
        fixture = ConnectedRun(self)

        def add_branch(suffix):
            evidence_id, review_id = "E-BRANCH-" + suffix, "REVIEW-BRANCH-" + suffix
            evidence = {
                "schema_version": "question-space-v4", "id": evidence_id, "run_id": fixture.run.name,
                "input_snapshot": fixture.init["input_snapshot"], "author_role": "P-04",
                "agent_id": "/root/evidence", "knowledge_status": "OBSERVED", "workflow_status": "ACCEPTED",
                "source_refs": [], "related_ids": [], "revision": "1", "created_at": "2026-10-21T00:00:00Z",
                "updated_at": "2026-10-21T00:00:00Z", "artifact_type": "EVIDENCE", "changed": "CHANGED",
                "branch_selection_review_id": review_id,
            }
            proof = fixture.publish(evidence, "evidence/" + evidence_id + ".yaml")
            output = {**fixture.seed_stages["S-000"]["inputs_contracts"]["outputs"][0],
                      "availability": "SELECTED_BRANCH", "branch_selection_status": "SELECTED",
                      "branch_selection_evidence_id": evidence_id}
            review = {key: value for key, value in evidence.items()
                      if key not in {"changed", "branch_selection_review_id"}}
            review.update({
                "id": review_id, "author_role": "P-05", "agent_id": "/root/reviewer",
                "artifact_type": "REVIEW", "outcome": "PASS", "target_result_id": evidence_id,
                "target_hash": proof["content_hash"],
                "branch_selection_scope": {
                    "output_id": output["id"], "contract_version": output["version"],
                    "producer_task_id": output["producer_task_id"], "acceptance_check_id": output["acceptance_check_id"],
                    "contract_sha256": qs.sha(qs.canon_yaml(output)),
                },
            })
            fixture.publish(review, "reviews/" + review_id + ".yaml")
            return evidence_id

        branch_a, branch_b = add_branch("A"), add_branch("B")
        fixture.stage_ids = fixture.publish_plan("2", branch_evidence=branch_a)
        fixture.cli("select-plan", "--selection", str(fixture.selection_request()))
        unaffected = fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected)
        resumed = fixture.cli("resume", "--changed", branch_a)
        self.assertIn(branch_a, resumed["stale_result_ids"])
        self.assertNotIn(branch_b, resumed["stale_result_ids"])
        held = qs.load_yaml(fixture.current)
        self.assertEqual(held["current_plan_projection"]["plan_version"], "2")
        gate = fixture.cli("validate", "--phase", "core", ok=False)
        self.assertTrue(any("selected branch requires current independently reviewed E" in item
                            for item in gate["errors"]), gate["errors"])
        self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected), unaffected)
        affected = fixture.make_reader("A-BRANCH", ["Q-001"], ["R-001"], ["D-001"], ["E-001"], fixture.stage_ids)
        result = fixture.query("EXECUTION", "T-000", assignment=affected)
        self.assertEqual(result["actionable"]["status"], "HELD")
        fixture.stage_ids = fixture.publish_plan("3", branch_evidence=branch_a)
        before = fixture.current.read_bytes()
        rejected = fixture.cli("select-plan", "--selection", str(fixture.selection_request()), ok=False)
        self.assertIn("selected branch requires current independently reviewed E", rejected["error"])
        self.assertEqual(fixture.current.read_bytes(), before)
        fixture.stage_ids = fixture.publish_plan("4", branch_evidence=branch_b)
        request = fixture.selection_request()
        before_replacement = fixture.current.read_bytes()
        accepted = fixture.cli("select-plan", "--selection", str(request))
        selected = fixture.current.read_bytes()
        self.assertEqual(qs.load_yaml(fixture.current)["current_plan_projection"]["plan_version"], "4")
        self.assertEqual(fixture.cli("select-plan", "--selection", str(request)), accepted)
        self.assertEqual(fixture.current.read_bytes(), selected)
        fixture.current.write_bytes(before_replacement)
        continued = fixture.cli("resume")
        self.assertEqual(continued["architecture_ref"], fixture.reference)
        self.assertEqual(continued["current_plan"]["version"], "4")
        confirmed = continued["last_confirmed_transition"]
        self.assertEqual(confirmed["operation"], "PLAN_PROJECTION_SELECT")
        self.assertEqual(qs.sha((fixture.run / confirmed["path"]).read_bytes()), confirmed["sha256"])
        self.assertNotIn("ASG-S-000-R4", {aid for group in continued["ready_groups"] for aid in group["assignment_ids"]})
        self.assertEqual(fixture.current.read_bytes(), selected)

    def test_connected_public_consumers(self):
        fixture = ConnectedRun(self)
        self.assertEqual(fixture.selection_receipt["status"], "PLAN_SELECTED")
        cases = {
            "KNOWLEDGE": ("Q-NEW", "Q"), "DECISIONS": ("D-001", "D"),
            "PROBLEMS": ("R-001", "R"), "COVERAGE": ("Q-001", "Q"),
            "EVIDENCE": ("E-001", "E"), "ALTERNATIVES": ("D-001", "D"),
            "EXECUTION": ("T-000", "T"), "EVOLUTION": ("E-001", "E"),
        }
        observations = {}
        for duty, (reference, kind) in cases.items():
            with self.subTest(duty=duty):
                value = fixture.query(duty, reference)
                self.assertEqual((value["status"], value["ref"], value["ref_kind"]), ("GRAPH_QUERY", reference, kind))
                self.assertEqual(value["architecture_ref"], fixture.reference)
                self.assertNotIn("private derived fixture text", json.dumps(value))
                self.assertEqual(value["actionable"]["status"], "ADDRESSED")
                for route in value["actionable"]["corrective"]:
                    for source in [route["task"], route["check"], *(edge["source"] for edge in route["path"])]:
                        raw = (fixture.run / source["path"]).read_bytes()
                        self.assertEqual(qs.sha(raw), source["sha256"])
                        target = qs.load_yaml(fixture.run / source["path"])
                        for part in source["pointer"].split("/")[1:]:
                            target = target[int(part)] if isinstance(target, list) else target[part]
                        self.assertIsNotNone(target)
                observations[duty] = value
        self.assertTrue(any(row["q_id"] == "Q-NEW" for row in observations["KNOWLEDGE"]["coverage"]))
        self.assertEqual({row["evidence_id"] for row in observations["KNOWLEDGE"]["actionable"]["qualified_reuse"]},
                         {"E-COMPETING"})
        self.assertIn("E-001", observations["KNOWLEDGE"]["artifacts"])
        for reviewer in ("REVIEW-D-SUBJECT", "REVIEW-D-SYSTEM"):
            self.assertIn(reviewer, observations["DECISIONS"]["artifacts"])
        self.assertIn("/objections", observations["DECISIONS"]["artifacts"]["REVIEW-D-SYSTEM"]["field_paths"])
        self.assertIn("/reconsideration_trigger", observations["DECISIONS"]["artifacts"]["D-001"]["field_paths"])
        problem = fixture.query("PROBLEMS", "H-001")
        self.assertIn("/finite_check", problem["artifacts"]["H-001"]["field_paths"])
        self.assertIn("REVIEW-H-001", problem["artifacts"])
        self.assertTrue(problem["actionable"]["corrective"])
        alternate_context = fixture.make_reader("A-ADVICE", ["Q-001"], ["R-001"], ["D-001"], [],
                                                ["REVIEW-D-SYSTEM"])
        denied = fixture.base / "denied-advice.yaml"
        rejected = fixture.cli("context", "--assignment", str(alternate_context), "--expand", "E-COMPETING",
                               "--output", str(denied), ok=False)
        self.assertEqual(rejected["error"], "context_expansion_not_linked")
        self.assertFalse(denied.exists())
        allowed = fixture.base / "advice.yaml"
        fixture.cli("context", "--assignment", str(alternate_context), "--expand", "REVIEW-D-SYSTEM",
                    "--output", str(allowed))
        self.assertIn("test the alternative before switching", allowed.read_text())
        self.assertNotIn("supports the competing bounded cause", allowed.read_text())
        self.assertIn("/grounds", observations["DECISIONS"]["artifacts"]["D-001"]["field_paths"])
        self.assertTrue(any(edge["from_id"] == "T-000" and edge["to_id"] == "C-000"
                            for edge in observations["EXECUTION"]["edges"]))
        execution_edges = observations["EXECUTION"]["edges"]
        produced = {edge["to_id"] for edge in execution_edges
                    if edge["relation"] == "PRODUCES" and edge["from_id"] == "T-000"}
        consumed = {edge["from_id"] for edge in execution_edges
                    if edge["relation"] == "CONSUMES" and edge["to_id"] in {"T-001", "C-000", "C-001"}}
        data_id = next(iter(sorted(produced & consumed)), None)
        self.assertEqual(data_id, "DATA-OUTPUT-T-000", execution_edges)
        self.assertEqual(fixture.query("EXECUTION", data_id)["ref_kind"], "DATA")
        self.assertTrue(any(edge["relation"] == "CONTRIBUTES_TO" and edge["to_id"] == "VECTOR-001"
                            for edge in execution_edges))
        self.assertTrue(any(edge["relation"] == "CONSUMES" and edge["from_id"] == data_id
                            and edge.get("contract_id") for edge in execution_edges))
        binding = observations["EXECUTION"]["execution_bindings"]["C-000"]
        self.assertEqual(binding, {"kind": "C", "plan_result_id": "S-000"})
        stage = observations["EXECUTION"]["artifacts"][binding["plan_result_id"]]
        self.assertEqual(stage["sha256"], qs.sha((fixture.run / stage["path"]).read_bytes()))
        self.assertIn("not_assigned", fixture.query("EXECUTION", "DATA-FOREIGN", ok=False)["error"])
        self.assertEqual(observations["COVERAGE"]["coverage"][0]["answer_status"], "ANSWERED")
        goal = fixture.query("COVERAGE", "VECTOR-001")
        self.assertIn("/NOT_G", goal["artifacts"]["VECTOR-001"]["field_paths"])
        backward = fixture.query("EXECUTION", "C-000")
        self.assertTrue(any(route["task_id"] == "T-000" for route in backward["actionable"]["corrective"]))
        evidence = observations["EVIDENCE"]["artifacts"]["E-001"]
        self.assertEqual(evidence["path"], "evidence/E-001.yaml")
        self.assertEqual(evidence["sha256"], qs.sha((fixture.run / evidence["path"]).read_bytes()))
        self.assertTrue(any(edge.get("semantic_relation") == "NECESSARY_FOR" and edge["from_id"] == "R-001"
                            for edge in observations["PROBLEMS"]["edges"]))
        self.assertIn("/alternatives", observations["ALTERNATIVES"]["artifacts"]["D-001"]["field_paths"])
        self.assertIn("command_cut", observations["EVOLUTION"])
        self.assertIn("not_assigned", fixture.query("COVERAGE", "Q-FOREIGN", ok=False)["error"])

        self.check_cache_parity(fixture, observations["DECISIONS"])
        self.check_external_cache(fixture)
        old_head = fixture.current.read_bytes()
        invalid = fixture.publish_plan("2", invalid_local=True)
        self.assertIn("local_execution_path", invalid["error"])
        self.assertEqual(fixture.current.read_bytes(), old_head)

        fixture.stage_ids = fixture.publish_plan("2")
        successor = fixture.selection_request()
        envelope = qs.load_yaml(successor)
        before = fixture.current.read_bytes()
        bad = fixture.base / "selection-negative.yaml"
        for field, value in (("expected_revision", "999"), ("authority_sha256", "0" * 64)):
            with self.subTest(selection=field):
                put(bad, {**envelope, field: value})
                fixture.cli("select-plan", "--selection", str(bad), ok=False)
                self.assertEqual(fixture.current.read_bytes(), before)
        reduced = deepcopy(envelope)
        reduced["current_plan_graph"]["stages"].pop()
        put(bad, reduced)
        fixture.cli("select-plan", "--selection", str(bad), ok=False)
        self.assertEqual(fixture.current.read_bytes(), before)
        newer = fixture.cli("select-plan", "--selection", str(successor))
        self.assertNotEqual(newer["revision"], fixture.selection_receipt["revision"])
        after = fixture.current.read_bytes()
        self.assertEqual(fixture.cli("select-plan", "--selection", str(fixture.selection)), fixture.selection_receipt)
        self.assertEqual(fixture.current.read_bytes(), after)

        selected_v2 = fixture.make_reader("A-GRAPH-V2", ["Q-001"], ["R-001"],
                                         ["D-001"], ["E-001"], fixture.stage_ids)
        self.assertEqual(fixture.query("EXECUTION", "T-000", assignment=selected_v2)["ref"], "T-000")
        self.assertIn("not_assigned", fixture.query("EXECUTION", "T-000", ok=False)["error"])
        self.assertEqual(fixture.query("COVERAGE", "Q-RETIRE")["ref"], "Q-RETIRE")
        outcomes = deepcopy(fixture.seed_outcomes)
        outcomes.update({"id": "SOURCE-OUTCOMES-002", "revision": "2", "supersedes": "SOURCE-OUTCOMES-001"})
        outcomes["records"] = [row for row in outcomes["records"] if row.get("q_id") != "Q-RETIRE"]
        fixture.publish(outcomes, "answers/source-outcomes/SOURCE-OUTCOMES-002.yaml")
        self.assertIn("not_assigned", fixture.query("COVERAGE", "Q-RETIRE", ok=False)["error"])
        self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected)["ref"], "Q-001")
        self.check_frontier(fixture, selected_v2)
        before_gate = fixture.cli("validate", "--phase", "core", ok=False)
        self.assertFalse(any(error.startswith("stale_result_ids:") and "H-001" in error
                             for error in before_gate["errors"]))
        disjoint = fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected)
        self.assertEqual(disjoint["actionable"]["status"], "UNRESOLVED")
        resumed = fixture.cli("resume", "--changed", "E-COMPETING")
        self.assertIn("H-001", resumed["stale_result_ids"])
        after_gate = fixture.cli("validate", "--phase", "core", ok=False)
        self.assertTrue(any(error.startswith("stale_result_ids:") and "H-001" in error
                            for error in after_gate["errors"]), after_gate["errors"])
        self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected), disjoint)

        fixture.stage_ids = fixture.publish_plan("3", invalid_obligations=True)
        invalid_graph = fixture.selection_request()
        current = fixture.current.read_bytes()
        rejected = fixture.cli("select-plan", "--selection", str(invalid_graph), ok=False)
        self.assertIn("omitted mandatory plan inputs", rejected["error"])
        self.assertIn("selected branch requires current independently reviewed E", rejected["error"])
        self.assertEqual(fixture.current.read_bytes(), current)

    def check_external_cache(self, fixture):
        from tests.test_github_evidence import external_fields

        identifier = "E-CACHED"
        external = external_fields(identifier)
        material = external["implementation"]["material_refs"][0]
        raw = b"external source bytes"
        relative = "evidence/raw/cached.bin"
        digest = qs.sha(raw)
        material.update({"content_sha256": digest, "cached_artifact": {"path": relative, "sha256": digest}})
        evidence = {
            **external, "schema_version": "question-space-v4", "id": identifier, "run_id": fixture.run.name,
            "input_snapshot": fixture.init["input_snapshot"], "author_role": "P-04",
            "agent_id": "/root/evidence", "knowledge_status": "OBSERVED", "workflow_status": "ACCEPTED",
            "source_refs": [], "related_ids": ["Q-001", "D-001"], "revision": "1",
            "created_at": "2026-10-21T00:00:00Z", "updated_at": "2026-10-21T00:00:00Z",
            "artifact_type": "EVIDENCE", "changed": "UNCHANGED",
        }
        output = "evidence/E-CACHED.yaml"
        rejected = fixture.publish(evidence, output, ok=False)
        self.assertIn("cached_artifact:source_unavailable", rejected["error"])
        self.assertFalse((fixture.run / output).exists())
        cache = fixture.run / relative
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(raw)
        fixture.cli("commit", "--assignment", str(fixture.base / (identifier + ".assignment.yaml")),
                    "--result", str(fixture.base / (identifier + ".result.yaml")))
        reader = fixture.make_reader("A-CACHED", ["Q-001"], ["R-001"], [], [identifier], [])
        accepted = fixture.query("EVIDENCE", identifier, assignment=reader)
        self.assertEqual(accepted["artifacts"][identifier]["path"], output)
        stable = fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected)
        cache.write_bytes(b"X" + raw[1:])
        rejected = fixture.query("EVIDENCE", identifier, assignment=reader, ok=False)
        self.assertIn("cached_artifact:source_drift", rejected["error"])
        self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected), stable)
        cache.write_bytes(raw)

    def check_frontier(self, fixture, affected_assignment):
        stable = fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected)
        evidence = {
            "schema_version": "question-space-v4", "id": "E-FRONTIER", "run_id": fixture.run.name,
            "input_snapshot": fixture.init["input_snapshot"], "author_role": "P-04",
            "agent_id": "/root/evidence", "knowledge_status": "OBSERVED", "workflow_status": "ACCEPTED",
            "source_refs": [], "related_ids": [], "revision": "1", "created_at": "2026-10-21T00:00:00Z",
            "updated_at": "2026-10-21T00:00:00Z", "artifact_type": "EVIDENCE", "changed": "CHANGED",
            "command_intake_scope": ["D-001"], "applicability_scope_version": "SCOPE-V1",
        }
        e_receipt = fixture.publish(evidence, "evidence/E-FRONTIER.yaml")

        def command(operation, key, authority, digest, actor, *, ok=True, evidence_id="E-FRONTIER"):
            cut = fixture.cli("command-cut")
            path = fixture.base / (key + ".yaml")
            put(path, {
                "key": key, "actor": actor, "authority_sha256": digest, "run_id": fixture.run.name,
                "input_snapshot": fixture.init["input_snapshot"], "expected_revision": cut["expected_revision"],
                "authority_result_id": authority, "evidence_result_id": evidence_id,
            })
            return fixture.cli(operation, "--request", str(path), ok=ok)

        pending = command("evidence-intake", "intake-a", "E-FRONTIER", e_receipt["content_hash"], "/root/evidence")
        self.assertEqual(pending["status"], "EVIDENCE_PENDING")
        self.assertIn("evidence_pending", fixture.query("DECISIONS", "D-001", assignment=affected_assignment, ok=False)["error"])
        self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected), stable)
        held = fixture.base / "held-knowledge.yaml"
        denied = fixture.cli("context", "--assignment", str(affected_assignment), "--output", str(held), ok=False)
        self.assertEqual(denied["error"], "assignment_evidence_pending")
        self.assertFalse(held.exists())
        admitted = fixture.base / "disjoint-knowledge.yaml"
        fixture.cli("context", "--assignment", str(fixture.unaffected), "--output", str(admitted))
        self.assertTrue(admitted.is_file())
        first = fixture.run / "transactions/commands/00000001.yaml"
        review = {
            **{key: value for key, value in evidence.items()
               if key not in {"changed", "command_intake_scope", "applicability_scope_version"}},
            "id": "REVIEW-FRONTIER", "artifact_type": "REVIEW", "author_role": "P-05", "agent_id": "/root/reviewer",
            "target_result_id": "E-FRONTIER", "target_hash": e_receipt["content_hash"], "outcome": "PASS",
            "evidence_disposition": "DISJOINT", "reviewed_affected_result_ids": ["D-001"],
            "applicability_frontier": {
                "scope_version": "SCOPE-V1", "cursor": "1", "cutoff": "1", "cut_sha256": qs.sha(first.read_bytes()),
                "reason": "same named result but disjoint observed input version", "consumers": ["D-001"], "lineage": {},
            },
        }
        foreign = deepcopy(review)
        foreign["id"] = "REVIEW-FOREIGN"
        foreign["applicability_frontier"]["scope_version"] = "SCOPE-FOREIGN"
        wrong = fixture.publish(foreign, "reviews/REVIEW-FOREIGN.yaml")
        blocked = command("evidence-disposition", "review-foreign", "REVIEW-FOREIGN",
                          wrong["content_hash"], "/root/reviewer", ok=False)
        self.assertEqual(blocked["error"], "evidence_frontier_authority")
        self.assertFalse((fixture.run / "transactions/commands/00000002.yaml").exists())
        review_receipt = fixture.publish(review, "reviews/REVIEW-FRONTIER.yaml")
        resolved = command("evidence-disposition", "review-a", "REVIEW-FRONTIER",
                           review_receipt["content_hash"], "/root/reviewer")
        self.assertEqual(resolved["disposition"], "DISJOINT")
        self.assertEqual(fixture.query("DECISIONS", "D-001", assignment=affected_assignment)["blockers"], [])
        last = fixture.run / "transactions/commands/00000002.yaml"
        raw = last.read_bytes()
        last.unlink()
        try:
            self.assertIn("evidence_pending", fixture.query("DECISIONS", "D-001", assignment=affected_assignment, ok=False)["error"])
            self.assertEqual(fixture.query("COVERAGE", "Q-001", assignment=fixture.unaffected), stable)
        finally:
            last.write_bytes(raw)

        evidence_b = {**evidence, "id": "E-FRONTIER-B", "command_intake_scope": ["E-CACHED"],
                      "applicability_scope_version": "SCOPE-B"}
        receipt_b = fixture.publish(evidence_b, "evidence/E-FRONTIER-B.yaml")
        command("evidence-intake", "intake-b", "E-FRONTIER-B", receipt_b["content_hash"],
                "/root/evidence", evidence_id="E-FRONTIER-B")
        third = fixture.run / "transactions/commands/00000003.yaml"
        review_b = {
            **review, "id": "REVIEW-FRONTIER-B", "target_result_id": "E-FRONTIER-B",
            "target_hash": receipt_b["content_hash"], "reviewed_affected_result_ids": ["E-CACHED"],
            "applicability_frontier": {
                **review["applicability_frontier"], "scope_version": "SCOPE-B",
                "cursor": "3", "cutoff": "3", "cut_sha256": qs.sha(third.read_bytes()),
                "consumers": ["E-CACHED"],
            },
        }
        review_receipt_b = fixture.publish(review_b, "reviews/REVIEW-FRONTIER-B.yaml")
        command("evidence-disposition", "review-b", "REVIEW-FRONTIER-B", review_receipt_b["content_hash"],
                "/root/reviewer", evidence_id="E-FRONTIER-B")
        reader_b = fixture.base / "A-CACHED.yaml"
        expected_b = fixture.query("EVIDENCE", "E-CACHED", assignment=reader_b)
        selected = fixture.current.read_bytes()
        try:
            projected = qs.load_yaml(fixture.current)
            frontier = projected["applicability_frontier"]
            later = frontier["E-FRONTIER-B"]
            self.assertEqual(later["position"], "4")
            frontier["E-FRONTIER"] = {**later, "affected_result_ids": ["D-001"]}
            put(fixture.current, projected)
            self.assertIn("evidence_pending", fixture.query("DECISIONS", "D-001",
                          assignment=affected_assignment, ok=False)["error"])
            self.assertEqual(fixture.query("EVIDENCE", "E-CACHED", assignment=reader_b), expected_b)
            self.assertEqual(last.read_bytes(), raw)
        finally:
            fixture.current.write_bytes(selected)

        evidence_new = {**evidence, "id": "E-FRONTIER-NEW", "applicability_scope_version": "SCOPE-NEW"}
        fresh = fixture.publish(evidence_new, "evidence/E-FRONTIER-NEW.yaml")
        command("evidence-intake", "intake-new", "E-FRONTIER-NEW", fresh["content_hash"],
                "/root/evidence", evidence_id="E-FRONTIER-NEW")
        fifth = fixture.run / "transactions/commands/00000005.yaml"
        lineage = {"question_or_alternative_id": "Q-NEW", "decision_id": "D-001", "discriminator": "C-000"}
        review_new = {
            **review, "id": "REVIEW-FRONTIER-NEW", "target_result_id": "E-FRONTIER-NEW",
            "target_hash": fresh["content_hash"], "evidence_disposition": "NEW",
            "applicability_frontier": {
                **review["applicability_frontier"], "scope_version": "SCOPE-NEW", "cursor": "5",
                "cutoff": "5", "cut_sha256": qs.sha(fifth.read_bytes()), "lineage": lineage,
            },
        }
        reviewed = fixture.publish(review_new, "reviews/REVIEW-FRONTIER-NEW.yaml")
        receipt = command("evidence-disposition", "review-new", "REVIEW-FRONTIER-NEW",
                          reviewed["content_hash"], "/root/reviewer", evidence_id="E-FRONTIER-NEW")
        self.assertEqual(receipt["frontier"]["proof"]["lineage"], lineage)
        covered = fixture.make_reader("A-NEW-COVERAGE", ["Q-NEW"], ["R-001"], [], ["E-FRONTIER-NEW"],
                                      ["REVIEW-FRONTIER-NEW"])
        proof = fixture.query("COVERAGE", "Q-NEW", assignment=covered)["new_lineage"][0]
        self.assertEqual(proof["source"]["sha256"], reviewed["content_hash"])
        self.assertEqual(proof["source"]["pointer"], "/applicability_frontier/lineage")
        self.assertIn("evidence_stale", fixture.query("DECISIONS", "D-001",
                      assignment=affected_assignment, ok=False)["error"])
        denied_new = fixture.base / "held-new.yaml"
        refused = fixture.cli("context", "--assignment", str(affected_assignment),
                              "--output", str(denied_new), ok=False)
        self.assertEqual(refused["error"], "assignment_evidence_stale")
        self.assertFalse(denied_new.exists())

    def check_cache_parity(self, fixture, cold):
        argv = ["graph-query", "--root", str(fixture.workspace), "--run-id", fixture.run.name,
                "--duty", "DECISIONS", "--ref", "D-001", "--assignment", str(fixture.reader)]

        def invoke():
            with patch.object(qs, "emit") as emitted:
                code = qs.main(argv)
            emitted.assert_called_once()
            return code, emitted.call_args.args[0]

        self.assertEqual(invoke(), (0, cold))
        self.assertEqual(invoke(), (0, cold))
        path = fixture.run / cold["artifacts"]["D-001"]["path"]
        raw = path.read_bytes()
        prior = path.stat()
        changed = raw.replace(b"bounded selected mechanism", b"bounded selected Mechanism", 1)
        self.assertNotEqual(changed, raw)
        self.assertEqual(len(changed), len(raw))
        try:
            path.write_bytes(changed)
            os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))
            code, result = invoke()
            self.assertNotEqual(code, 0)
            self.assertIn("immutable_result_tamper:D-001", result["error"])
        finally:
            path.write_bytes(raw)
            os.utime(path, ns=(prior.st_atime_ns, prior.st_mtime_ns))
        self.assertEqual(invoke(), (0, cold))
