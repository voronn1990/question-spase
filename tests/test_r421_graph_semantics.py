"""R42-01/03/04/05: canonical branch state and conditional constructibility.

These fixtures describe planned architecture; a graph PASS proves no execution.
"""
from __future__ import annotations

import copy
import unittest

from test_architecture import fixture_context, graph_for_context
from architecture_validation import validate_architecture_graph


def add_edge(graph, kind, source, target, **fields):
    edge = {"id": "EDGE-R421-" + str(len(graph["edges"])), "kind": kind,
            "from_id": source, "to_id": target, "meaning": "explicit fixture dependency",
            "scope": "EXECUTION", "conditions": [], "check_ids": [], **fields}
    graph["edges"].append(edge)
    return edge


def optional_branch_context():
    context = fixture_context()
    context["tasks"]["T-OPTIONAL"] = {
        "id": "T-OPTIONAL", "execution_status": "NOT_APPLICABLE", "inputs": ["INPUT-001"],
        "outputs": ["artifact/optional.yaml"], "dependencies": [], "r_refs": ["R-001"],
        "branch_guard": "the observed branch selection excludes this task"}
    context["task_stage"]["T-OPTIONAL"] = "S-000"
    context["checks"]["C-OPTIONAL"] = {
        "id": "C-OPTIONAL", "task_id": "T-OPTIONAL", "inputs": ["INPUT-001"],
        "procedure": "check the selected branch against the initial input",
        "expected_observation": "the optional branch is not selected"}
    context["check_stage"]["C-OPTIONAL"] = "S-000"
    context["input_contracts"]["INPUT-001"]["consumer_ids"] += ["T-OPTIONAL", "C-OPTIONAL"]
    context["output_contracts"]["OUTPUT-OPTIONAL"] = {
        "id": "OUTPUT-OPTIONAL", "path": "artifact/optional.yaml", "producer_task_id": "T-OPTIONAL",
        "acceptance_check_id": "C-OPTIONAL", "consumer_ids": [], "contract": "ordered integer records",
        "availability": "UNSELECTED_BRANCH"}
    context["output_contract_stage"]["OUTPUT-OPTIONAL"] = "S-000"
    return context


def optional_branch_graph(context):
    graph = graph_for_context(context)
    # The optional mechanism is recorded, without asserting that it realizes the
    # active functional capability. Its C verifies branch choice from real input.
    graph["edges"] = [edge for edge in graph["edges"] if not (
        edge["kind"] == "REALIZES" and edge["from_id"] == "ALGORITHM-T-OPTIONAL")]
    seen = set()
    graph["edges"] = [edge for edge in graph["edges"] if not (
        (edge["kind"], edge["from_id"], edge["to_id"]) in seen
        or seen.add((edge["kind"], edge["from_id"], edge["to_id"])))]
    return graph


def external_context(status="UNAVAILABLE", task_status="BLOCKED"):
    context = fixture_context()
    context["tasks"]["T-001"]["execution_status"] = task_status
    context["input_contracts"]["INPUT-001"] = {
        "id": "INPUT-001", "input_kind": "EXTERNAL", "consumer_ids": ["T-001"],
        "contract": "ordered integer records", "source": "owner endpoint", "owner": "data owner",
        "availability_condition": "owner grants the requested dataset", "availability_status": status,
        "blocked_consumer_ids": ["T-001"] if status == "UNAVAILABLE" else []}
    if status == "AVAILABLE":
        context["input_contracts"]["INPUT-001"]["availability_evidence_id"] = "E-001"
    return context


def sequential_context(*, direct_reference=None, local_order=False):
    context = fixture_context()
    context["tasks"]["T-002"] = {"id": "T-002", "inputs": [direct_reference or "INPUT-002"],
        "outputs": ["artifact/second.yaml"], "dependencies": [] if local_order else ["T-001"], "r_refs": ["R-001"]}
    context["task_stage"]["T-002"] = "S-000"
    context["checks"]["C-002"] = {"id": "C-002", "task_id": "T-002", "inputs": ["artifact/second.yaml"],
        "procedure": "inspect second result", "expected_observation": "expected second result"}
    context["check_stage"]["C-002"] = "S-000"
    context["output_contracts"]["OUTPUT-001"]["consumer_ids"] = ["T-002"]
    context["output_contracts"]["OUTPUT-002"] = {"id": "OUTPUT-002", "path": "artifact/second.yaml",
        "producer_task_id": "T-002", "acceptance_check_id": "C-002", "consumer_ids": [], "contract": "ordered integer records"}
    context["output_contract_stage"]["OUTPUT-002"] = "S-000"
    if direct_reference is None:
        context["input_contracts"]["INPUT-002"] = {"id": "INPUT-002", "input_kind": "PLANNED", "output_id": "OUTPUT-001",
            "producer_task_id": "T-001", "acceptance_check_id": "C-001", "consumer_ids": ["T-002"], "contract": "ordered integer records"}
        context["input_contract_stage"]["INPUT-002"] = "S-000"
    if local_order:
        context["stages"]["S-000"] = {"id": "S-000", "tasks": list(context["tasks"].values()), "local_execution_graph": {
            "entry": "ENTRY", "exit": "EXIT", "edges": [
                {"from": "ENTRY", "to": "T-001"}, {"from": "T-001", "to": "T-002"}, {"from": "T-002", "to": "EXIT"}]}}
    return context


class CanonicalGraphSemanticsTests(unittest.TestCase):
    def test_unselected_branch_is_recorded_without_producing_or_dead_output(self):
        context = optional_branch_context(); graph = optional_branch_graph(context)
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["model_integrity_status"], "PASS")
        self.assertEqual(report["reachability_status"], "COMPLETE")
        for identifier in ("T-OPTIONAL", "ALGORITHM-T-OPTIONAL", "DATA-OUTPUT-OPTIONAL"):
            self.assertNotIn(identifier, report["constructive_reachable"])
            self.assertIn(identifier, report["unselected_nodes"])
        self.assertIn("C-OPTIONAL", report["constructive_reachable"])
        self.assertEqual(report["unused_outputs"], [])

    def test_unselected_output_cannot_support_goal(self):
        context = optional_branch_context(); graph = optional_branch_graph(context)
        add_edge(graph, "CONTRIBUTES_TO", "DATA-OUTPUT-OPTIONAL", "G-001")
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("unselected" in error.lower() for error in report["errors"]), report)
        self.assertNotIn("DATA-OUTPUT-OPTIONAL", report["constructive_reachable"])

    def test_unselected_output_cannot_feed_consumer(self):
        context = optional_branch_context(); graph = optional_branch_graph(context)
        add_edge(graph, "CONSUMES", "DATA-OUTPUT-OPTIONAL", "T-001", contract_id=graph["contracts"][0]["id"], input_id="INPUT-001")
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("unselected" in error.lower() for error in report["errors"]), report)

    def test_not_applicable_task_cannot_declare_unconditional_production(self):
        context = optional_branch_context()
        context["output_contracts"]["OUTPUT-OPTIONAL"]["availability"] = "UNCONDITIONAL"
        report = validate_architecture_graph(optional_branch_graph(context), context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("NOT_APPLICABLE" in error for error in report["errors"]), report)

    def test_selected_branch_requires_current_evidence_and_then_produces(self):
        from hashlib import sha256
        from prep_validation import canonical_yaml_bytes

        context = fixture_context()
        context["output_contracts"]["OUTPUT-001"].update({"version": "1", "availability": "SELECTED_BRANCH", "branch_selection_status": "SELECTED", "branch_selection_evidence_id": "E-BRANCH"})
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertNotIn("DATA-OUTPUT-001", report["constructive_reachable"])
        self.assertTrue(any("selected branch" in error.lower() for error in report["errors"]), report)
        output = context["output_contracts"]["OUTPUT-001"]
        digest = "a" * 64
        context["results"]["E-BRANCH"] = {**context["results"]["E-001"], "agent_id": "producer", "content_hash": digest}
        context["docs"]["E-BRANCH"] = {**context["docs"]["E-001"], "id": "E-BRANCH",
                                         "branch_selection_review_id": "REVIEW-BRANCH"}
        context["results"]["REVIEW-BRANCH"] = {"agent_id": "checker", "artifact_type": "REVIEW"}
        context["docs"]["REVIEW-BRANCH"] = {
            "artifact_type": "REVIEW", "outcome": "PASS", "target_result_id": "E-BRANCH", "target_hash": digest,
            "branch_selection_scope": {
                "output_id": "OUTPUT-001", "contract_version": output["version"],
                "producer_task_id": output["producer_task_id"], "acceptance_check_id": output["acceptance_check_id"],
                "contract_sha256": sha256(canonical_yaml_bytes(output)).hexdigest(),
            },
        }
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["errors"], [])
        self.assertIn("DATA-OUTPUT-001", report["constructive_reachable"])
        original = dict(output)
        output["branch_guard"] = "changed scope with the same ID and version"
        wrong_scope = validate_architecture_graph(graph_for_context(context), context)
        self.assertTrue(any("selected branch requires current independently reviewed E" in error
                            for error in wrong_scope["errors"]))
        self.assertNotIn("withdrawn_branch_errors", wrong_scope)
        output.clear()
        output.update(original)
        for stale in ("E-BRANCH", "REVIEW-BRANCH"):
            with self.subTest(stale=stale):
                context["stale_result_ids"] = [stale]
                report = validate_architecture_graph(graph_for_context(context), context)
                self.assertNotIn("DATA-OUTPUT-001", report["constructive_reachable"])
                self.assertTrue(any("selected branch" in error.lower() for error in report["errors"]), report)
                self.assertIn("DATA-OUTPUT-001", report["withdrawn_branch_errors"])

    def test_external_pending_is_valid_model_with_explicit_blockers(self):
        context = external_context(); report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["model_integrity_status"], "PASS")
        self.assertEqual(report["reachability_status"], "BLOCKED")
        self.assertEqual(report["readiness_status"], "BLOCKED")
        self.assertTrue(report["blockers"])
        self.assertIn("T-001", report["blocked_nodes"])
        self.assertNotIn("G-001", report["constructive_reachable"])
        self.assertEqual(report["implementation_readiness"], "NOT_ASSESSED")

    def test_external_pending_without_plan_is_accepted_as_conditional_synthesis(self):
        context = external_context(); graph = graph_for_context(context)
        context.update({"plan_required": False, "tasks": {}, "checks": {}, "stages": {}, "input_contracts": {}, "output_contracts": {}})
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reachability_status"], "BLOCKED")
        self.assertNotIn("G-001", report["constructive_reachable"])

    def test_external_availability_requires_evidence_and_resolves_by_addressed_revision(self):
        context = external_context("AVAILABLE", "NOT_STARTED")
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reachability_status"], "COMPLETE")
        self.assertIn("G-001", report["constructive_reachable"])
        context["input_contracts"]["INPUT-001"].pop("availability_evidence_id")
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("accepted E" in error for error in report["errors"]), report)

    def test_external_pending_cannot_claim_consumer_already_executed(self):
        for status in ("RUNNING", "ACCEPTED"):
            with self.subTest(status=status):
                context = external_context(task_status=status)
                report = validate_architecture_graph(graph_for_context(context), context)
                self.assertEqual(report["structural_status"], "FAIL")
                self.assertTrue(any("executed" in error for error in report["errors"]), report)

    def test_available_input_cannot_retain_a_canonical_consumer_block(self):
        context = external_context("AVAILABLE", "NOT_STARTED")
        context["input_contracts"]["INPUT-001"]["blocked_consumer_ids"] = ["T-001"]
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["model_integrity_status"], "FAIL")
        self.assertTrue(any("retain blocked" in error for error in report["errors"]), report)

    def test_blocked_failed_and_recheck_states_have_distinct_machine_reasons(self):
        for status in ("BLOCKED", "FAILED", "NEEDS_RECHECK"):
            with self.subTest(status=status):
                context = fixture_context(); context["tasks"]["T-001"]["execution_status"] = status
                report = validate_architecture_graph(graph_for_context(context), context)
                self.assertEqual(report["errors"], [])
                self.assertEqual(report["reachability_status"], "BLOCKED")
                self.assertTrue(any(row["node_id"] == "T-001" and row["reason"] == "TASK_" + status for row in report["blockers"]), report)
                self.assertNotIn("DATA-OUTPUT-001", report["constructive_reachable"])

    def test_external_blocker_cannot_mask_malformed_typed_mechanism_or_cycle(self):
        context = external_context(); graph = graph_for_context(context)
        graph["edges"] = [edge for edge in graph["edges"] if edge["kind"] != "REALIZES"]
        add_edge(graph, "REQUIRES", "ALGORITHM-T-001", "CAPABILITY-001")
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["model_integrity_status"], "FAIL")
        self.assertTrue(any("no REALIZES" in error for error in report["errors"]), report)
        graph = graph_for_context(context)
        add_edge(graph, "REQUIRES", "DATA-OUTPUT-001", "T-001")
        report = validate_architecture_graph(graph, context)
        self.assertTrue(any("cycle" in error for error in report["errors"]), report)

    def test_unrequired_disconnected_external_condition_does_not_block_ready_path(self):
        context = fixture_context(); graph = graph_for_context(context)
        graph["nodes"].append({"id": "DATA-OPTIONAL-EXTERNAL", "kind": "DATA", "label": "future optional dataset",
            "subsystem_id": "optional", "mechanism_id": graph["selected_mechanism_id"], "required": "false",
            "contract_id": graph["contracts"][0]["id"], "availability": "EXTERNAL", "source": "optional owner endpoint",
            "owner": "optional data owner", "availability_condition": "future permission", "availability_status": "UNAVAILABLE"})
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reachability_status"], "COMPLETE")
        self.assertEqual(report["blockers"], [])
        self.assertIn("DATA-OPTIONAL-EXTERNAL", report["blocked_nodes"])

    def test_independent_completed_branch_survives_other_external_blocker(self):
        context = external_context()
        second = fixture_context()
        context["tasks"]["T-002"] = {"id": "T-002", "execution_status": "ACCEPTED", "inputs": ["INPUT-002"], "outputs": ["artifact/second.yaml"], "dependencies": [], "r_refs": ["R-001"]}
        context["task_stage"]["T-002"] = "S-000"
        context["checks"]["C-002"] = {"id": "C-002", "task_id": "T-002", "inputs": ["artifact/second.yaml"], "procedure": "independent bounded observation", "expected_observation": "second output"}
        context["check_stage"]["C-002"] = "S-000"
        context["input_contracts"]["INPUT-002"] = {**second["input_contracts"]["INPUT-001"], "id": "INPUT-002", "consumer_ids": ["T-002"]}
        context["input_contract_stage"]["INPUT-002"] = "S-000"
        context["output_contracts"]["OUTPUT-002"] = {**second["output_contracts"]["OUTPUT-001"], "id": "OUTPUT-002", "path": "artifact/second.yaml", "producer_task_id": "T-002", "acceptance_check_id": "C-002"}
        context["output_contract_stage"]["OUTPUT-002"] = "S-000"
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reachability_status"], "BLOCKED")
        self.assertIn("T-002", report["constructive_reachable"])
        self.assertIn("DATA-OUTPUT-002", report["constructive_reachable"])
        self.assertNotIn("T-002", report["blocked_nodes"])

    def test_local_graph_order_without_duplicate_dependency_is_accepted(self):
        context = sequential_context(local_order=True)
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reachability_status"], "COMPLETE")

    def test_direct_output_id_and_path_share_the_canonical_contract(self):
        for reference in ("OUTPUT-001", "artifact/result.yaml"):
            with self.subTest(reference=reference):
                context = sequential_context(direct_reference=reference)
                graph = graph_for_context(context)
                data = next(node for node in graph["nodes"] if node["id"] == "DATA-OUTPUT-001")
                add_edge(graph, "CONSUMES", data["id"], "T-002", input_id=reference, contract_id=data["contract_id"])
                report = validate_architecture_graph(graph, context)
                self.assertEqual(report["errors"], [])
                context["output_contracts"]["OUTPUT-001"]["consumer_ids"] = []
                report = validate_architecture_graph(graph, context)
                self.assertEqual(report["structural_status"], "FAIL")
                self.assertTrue(any("binding" in error for error in report["errors"]), report)

    def test_order_cycle_unknown_input_and_unexplained_unreachability_remain_errors(self):
        context = sequential_context(local_order=True)
        context["stages"]["S-000"]["local_execution_graph"]["edges"].append({"from": "T-002", "to": "T-001"})
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertTrue(any("cycle" in error for error in report["errors"]), report)
        context = sequential_context(direct_reference="OUTPUT-NOT-FOUND")
        graph = graph_for_context(context)
        add_edge(graph, "CONSUMES", "DATA-OUTPUT-001", "T-002", input_id="OUTPUT-NOT-FOUND", contract_id=graph["contracts"][0]["id"])
        self.assertEqual(validate_architecture_graph(graph, context)["structural_status"], "FAIL")
        context = fixture_context(); graph = graph_for_context(context)
        graph["edges"] = [edge for edge in graph["edges"] if not (edge["kind"] == "CONSUMES" and edge["to_id"] == "T-001")]
        report = validate_architecture_graph(graph, context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("not constructively reachable" in error for error in report["errors"]), report)

    def test_malformed_canonical_status_and_order_return_addressed_errors(self):
        for value in ([], {}, True, "UNKNOWN_STATE"):
            with self.subTest(status=value):
                context = fixture_context(); context["tasks"]["T-001"]["execution_status"] = value
                report = validate_architecture_graph(graph_for_context(context), context)
                self.assertEqual(report["model_integrity_status"], "FAIL")
                self.assertTrue(any("execution_status" in error for error in report["errors"]), report)
        context = fixture_context(); context["task_order_edges"] = [["T-001", {"malformed": "target"}]]
        report = validate_architecture_graph(graph_for_context(context), context)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("task_order_edges" in error for error in report["errors"]), report)


if __name__ == "__main__":
    unittest.main()
