"""Repeat-audit regressions on complete accepted temporary models.

These scenarios reconstruct the supplied audit text; its original probe script
was not supplied. Fixture semantic PASS records are TEST_ONLY, not role audits.
"""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from test_architecture import graph_for_context
from test_architecture_integration import CompleteArchitectureModel, bind_architecture
from test_iceberg_contracts import _refresh_hash_bindings
from test_model_contracts import model_validation as model, load_yaml, digest, write
from architecture_validation import (architecture_impact, derive_architecture_views,
                                     validate_architecture_graph)


class RepeatAuditFixture:
    """Keep all normative records and exact fixture bindings coherent."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = CompleteArchitectureModel(Path(self.tmp.name))

    def close(self):
        self.tmp.cleanup()

    @property
    def stages(self):
        return {key: self.fixture.docs[key] for key in ("S-000", "S-001")}

    @property
    def graph(self):
        return self.fixture.docs["SYNTHESIS-001"]["architecture_graph"]

    def context(self):
        f = self.fixture
        return model.architecture_context(f.run, f.bank, f.results, f.docs,
                                          self.stages, load_yaml=load_yaml, sha=digest)

    def rebuild(self):
        graph = graph_for_context(self.context())
        # The old fixture builder emits both contract-consumer and check-input
        # edges; one semantic edge suffices, preserving the original input ID.
        seen = set()
        graph["edges"] = [edge for edge in graph["edges"] if not (
            (edge["kind"], edge["from_id"], edge["to_id"], edge.get("input_id")) in seen
            or seen.add((edge["kind"], edge["from_id"], edge["to_id"], edge.get("input_id"))))]
        self.fixture.docs["SYNTHESIS-001"]["architecture_graph"] = graph
        return graph

    def bind(self):
        for key in self.stages:
            self.fixture.sync(key)
        _refresh_hash_bindings(self.fixture)
        bind_architecture(self.fixture)

    def result(self):
        self.bind()
        f = self.fixture
        report = validate_architecture_graph(self.graph, self.context())
        return {
            "full_model_errors": model.validate_model(f.run, f.bank, f.results,
                                                       f.docs, "final", load_yaml, digest),
            "architecture_report": report,
        }

    def external(self, consumer="C-001", identifier="INPUT-EXTERNAL-AUDIT",
                 status="UNAVAILABLE"):
        row_kind = "acceptance_exit" if consumer.startswith("C-") else "tasks"
        stage, consumer_doc = next((stage, row) for stage in self.stages.values()
                                   for row in stage[row_kind] if row["id"] == consumer)
        canonical = {
            "id": identifier, "input_kind": "EXTERNAL", "consumer_ids": [consumer],
            "source": "independent exact oracle endpoint", "owner": "oracle owner",
            "availability_condition": "owner grants the exact independent oracle payload",
            "availability_status": status,
            "blocked_consumer_ids": [consumer] if status == "UNAVAILABLE" else [],
            "contract": stage["inputs_contracts"]["inputs"][0]["contract"],
            "format": "application/json", "version": "1",
        }
        if status == "AVAILABLE":
            canonical["availability_evidence_id"] = "E-001"
        stage["inputs_contracts"]["inputs"].append(canonical)
        consumer_doc["inputs"].append(identifier)
        if status == "UNAVAILABLE" and consumer.startswith("T-"):
            consumer_doc["execution_status"] = "BLOCKED"
        return canonical

    def planned_alias(self, reference="INPUT-T-001", consumer="C-001"):
        stage = self.stages["S-001"]
        canonical = stage["inputs_contracts"]["inputs"][0]
        if consumer not in canonical["consumer_ids"]:
            canonical["consumer_ids"].append(consumer)
        output = self.stages["S-000"]["inputs_contracts"]["outputs"][0]
        if consumer not in output["consumer_ids"]:
            output["consumer_ids"].append(consumer)
        if consumer.startswith("C-"):
            stage["acceptance_exit"][0]["inputs"].append(reference)
        else:
            stage["tasks"][0]["inputs"] = [reference]
        graph = self.rebuild()
        # Explicitly use the same legal spelling as the canonical consumer.
        for edge in graph["edges"]:
            if edge["kind"] == "CONSUMES" and edge["to_id"] == consumer and edge["from_id"] == "DATA-OUTPUT-T-000":
                edge["input_id"] = reference
        seen = set()
        graph["edges"] = [edge for edge in graph["edges"] if not (
            (edge["kind"], edge["from_id"], edge["to_id"], edge.get("input_id")) in seen
            or seen.add((edge["kind"], edge["from_id"], edge["to_id"], edge.get("input_id"))))]
        return graph

    def existing(self, consumer="C-001"):
        stage = self.stages["S-001"]
        canonical = copy.deepcopy(self.stages["S-000"]["inputs_contracts"]["inputs"][0])
        canonical.update({"id": "INPUT-MATRIX-EXISTING", "consumer_ids": [consumer]})
        stage["inputs_contracts"]["inputs"].append(canonical)
        rows = stage["acceptance_exit" if consumer.startswith("C-") else "tasks"]
        next(row for row in rows if row["id"] == consumer)["inputs"].append(canonical["id"])
        return canonical

    def local_order(self, explicit_dependency=False):
        stage = self.stages["S-001"]
        task = copy.deepcopy(stage["tasks"][0])
        task.update({"id": "T-LOCAL", "outputs": ["artifact/T-LOCAL.yaml"],
                     "dependencies": ["T-000", "T-001"] if explicit_dependency else ["T-000"],
                     "checks": ["C-LOCAL"], "acceptance_criteria": ["C-LOCAL"],
                     "serialized_with": ["T-000", "T-001"]})
        check = copy.deepcopy(stage["acceptance_exit"][0])
        check.update({"id": "C-LOCAL", "task_id": "T-LOCAL", "inputs": ["artifact/T-LOCAL.yaml"]})
        output = copy.deepcopy(stage["inputs_contracts"]["outputs"][0])
        output.update({"id": "OUTPUT-T-LOCAL", "path": "artifact/T-LOCAL.yaml",
                       "producer_task_id": "T-LOCAL", "acceptance_check_id": "C-LOCAL"})
        stage["tasks"].append(task)
        stage["acceptance_exit"].append(check)
        stage["inputs_contracts"]["outputs"].append(output)
        stage["inputs_contracts"]["inputs"][0]["consumer_ids"].append("T-LOCAL")
        self.stages["S-000"]["inputs_contracts"]["outputs"][0]["consumer_ids"].append("T-LOCAL")
        stage["outputs_handoff"]["artifacts"].append("artifact/T-LOCAL.yaml")
        stage["outputs_handoff"]["consumers"].append("C-LOCAL")
        local = stage["local_execution_graph"]
        local["edges"] = [{"from": local["entry"], "to": "T-001"},
                          {"from": "T-001", "to": "T-LOCAL"},
                          {"from": "T-LOCAL", "to": local["exit"]}]
        execution = self.fixture.docs["SYNTHESIS-001"]["execution_edges"]
        execution.extend([{"from_type": "T", "from_id": "T-001", "to_type": "T", "to_id": "T-LOCAL"},
                          {"from_type": "T", "from_id": "T-LOCAL", "to_type": "C", "to_id": "C-LOCAL"}])
        return self.rebuild()

    def mandatory(self, obligation="all", hidden=False, status="UNAVAILABLE"):
        stage = self.stages["S-001"]
        check = copy.deepcopy(stage["acceptance_exit"][0])
        check.update({"id": "C-MANDATORY", "inputs": ["artifact/T-001.yaml"],
                      "execution_status": "NOT_RUN"})
        stage["acceptance_exit"].append(check)
        stage["inputs_contracts"]["outputs"][0]["consumer_ids"].append("C-MANDATORY")
        self.external("C-MANDATORY", "INPUT-CHECK-ORACLE", status)
        task = stage["tasks"][0]
        if obligation in ("all", "task_checks"):
            task["checks"].append("C-MANDATORY")
        if obligation in ("all", "acceptance_criteria"):
            task["acceptance_criteria"].append("C-MANDATORY")
        self.fixture.docs["SYNTHESIS-001"]["execution_edges"].append(
            {"from_type": "T", "from_id": "T-001", "to_type": "C", "to_id": "C-MANDATORY"})
        graph = self.rebuild()
        if obligation in ("all", "edge_checks"):
            edge = next(edge for edge in graph["edges"] if edge["kind"] == "CONSUMES" and edge["to_id"] == "T-001")
            edge["check_ids"].append("C-MANDATORY")
        if obligation in ("all", "contract_checks"):
            contract_id = next(edge["contract_id"] for edge in graph["edges"] if edge["kind"] == "CONSUMES" and edge["to_id"] == "T-001")
            contract = next(row for row in graph["contracts"] if row["id"] == contract_id)
            contract["acceptance_check_ids"].append("C-MANDATORY")
        if hidden:
            for node in graph["nodes"]:
                if node["id"] in ("C-MANDATORY", "DATA-INPUT-CHECK-ORACLE"):
                    node["required"] = "false"
            graph["edges"] = [edge for edge in graph["edges"] if not (
                edge["from_id"] == "C-MANDATORY" and edge["kind"] == "CONTRIBUTES_TO")]
        return graph

    def stage_order_only(self, explicit_dependency=False):
        stage = self.stages["S-001"]
        canonical = copy.deepcopy(self.stages["S-000"]["inputs_contracts"]["inputs"][0])
        canonical.update({"id": "INPUT-T-001", "consumer_ids": ["T-001"]})
        stage["inputs_contracts"]["inputs"] = [canonical]
        stage["tasks"][0]["dependencies"] = ["T-000"] if explicit_dependency else []
        stage["tasks"][0]["writes"] = ["artifact/T-001.yaml"]
        self.stages["S-000"]["inputs_contracts"]["outputs"][0]["consumer_ids"] = []
        # The reciprocal stage barrier remains canonical in both variants.
        return self.rebuild()


def add_order_edge(graph, source="T-001", target="T-LOCAL"):
    graph["edges"].append({"id": "EDGE-REPEAT-ORDER", "kind": "DEPENDS_ON",
                           "from_id": source, "to_id": target, "meaning": "canonical local order",
                           "scope": "EXECUTION", "conditions": [], "check_ids": []})


class RepeatAuditContractsTests(unittest.TestCase):
    def setUp(self):
        self.h = RepeatAuditFixture()

    def tearDown(self):
        self.h.close()

    def test_complete_positive_control(self):
        self.assertEqual(self.h.result()["full_model_errors"], [])

    def test_F421_01_external_check_input_cannot_be_relabelled_existing(self):
        self.h.external(); self.h.rebuild()
        honest = self.h.result()
        self.assertEqual(honest["architecture_report"]["model_integrity_status"], "PASS")
        self.assertEqual(honest["architecture_report"]["reachability_status"], "BLOCKED")
        graph = self.h.graph
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "DATA-INPUT-EXTERNAL-AUDIT"]
        for edge in graph["edges"]:
            if edge["from_id"] == "DATA-INPUT-EXTERNAL-AUDIT":
                edge["from_id"] = "DATA-INPUT-T-000"
            if edge["to_id"] == "DATA-INPUT-EXTERNAL-AUDIT":
                edge["to_id"] = "DATA-INPUT-T-000"
        result = self.h.result()
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")

    def test_F421_02_check_input_alias_forms_are_equivalent(self):
        outcomes = []
        for reference in ("INPUT-T-001", "OUTPUT-T-000", "artifact/T-000.yaml"):
            with self.subTest(reference=reference):
                h = RepeatAuditFixture()
                try:
                    h.planned_alias(reference)
                    result = h.result()
                    self.assertEqual(result["full_model_errors"], [], result)
                    outcomes.append(result["architecture_report"]["constructive_reachable"])
                finally:
                    h.close()
        self.assertTrue(all(row == outcomes[0] for row in outcomes))

    def test_F421_03_missing_local_order_is_rejected(self):
        self.h.local_order()
        result = self.h.result()
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")
        self.assertTrue(any("order" in error.lower() or "dependency" in error.lower()
                            for error in result["architecture_report"]["errors"]), result)

    def test_F421_03_covered_order_blocks_and_invalidates_successors(self):
        graph = self.h.local_order(); add_order_edge(graph)
        self.assertEqual(self.h.result()["full_model_errors"], [])
        self.h.stages["S-001"]["tasks"][0]["execution_status"] = "BLOCKED"
        result = self.h.result()
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "PASS")
        self.assertNotIn("T-LOCAL", result["architecture_report"]["constructive_reachable"])
        impact = architecture_impact(graph, ["T-001"])
        self.assertIn("T-LOCAL", impact["affected_task_ids"])
        self.assertIn("C-LOCAL", impact["affected_check_ids"])

    def test_F421_04_required_false_cannot_cancel_mandatory_acceptance(self):
        self.h.mandatory(hidden=True)
        result = self.h.result(); report = result["architecture_report"]
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(report["reachability_status"], "BLOCKED")
        self.assertTrue(any(row["node_id"] in ("C-MANDATORY", "DATA-INPUT-CHECK-ORACLE")
                            for row in report["blockers"]), report)

    def test_future_NOT_RUN_checks_are_constructible_without_observed_PASS(self):
        self.h.mandatory(hidden=True, status="AVAILABLE")
        result = self.h.result()
        self.assertEqual(result["full_model_errors"], [], result)
        self.assertEqual(result["architecture_report"]["reachability_status"], "COMPLETE")

    def test_input_matrix_legal_forms_keep_full_model_and_reachability_consistent(self):
        # OUTPUT-ID/path denote PLANNED output contracts. EXISTING/EXTERNAL
        # have INPUT-ID only; EXISTING must already have accepted identity.
        # Thus 18 of the 36 nominal cross-product cells are legal contracts.
        cases = []
        for consumer in ("T-001", "C-001"):
            cases.append((consumer, "INPUT-ID", "EXISTING", "AVAILABLE"))
            for availability in ("AVAILABLE", "UNAVAILABLE"):
                cases.append((consumer, "INPUT-ID", "EXTERNAL", availability))
                for form in ("INPUT-ID", "OUTPUT-ID", "path"):
                    cases.append((consumer, form, "PLANNED", availability))
        self.assertEqual(len(cases), 18)
        for consumer, form, kind, availability in cases:
            with self.subTest(consumer=consumer, form=form, kind=kind, availability=availability):
                h = RepeatAuditFixture()
                try:
                    if kind == "PLANNED":
                        reference = {"INPUT-ID": "INPUT-T-001", "OUTPUT-ID": "OUTPUT-T-000",
                                     "path": "artifact/T-000.yaml"}[form]
                        h.planned_alias(reference, consumer)
                        if availability == "UNAVAILABLE":
                            h.stages["S-000"]["tasks"][0]["execution_status"] = "BLOCKED"
                    else:
                        h.existing(consumer) if kind == "EXISTING" else h.external(consumer, status=availability)
                        h.rebuild()
                    result = h.result(); report = result["architecture_report"]
                    self.assertEqual(report["model_integrity_status"], "PASS", result)
                    if availability == "AVAILABLE":
                        self.assertEqual(result["full_model_errors"], [], result)
                        self.assertEqual(report["reachability_status"], "COMPLETE", result)
                    else:
                        self.assertEqual(report["reachability_status"], "BLOCKED", result)
                        self.assertTrue(result["full_model_errors"], result)
                        self.assertNotIn(consumer, report["constructive_reachable"], result)
                finally:
                    h.close()

    def test_check_existing_identity_cannot_be_rebound_to_another_accepted_source(self):
        self.h.existing(); graph = self.h.rebuild()
        self.assertEqual(self.h.result()["full_model_errors"], [])
        data = next(node for node in graph["nodes"] if node["id"] == "DATA-INPUT-MATRIX-EXISTING")
        data["source_id"] = "input/bank.yaml"
        data["accepted_hash"] = self.h.fixture.bank_hash
        report = self.h.result()
        self.assertTrue(report["full_model_errors"], report)
        self.assertEqual(report["architecture_report"]["model_integrity_status"], "FAIL")

    def test_same_named_check_input_cannot_change_external_source_or_version(self):
        for mutation in ("source", "version", "format", "semantics"):
            with self.subTest(mutation=mutation):
                h = RepeatAuditFixture()
                try:
                    h.external(status="AVAILABLE"); graph = h.rebuild()
                    data = next(node for node in graph["nodes"] if node["id"] == "DATA-INPUT-EXTERNAL-AUDIT")
                    # Isolate this exact input contract, so a version mutation
                    # cannot fail merely because some unrelated EXISTING node
                    # shares the fixture builder's deduplicated contract.
                    original = next(row for row in graph["contracts"] if row["id"] == data["contract_id"])
                    contract = copy.deepcopy(original)
                    contract["id"] = "CONTRACT-MATRIX-CHECK"
                    contract["plan_refs"] = [ref for ref in original["plan_refs"]
                                             if ref["contract_id"] == "INPUT-EXTERNAL-AUDIT"]
                    original["plan_refs"] = [ref for ref in original["plan_refs"]
                                             if ref["contract_id"] != "INPUT-EXTERNAL-AUDIT"]
                    graph["contracts"].append(contract)
                    data["contract_id"] = contract["id"]
                    for edge in graph["edges"]:
                        if edge["kind"] == "CONSUMES" and edge["from_id"] == data["id"]:
                            edge["contract_id"] = contract["id"]
                    self.assertEqual(h.result()["full_model_errors"], [])
                    if mutation == "source":
                        data["source"] = "another plausible oracle endpoint"
                    else:
                        contract[mutation] = {"version": "2", "format": "application/octet-stream",
                                              "semantics": "different schema and units"}[mutation]
                    result = h.result()
                    self.assertTrue(result["full_model_errors"], result)
                    self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")
                finally:
                    h.close()

    def test_unknown_ambiguous_and_wrong_consumer_check_inputs_are_rejected(self):
        for mutation in ("unknown", "ambiguous", "wrong_consumer"):
            with self.subTest(mutation=mutation):
                h = RepeatAuditFixture()
                try:
                    h.planned_alias()
                    self.assertEqual(h.result()["full_model_errors"], [])
                    if mutation == "unknown":
                        for edge in h.graph["edges"]:
                            if edge.get("input_id") == "INPUT-T-001" and edge["to_id"] == "C-001":
                                edge["input_id"] = "INPUT-NOT-DECLARED"
                    elif mutation == "wrong_consumer":
                        h.stages["S-001"]["inputs_contracts"]["inputs"][0]["consumer_ids"].remove("C-001")
                    else:
                        output = copy.deepcopy(h.stages["S-001"]["inputs_contracts"]["outputs"][0])
                        output.update({"id": "OUTPUT-AMBIGUOUS", "path": "INPUT-T-001"})
                        h.stages["S-001"]["inputs_contracts"]["outputs"].append(output)
                    result = h.result()
                    self.assertTrue(result["full_model_errors"], result)
                    self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")
                finally:
                    h.close()

    def test_local_edges_and_dependencies_have_identical_reachability_and_impact(self):
        outcomes = []
        for explicit_dependency in (False, True):
            h = RepeatAuditFixture()
            try:
                graph = h.local_order(explicit_dependency)
                if not explicit_dependency:
                    add_order_edge(graph)
                self.assertEqual(h.result()["full_model_errors"], [])
                h.stages["S-001"]["tasks"][0]["execution_status"] = "BLOCKED"
                report = h.result()["architecture_report"]
                impact = architecture_impact(graph, ["T-001"])
                outcomes.append((report["constructive_reachable"], report["blocked_nodes"],
                                 impact["affected_task_ids"], impact["affected_check_ids"]))
                self.assertNotIn("T-LOCAL", report["constructive_reachable"])
                self.assertIn("T-LOCAL", impact["affected_task_ids"])
                views = derive_architecture_views(graph)
                self.assertTrue(any(edge["from_id"] == "T-001" and edge["to_id"] == "T-LOCAL"
                                    for edge in views["implementation"]["edges"]), views)
            finally:
                h.close()
        self.assertEqual(outcomes[0], outcomes[1])

    def test_each_independent_acceptance_obligation_survives_required_false(self):
        for obligation in ("all", "edge_checks", "contract_checks"):
            for status in ("AVAILABLE", "UNAVAILABLE"):
                with self.subTest(obligation=obligation, availability=status):
                    h = RepeatAuditFixture()
                    try:
                        h.mandatory(obligation, hidden=True, status=status)
                        result = h.result(); report = result["architecture_report"]
                        self.assertEqual(report["model_integrity_status"], "PASS", result)
                        if status == "AVAILABLE":
                            self.assertEqual(result["full_model_errors"], [], result)
                            self.assertEqual(report["reachability_status"], "COMPLETE", report)
                        else:
                            self.assertTrue(result["full_model_errors"], result)
                            self.assertEqual(report["reachability_status"], "BLOCKED", report)
                            self.assertTrue(report["blockers"], report)
                    finally:
                        h.close()

    def test_task_checks_and_acceptance_criteria_are_a_co_required_pair(self):
        for field in ("task_checks", "acceptance_criteria"):
            h = RepeatAuditFixture()
            try:
                h.mandatory(field, hidden=True, status="AVAILABLE")
                result = h.result()
                self.assertIn("task:T-001:check_acceptance_mismatch", result["full_model_errors"])
            finally:
                h.close()

    def test_stage_barrier_without_duplicate_task_dependencies_requires_graph_coverage(self):
        graph = self.h.stage_order_only()
        graph["edges"] = [edge for edge in graph["edges"] if not (
            edge["from_id"] in ("T-000", "C-000") and edge["to_id"] == "T-001")]
        result = self.h.result()
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")

    def test_stage_barrier_and_explicit_task_order_are_semantically_equivalent(self):
        outcomes = []
        for explicit_dependency in (False, True):
            h = RepeatAuditFixture()
            try:
                graph = h.stage_order_only(explicit_dependency)
                if not explicit_dependency:
                    add_order_edge(graph, "T-000", "T-001")
                self.assertEqual(h.result()["full_model_errors"], [])
                h.stages["S-000"]["tasks"][0]["execution_status"] = "BLOCKED"
                report = h.result()["architecture_report"]
                impact = architecture_impact(graph, ["T-000"])
                self.assertNotIn("T-001", report["constructive_reachable"], report)
                self.assertIn("T-001", impact["affected_task_ids"])
                self.assertIn("C-001", impact["affected_check_ids"])
                outcomes.append((report["constructive_reachable"], report["blocked_nodes"],
                                 impact["affected_task_ids"], impact["affected_check_ids"]))
            finally:
                h.close()
        self.assertEqual(outcomes[0], outcomes[1])

    def test_unselected_stage_task_does_not_fabricate_a_required_stage_order(self):
        self.h.stage_order_only()
        stage = self.h.stages["S-001"]
        stage["tasks"][0].update({"execution_status": "NOT_APPLICABLE",
                                   "branch_guard": "recorded selection excludes this task"})
        stage["inputs_contracts"]["outputs"][0]["availability"] = "UNSELECTED_BRANCH"
        stage["inputs_contracts"]["inputs"][0]["consumer_ids"].append("C-001")
        stage["acceptance_exit"][0]["inputs"] = ["INPUT-T-001"]
        graph = self.h.rebuild()
        graph["edges"] = [edge for edge in graph["edges"] if not (
            edge["kind"] == "REALIZES" and edge["from_id"] == "ALGORITHM-T-001")]
        result = self.h.result()
        self.assertEqual(result["full_model_errors"], [], result)
        self.assertEqual(result["architecture_report"]["reachability_status"], "COMPLETE")
        self.assertNotIn("T-001", result["architecture_report"]["constructive_reachable"])

    def test_planned_output_consumer_requires_canonical_acceptance_coverage(self):
        graph = self.h.graph
        # T-001 reads OUTPUT-T-000 through INPUT-T-001. Its accepting C-000
        # must precede consumption; T-000 itself produces before its C runs.
        graph["edges"] = [edge for edge in graph["edges"] if not (
            edge["from_id"] == "C-000" and edge["to_id"] == "T-001")]
        result = self.h.result()
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")

    def test_blocked_acceptance_blocks_downstream_consumer_but_not_raw_producer(self):
        self.h.external("C-000", "INPUT-PREDECESSOR-ORACLE")
        self.h.stages["S-000"]["acceptance_exit"][0]["execution_status"] = "NOT_RUN"
        graph = self.h.rebuild()
        result = self.h.result(); report = result["architecture_report"]
        self.assertEqual(report["model_integrity_status"], "PASS", result)
        self.assertEqual(report["reachability_status"], "BLOCKED", report)
        self.assertIn("T-000", report["constructive_reachable"], report)
        self.assertIn("DATA-OUTPUT-T-000", report["constructive_reachable"], report)
        self.assertNotIn("T-001", report["constructive_reachable"], report)
        self.assertNotIn("C-001", report["constructive_reachable"], report)
        impact = architecture_impact(graph, ["C-000"])
        self.assertIn("T-001", impact["affected_task_ids"])
        self.assertIn("C-001", impact["affected_check_ids"])
        self.assertNotIn("T-000", impact["affected_task_ids"])

    def test_future_acceptance_NOT_RUN_with_available_inputs_admits_downstream_plan(self):
        self.h.external("C-000", "INPUT-PREDECESSOR-ORACLE", status="AVAILABLE")
        self.h.stages["S-000"]["acceptance_exit"][0]["execution_status"] = "NOT_RUN"
        self.h.rebuild()
        result = self.h.result()
        self.assertEqual(result["full_model_errors"], [], result)
        self.assertEqual(result["architecture_report"]["reachability_status"], "COMPLETE")

    def test_stage_acceptance_barrier_blocks_successor_even_without_shared_data(self):
        self.h.stage_order_only()
        self.h.external("C-000", "INPUT-STAGE-ACCEPTANCE-ORACLE")
        self.h.stages["S-000"]["acceptance_exit"][0]["execution_status"] = "NOT_RUN"
        graph = self.h.rebuild()
        add_order_edge(graph, "T-000", "T-001")
        result = self.h.result(); report = result["architecture_report"]
        self.assertEqual(report["model_integrity_status"], "PASS", result)
        self.assertIn("T-000", report["constructive_reachable"], report)
        self.assertNotIn("T-001", report["constructive_reachable"], report)
        self.assertEqual(report["reachability_status"], "BLOCKED", report)
        self.assertIn("T-001", architecture_impact(graph, ["C-000"])["affected_task_ids"])

    def test_stage_task_order_cannot_substitute_for_stage_acceptance_coverage(self):
        graph = self.h.stage_order_only()
        add_order_edge(graph, "T-000", "T-001")
        graph["edges"] = [edge for edge in graph["edges"] if not (
            edge["from_id"] == "C-000" and edge["to_id"] == "T-001")]
        result = self.h.result()
        self.assertTrue(result["full_model_errors"], result)
        self.assertEqual(result["architecture_report"]["model_integrity_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
