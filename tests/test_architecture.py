"""Destructive architecture tests; fixtures describe future work, not runtime proof."""
from __future__ import annotations
import copy
import hashlib
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from architecture_validation import (architecture_graph_sha256, validate_architecture_graph,
    derive_architecture_views, render_architecture_mermaid, architecture_impact,
    compare_architecture_implementation)
from model_validation import _acyclic, _reachable


def fixture_context():
    sha = "1" * 64
    return {
        "requirements": {"R-001"}, "goal_ids": {"VECTOR-001"},
        "stages": {"S-000": {}},
        "tasks": {"T-001": {"id": "T-001", "inputs": ["INPUT-001"], "outputs": ["artifact/result.yaml"], "dependencies": [], "r_refs": ["R-001"]}},
        "checks": {"C-001": {"id": "C-001", "task_id": "T-001", "inputs": ["artifact/result.yaml"], "procedure": "run bounded acceptance", "expected_observation": "expected payload"}},
        "task_stage": {"T-001": "S-000"}, "check_stage": {"C-001": "S-000"},
        "input_contracts": {"INPUT-001": {"id": "INPUT-001", "input_kind": "EXISTING", "source_id": "E-001", "version": "1", "accepted_hash": sha, "consumer_ids": ["T-001"], "contract": "ordered integer records"}},
        "output_contracts": {"OUTPUT-001": {"id": "OUTPUT-001", "path": "artifact/result.yaml", "producer_task_id": "T-001", "acceptance_check_id": "C-001", "consumer_ids": [], "contract": "ordered integer records"}},
        "input_contract_stage": {"INPUT-001": "S-000"}, "output_contract_stage": {"OUTPUT-001": "S-000"},
        "results": {"E-001": {"content_hash": sha}}, "docs": {"E-001": {"artifact_type": "EVIDENCE", "procedure": "bounded observation", "observation": "fixture input observed"}},
        "immutable_inputs": {}, "plan_required": True, "acyclic": _acyclic, "reachable": _reachable,
    }


def graph_for_context(context):
    """Build a complete test graph from canonical records, without mutating them.

    Canonical producer/consumer contracts must already have identical machine
    semantics/version/format or the fixture must supply a compatibility C.  The
    production validator deliberately does not silently normalize mismatches.
    """
    mech = "MECHANISM-SELECTED"
    graph = {"schema_version": "question-space-architecture-v1", "id": "ARCH-001", "revision": "1", "project_kind": "NEW", "selected_mechanism_id": mech,
             "mechanisms": [{"id": mech, "status": "SELECTED"}], "nodes": [], "edges": [], "contracts": [], "implementation_map": []}
    nodes = {}
    def node(identifier, kind, **extra):
        value = {"id": identifier, "kind": kind, "label": identifier, "subsystem_id": "main", "mechanism_id": mech, "required": "true", **extra}
        nodes[identifier] = value
        return value
    def edge(kind, source, target, **extra):
        identifier = f"EDGE-{len(graph['edges']) + 1:03d}"
        value = {"id": identifier, "kind": kind, "from_id": source, "to_id": target, "meaning": f"{source} {kind} {target}", "scope": "EVIDENCE" if kind == "VERIFIES" else "EXECUTION", "conditions": [], "check_ids": [], **extra}
        graph["edges"].append(value)
        return value
    contracts, signatures = {}, {}
    contract_for_input, contract_for_output = {}, {}
    all_checks = sorted(context.get("checks", {}))
    for direction in ("input", "output"):
        for identifier, canonical in sorted(context.get(direction + "_contracts", {}).items()):
            signature = (canonical.get("version", "1"), canonical.get("format", "application/yaml"), canonical.get("contract"))
            contract_id = signatures.get(signature)
            if contract_id is None:
                contract_id = "CONTRACT-" + str(len(contracts) + 1).zfill(3)
                signatures[signature] = contract_id
                contracts[contract_id] = {"id": contract_id, "version": signature[0], "format": signature[1], "semantics": signature[2], "availability": "canonical plan availability", "external_conditions": [], "error_behavior": "reject invalid payload and retain evidence", "acceptance_check_ids": [], "plan_refs": []}
            contract = contracts[contract_id]
            check = canonical.get("acceptance_check_id")
            if check is None and canonical.get("consumer_ids"):
                check = next((key for key, doc in context.get("checks", {}).items() if doc.get("task_id") in canonical["consumer_ids"]), None)
            if check is None and all_checks:
                check = all_checks[0]
            if check and check not in contract["acceptance_check_ids"]:
                contract["acceptance_check_ids"].append(check)
            if canonical.get("availability_condition") and canonical["availability_condition"] not in contract["external_conditions"]:
                contract["external_conditions"].append(canonical["availability_condition"])
            ref = {"stage_id": context[direction + "_contract_stage"][identifier], "kind": direction.upper(), "contract_id": identifier}
            if direction == "output":
                ref["path"] = canonical["path"]
            contract["plan_refs"].append(ref)
            (contract_for_input if direction == "input" else contract_for_output)[identifier] = contract_id
    for identifier in sorted(context.get("stages", {})):
        node(identifier, "S")
    for identifier, task in sorted(context.get("tasks", {}).items()):
        node(identifier, "T", stage_id=context["task_stage"][identifier])
        component = "ALGORITHM-" + identifier
        node(component, "ALGORITHM")
        edge("IMPLEMENTS", identifier, component)
    for identifier, check in sorted(context.get("checks", {}).items()):
        node(identifier, "C", stage_id=context["check_stage"][identifier], task_id=check["task_id"], **{key: check[key] for key in ("compatibility_contract_ids", "procedure", "expected_observation") if key in check})
    node("CAPABILITY-001", "CAPABILITY")
    for identifier in sorted(context.get("tasks", {})):
        edge("REALIZES", "ALGORITHM-" + identifier, "CAPABILITY-001")
    goals = []
    for index, identifier in enumerate(sorted(context.get("goal_ids", set()))):
        gid = "G-" + str(index + 1).zfill(3)
        node(gid, "G", existing_id=identifier)
        goals.append(gid)
        edge("CONTRIBUTES_TO", "CAPABILITY-001", gid)
    for identifier in sorted(context.get("requirements", set())):
        node(identifier, "R")
        edge("CONTRIBUTES_TO", "CAPABILITY-001", identifier)
    output_nodes, input_nodes = {}, {}
    for identifier, canonical in sorted(context.get("output_contracts", {}).items()):
        data_id = "DATA-" + identifier
        output_nodes[identifier] = data_id
        node(data_id, "DATA", availability="PLANNED", contract_id=contract_for_output[identifier], plan_ref={"stage_id": context["output_contract_stage"][identifier], "output_id": identifier})
        edge("PRODUCES", canonical["producer_task_id"], data_id)
    for identifier, canonical in sorted(context.get("input_contracts", {}).items()):
        availability = canonical["input_kind"]
        data_id = output_nodes.get(canonical.get("output_id")) if availability == "PLANNED" else "DATA-" + identifier
        if data_id is None:
            data_id = "DATA-MISSING-" + identifier
            node(data_id, "DATA", availability="PLANNED", contract_id=contract_for_input[identifier], plan_ref={"stage_id": context["input_contract_stage"][identifier], "output_id": canonical.get("output_id", "MISSING")})
        if availability != "PLANNED":
            fields = {key: canonical[key] for key in ("source_id", "accepted_hash", "version", "source", "owner", "availability_condition", "availability_status", "availability_evidence_id") if key in canonical}
            node(data_id, "DATA", availability=availability, contract_id=contract_for_input[identifier], **fields)
        input_nodes[identifier] = data_id
        for consumer in canonical.get("consumer_ids", []):
            extra = {"contract_id": contract_for_input[identifier], "input_id": identifier}
            if nodes[data_id]["contract_id"] != extra["contract_id"]:
                pair = {nodes[data_id]["contract_id"], extra["contract_id"]}
                check_id = next((key for key, doc in context.get("checks", {}).items() if set(doc.get("compatibility_contract_ids", [])) == pair), None)
                if check_id:
                    extra["compatibility_check_id"] = check_id
            edge("CONSUMES", data_id, consumer, **extra)
    for identifier, task in sorted(context.get("tasks", {}).items()):
        for dependency in task.get("dependencies", []):
            edge("DEPENDS_ON", dependency, identifier)
    for identifier, check in sorted(context.get("checks", {}).items()):
        for input_id in check.get("inputs", []):
            canonical_id = next((key for key, value in context.get("output_contracts", {}).items() if key == input_id or value.get("path") == input_id), None)
            data_id = output_nodes.get(canonical_id) or input_nodes.get(input_id)
            if data_id:
                edge("CONSUMES", data_id, identifier, contract_id=nodes[data_id]["contract_id"], input_id=canonical_id or input_id)
                edge("VERIFIES", identifier, data_id)
        for goal in goals:
            edge("CONTRIBUTES_TO", identifier, goal)
    # Lawful accepted-output admission is distinct from raw production. The
    # producer's own acceptance checks inspect raw data without a self-cycle;
    # downstream tasks and checks are ordered after its canonical acceptance C.
    from plan_semantics import normalize_plan_order, resolve_plan_input
    admissions = set()
    consumed_inputs = list(graph["edges"])
    for collection in ("tasks", "checks"):
        for consumer_id, canonical in context.get(collection, {}).items():
            for reference in canonical.get("inputs", []):
                binding = resolve_plan_input(reference, context.get("input_contracts", {}), context.get("output_contracts", {}))
                data_id = output_nodes.get(binding.get("output_id")) if binding else None
                if data_id:
                    consumed_inputs.append({"kind": "CONSUMES", "from_id": data_id, "to_id": consumer_id})
    for consumed in consumed_inputs:
        if consumed["kind"] != "CONSUMES":
            continue
        data, target = nodes[consumed["from_id"]], nodes[consumed["to_id"]]
        if data.get("availability") != "PLANNED":
            continue
        output = context.get("output_contracts", {}).get(data.get("plan_ref", {}).get("output_id"), {})
        acceptance_id = output.get("acceptance_check_id")
        if target.get("kind") == "C" and context.get("checks", {}).get(target["id"], {}).get("task_id") == output.get("producer_task_id"):
            continue
        pair = (acceptance_id, target["id"])
        if acceptance_id in nodes and pair not in admissions:
            edge("DEPENDS_ON", *pair)
            admissions.add(pair)
    order = normalize_plan_order(context.get("stages", {}), context.get("tasks", {}))
    existing_order = {(item["from_id"], item["to_id"]) for item in graph["edges"] if item["kind"] == "DEPENDS_ON"}
    for pair in order["stage_check_edges"]:
        if pair[0] in nodes and pair[1] in nodes and pair not in existing_order:
            edge("DEPENDS_ON", *pair)
            existing_order.add(pair)
    graph["nodes"] = [nodes[key] for key in sorted(nodes)]
    graph["contracts"] = [contracts[key] for key in sorted(contracts)]
    return graph


class ArchitectureGraphTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture_context()
        self.graph = graph_for_context(self.context)

    def report(self, graph=None, context=None):
        return validate_architecture_graph(graph or self.graph, context or self.context)

    def assert_rejected(self, graph, text):
        report = self.report(graph)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any(text in error for error in report["errors"]), report)

    def test_positive_complete_new_project_is_structural_only(self):
        report = self.report()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["semantic_validity"], "NOT_ASSESSED")
        self.assertEqual(report["implementation_readiness"], "NOT_ASSESSED")
        self.assertIn("G-001", report["constructive_reachable"])
        self.assertEqual(self.report(), report)

    def test_orphan_required_component_and_missing_producer(self):
        graph = copy.deepcopy(self.graph)
        output = next(node for node in graph["nodes"] if node["kind"] == "DATA" and node["availability"] == "PLANNED")
        graph["edges"] = [edge for edge in graph["edges"] if not (edge["kind"] == "PRODUCES" and edge["to_id"] == output["id"])]
        self.assert_rejected(graph, "exactly one producer")
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if edge["kind"] != "IMPLEMENTS"]
        self.assert_rejected(graph, "no implementing canonical task")

    def test_contract_drift_version_compatibility_and_plan_copy_rejected(self):
        graph = copy.deepcopy(self.graph)
        graph["contracts"][0]["semantics"] = "different units"
        self.assert_rejected(graph, "semantics diverges")
        graph = copy.deepcopy(self.graph)
        consumer = next(edge for edge in graph["edges"] if edge["kind"] == "CONSUMES")
        new = copy.deepcopy(graph["contracts"][0]); new["id"] = "CONTRACT-INCOMPATIBLE"; new["version"] = "2"
        graph["contracts"].append(new); consumer["contract_id"] = new["id"]
        self.assert_rejected(graph, "without explicit compatibility check")
        context = copy.deepcopy(self.context)
        context["checks"]["C-001"]["compatibility_contract_ids"] = [graph["contracts"][0]["id"], new["id"]]
        context["input_contracts"]["INPUT-001"]["version"] = "2"
        graph["contracts"][0]["plan_refs"] = [ref for ref in graph["contracts"][0]["plan_refs"] if ref["kind"] != "INPUT"]
        # The canonical consumer now declares v2; the explicit finite C binds
        # producer v1 to consumer v2 instead of equating prose with compatibility.
        consumer["compatibility_check_id"] = "C-001"
        self.assertEqual(self.report(graph, context)["structural_status"], "PASS", self.report(graph, context))

    def test_and_fixedpoint_does_not_treat_one_path_or_condition_as_available(self):
        graph = copy.deepcopy(self.graph)
        data = copy.deepcopy(next(node for node in graph["nodes"] if node.get("availability") == "EXISTING"))
        data.update({"id": "DATA-BLOCKED", "availability": "EXTERNAL", "source": "owner endpoint", "owner": "owner", "availability_condition": "permission delivered", "availability_status": "UNAVAILABLE"})
        for key in ("source_id", "accepted_hash", "version"):
            data.pop(key, None)
        graph["nodes"].append(data)
        edge = copy.deepcopy(next(edge for edge in graph["edges"] if edge["kind"] == "CONSUMES" and edge["to_id"] == "T-001"))
        edge.update({"id": "EDGE-BLOCKED", "from_id": "DATA-BLOCKED", "conditions": ["true"]})
        graph["edges"].append(edge)
        # The added external resource is not the canonical existing input. That
        # remains a model error; its honest unavailability is reported separately.
        self.assert_rejected(graph, "availability differs from canonical input")
        self.assertEqual(self.report(graph)["reachability_status"], "BLOCKED")
        self.assertNotIn("T-001", self.report(graph)["constructive_reachable"])

    def test_future_output_has_no_hash_and_seed_identity_is_exact(self):
        graph = copy.deepcopy(self.graph)
        next(node for node in graph["nodes"] if node.get("availability") == "PLANNED")["accepted_hash"] = "2" * 64
        self.assert_rejected(graph, "must not fabricate")
        graph = copy.deepcopy(self.graph)
        next(node for node in graph["nodes"] if node.get("availability") == "EXISTING")["accepted_hash"] = "2" * 64
        self.assert_rejected(graph, "exact accepted/immutable hash")

    def test_consuming_before_producer_is_rejected(self):
        context = fixture_context()
        context["stages"]["S-001"] = {}
        context["tasks"]["T-002"] = {"id": "T-002", "inputs": ["INPUT-002"], "outputs": ["artifact/second.yaml"], "dependencies": [], "r_refs": ["R-001"]}
        context["task_stage"]["T-002"] = "S-001"
        context["checks"]["C-002"] = {"id": "C-002", "task_id": "T-002", "inputs": ["artifact/second.yaml"], "procedure": "check", "expected_observation": "payload"}
        context["check_stage"]["C-002"] = "S-001"
        context["input_contracts"]["INPUT-002"] = {"id": "INPUT-002", "input_kind": "PLANNED", "producer_task_id": "T-001", "output_id": "OUTPUT-001", "acceptance_check_id": "C-001", "consumer_ids": ["T-002"], "contract": "ordered integer records"}
        context["input_contract_stage"]["INPUT-002"] = "S-001"
        context["output_contracts"]["OUTPUT-002"] = {"id": "OUTPUT-002", "path": "artifact/second.yaml", "producer_task_id": "T-002", "acceptance_check_id": "C-002", "consumer_ids": [], "contract": "ordered integer records"}
        context["output_contract_stage"]["OUTPUT-002"] = "S-001"
        graph = graph_for_context(context)
        report = self.report(graph, context)
        self.assertTrue(any("precedes availability" in error for error in report["errors"]), report)
        context["tasks"]["T-002"]["dependencies"] = ["T-001"]
        graph = graph_for_context(context)
        self.assertEqual(self.report(graph, context)["structural_status"], "PASS", self.report(graph, context))

    def test_research_feedback_allowed_but_provenance_and_execution_cycles_rejected(self):
        graph = copy.deepcopy(self.graph)
        edge = copy.deepcopy(graph["edges"][0]); edge.update({"id": "FEEDBACK-1", "kind": "RESEARCH_FEEDBACK", "scope": "RESEARCH", "from_id": "G-001", "to_id": "DATA-INPUT-001"})
        graph["edges"].append(edge)
        self.assertEqual(self.report(graph)["structural_status"], "PASS")
        edge.update({"kind": "DEPENDS_ON", "scope": "EXECUTION"})
        self.assert_rejected(graph, "cycle")
        graph = copy.deepcopy(self.graph)
        for index, (source, target) in enumerate((("DATA-INPUT-001", "DATA-OUTPUT-001"), ("DATA-OUTPUT-001", "DATA-INPUT-001"))):
            graph["edges"].append({**copy.deepcopy(edge), "id": f"PROV-{index}", "kind": "EVIDENCE_SUPPORT", "scope": "EVIDENCE", "from_id": source, "to_id": target})
        self.assert_rejected(graph, "self-justification cycle")

    def test_requirement_loss_unused_output_and_no_goal_path(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "R-001"]
        graph["edges"] = [edge for edge in graph["edges"] if edge["to_id"] != "R-001"]
        self.assert_rejected(graph, "lost from selected")
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if not (edge["kind"] == "CONSUMES" and edge["to_id"] == "C-001")]
        self.assert_rejected(graph, "produced result is unused")
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if edge["kind"] != "REALIZES"]
        self.assert_rejected(graph, "no complete functional mechanism path")

    def test_generic_dependencies_cannot_fabricate_capability_or_functional_goal_path(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if edge["kind"] != "REALIZES"]
        edge = {"id": "DEPENDENCY-ONLY-CAP", "kind": "REQUIRES", "from_id": "DATA-INPUT-001", "to_id": "CAPABILITY-001", "meaning": "dependency is not a realizer", "scope": "EXECUTION", "conditions": [], "check_ids": []}
        graph["edges"] += [edge, {**edge, "id": "UNRELATED-MECHANISM", "from_id": "ALGORITHM-T-001", "to_id": "G-001"}]
        report = self.report(graph)
        # All nodes are reachable by untyped prerequisites, but typed production
        # and a same-chain functional goal contribution must still be present.
        self.assertIn("G-001", report["constructive_reachable"])
        self.assertTrue(any("no REALIZES producer" in error for error in report["errors"]), report)
        self.assertTrue(any("no complete functional mechanism path" in error for error in report["errors"]), report)
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if not (edge["kind"] == "CONTRIBUTES_TO" and edge["from_id"] == "CAPABILITY-001" and edge["to_id"] == "G-001")]
        graph["edges"].append({**edge, "id": "DEPENDENCY-ONLY-GOAL", "from_id": "CAPABILITY-001", "to_id": "G-001"})
        self.assert_rejected(graph, "no complete functional mechanism path")
        # Restoring the actual typed contribution repairs the same graph.
        graph["edges"][-1]["kind"] = "CONTRIBUTES_TO"
        self.assertEqual(self.report(graph)["structural_status"], "PASS", self.report(graph))

    def test_generic_requirement_dependency_is_not_a_typed_satisfaction(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"] = [edge for edge in graph["edges"] if edge.get("to_id") != "R-001"]
        graph["edges"].append({"id": "DEPENDENCY-ONLY-REQUIREMENT", "kind": "REQUIRES", "from_id": "DATA-INPUT-001", "to_id": "R-001", "meaning": "prerequisite alone does not satisfy a requirement", "scope": "EXECUTION", "conditions": [], "check_ids": []})
        report = self.report(graph)
        self.assertIn("R-001", report["constructive_reachable"])
        self.assert_rejected(graph, "no constructive CONTRIBUTES_TO satisfier")
        # An actual existing resource may satisfy R directly; an arbitrary
        # same-task lineage requirement would incorrectly reject that case.
        graph["edges"][-1]["kind"] = "CONTRIBUTES_TO"
        self.assertEqual(self.report(graph)["structural_status"], "PASS", self.report(graph))

    def test_alternative_is_not_selected_resource(self):
        graph = copy.deepcopy(self.graph)
        graph["mechanisms"].append({"id": "ALTERNATIVE", "status": "ALTERNATIVE"})
        alternative = copy.deepcopy(next(node for node in graph["nodes"] if node["kind"] == "T"))
        alternative.update({"id": "T-ALT", "mechanism_id": "ALTERNATIVE"})
        graph["nodes"].append(alternative)
        self.assertEqual(self.report(graph)["structural_status"], "PASS")
        alternatives = derive_architecture_views(graph)["functional"]["alternatives"]
        self.assertEqual(alternatives[0]["mechanism_id"], "ALTERNATIVE")
        self.assertEqual(alternatives[0]["nodes"][0]["id"], "T-ALT")
        self.assertIn("ALTERNATIVE ALTERNATIVE", render_architecture_mermaid(graph, "functional"))
        edge = copy.deepcopy(graph["edges"][0]); edge.update({"id": "ILLEGAL-ALT", "from_id": "T-ALT"})
        graph["edges"].append(edge)
        self.assert_rejected(graph, "alternative cannot feed")

    def test_views_share_canonical_graph_and_filter_deterministically(self):
        before = architecture_graph_sha256(self.graph)
        views = derive_architecture_views(self.graph)
        self.assertEqual(set(views), {"functional", "data_contracts", "implementation"})
        for name in ("functional", "data", "implementation"):
            rendered = render_architecture_mermaid(self.graph, name)
            self.assertTrue(rendered.startswith("flowchart LR\n"))
            self.assertIn("ALGORITHM-T-001", rendered)
            if name != "functional":
                self.assertIn("DATA-OUTPUT-001", rendered)
        self.assertIn("DATA-OUTPUT-001", {node["id"] for node in views["functional"]["detail"]["nodes"]})
        self.assertNotIn("DATA-OUTPUT-001", {node["id"] for node in views["functional"]["nodes"]})
        data_render = render_architecture_mermaid(self.graph, "data")
        self.assertIn("CONTRACT-001 v1", data_render)
        self.assertNotIn("ordered integer records", data_render)
        card = views["data_contracts"]["contracts"][0]
        self.assertEqual(card["format"], "application/yaml")
        self.assertEqual(card["semantics"], "ordered integer records")
        for descriptor in ("availability", "error_behavior", "acceptance_check_ids"):
            self.assertIn(descriptor, card)
        self.assertIn("subgraph stage0", render_architecture_mermaid(self.graph, "implementation"))
        self.assertEqual(before, architecture_graph_sha256(self.graph))
        self.assertEqual(views, derive_architecture_views(self.graph, subsystem_id="main"))
        self.assertIn("G-001", [node["id"] for node in derive_architecture_views(self.graph, goal_path=True)["functional"]["nodes"]])
        with self.assertRaises(ValueError): render_architecture_mermaid(self.graph, "unknown")
        with self.assertRaises(ValueError): derive_architecture_views(self.graph, subsystem_id="missing")
        with self.assertRaises(ValueError): architecture_impact(self.graph, ["unknown"])

    def test_selective_impact_rechecks_own_task_and_verifier_preserves_other_branch(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"] += [{**copy.deepcopy(node), "id": node["id"] + "-INDEPENDENT", "subsystem_id": "independent"} for node in self.graph["nodes"] if node["kind"] in {"T", "C"}]
        graph["edges"].append({**copy.deepcopy(graph["edges"][0]), "id": "FEEDBACK-IGNORED", "kind": "RESEARCH_FEEDBACK", "scope": "RESEARCH", "from_id": "G-001", "to_id": "T-001-INDEPENDENT"})
        impact = architecture_impact(graph, ["ALGORITHM-T-001"])
        self.assertIn("T-001", impact["affected_task_ids"])
        self.assertIn("C-001", impact["affected_check_ids"])
        self.assertIn("T-001-INDEPENDENT", impact["preserved_task_ids"])
        self.assertIn("C-001-INDEPENDENT", impact["preserved_check_ids"])
        self.assertNotIn("T-001-INDEPENDENT", impact["affected_node_ids"])

    def test_existing_mapping_five_statuses_and_actual_integration_evidence(self):
        graph = copy.deepcopy(self.graph); graph["project_kind"] = "EXISTING"
        self.assert_rejected(graph, "missing from map")
        targets = {node["id"] for node in graph["nodes"] if node["kind"] in {"COMPONENT", "ALGORITHM"}} | {edge["id"] for edge in graph["edges"] if edge["scope"] == "EXECUTION"}
        digest = hashlib.sha256(b"def transform(x): return x\n").hexdigest()
        graph["implementation_map"] = [{"target_id": target, "status": "IMPLEMENTED_UNQUALIFIED", "files": [{"path": "src/transform.py", "sha256": digest}], "evidence_ids": ["E-001"], "evidence_scope": "static actual file observation only", "required_action": "run integration check", "verification_basis": "STATIC_ONLY"} for target in sorted(targets)]
        report = self.report(graph)
        self.assertEqual(report["structural_status"], "PASS", report)
        comparison = compare_architecture_implementation(graph, context=self.context)
        self.assertEqual(len(comparison["discrepancies"]), len(targets))
        entry = graph["implementation_map"][0]; entry["status"] = "IMPLEMENTED_VERIFIED"
        self.assert_rejected(graph, "verified requires integration-check")
        entry["verification_basis"] = "INTEGRATION_CHECK"
        self.assert_rejected(graph, "lacks exact passing integration")
        context = copy.deepcopy(self.context)
        context["docs"]["E-001"]["integration_check"] = {"check_id": "ACTUAL-CHECK-001", "status": "PASS", "target_ids": [entry["target_id"]], "file_hashes": {"src/transform.py": digest}}
        context["implementation_files"] = {"src/transform.py": digest}
        self.assertEqual(self.report(graph, context)["structural_status"], "PASS", self.report(graph, context))
        context["implementation_files"]["src/transform.py"] = "9" * 64
        self.assertTrue(any("observed file hash drift" in error for error in self.report(graph, context)["errors"]))
        graph["implementation_map"].append({**copy.deepcopy(entry), "target_id": "EXTRA-CODE", "status": "EXTRA_UNJUSTIFIED", "verification_basis": "STATIC_ONLY", "required_action": "justify or remove extra"})
        statuses = {row["status"] for row in compare_architecture_implementation(graph, context=context)["discrepancies"]}
        self.assertIn("EXTRA_UNJUSTIFIED", statuses)
        for status in ("PLANNED_ABSENT", "CONTRACT_DIVERGENCE"):
            graph["implementation_map"][1]["status"] = status
            self.assertIn(status, {row["status"] for row in compare_architecture_implementation(graph, context=self.context)["discrepancies"]})

    def test_observed_code_discrepancy_requires_file_and_accepted_evidence(self):
        graph=copy.deepcopy(self.graph);graph['project_kind']='EXISTING'
        targets={n['id'] for n in graph['nodes'] if n['kind'] in {'COMPONENT','ALGORITHM'}}|{e['id'] for e in graph['edges'] if e['scope']=='EXECUTION'}
        graph['implementation_map']=[{'target_id':target,'status':'PLANNED_ABSENT','files':[],'evidence_ids':[],'evidence_scope':'planned item only; no observed file existence claim','required_action':'implement and qualify later','verification_basis':'NONE'} for target in sorted(targets)]
        entry=graph['implementation_map'][0]
        for status in ('IMPLEMENTED_UNQUALIFIED','CONTRACT_DIVERGENCE'):
            entry['status']=status
            self.assert_rejected(graph,'requires exact files and accepted E')
            entry['files']=[{'path':'module.py','sha256':'b'*64}]
            self.assert_rejected(graph,'requires exact files and accepted E')
            entry['evidence_ids']=['E-001']
            self.assertEqual(self.report(graph)['structural_status'],'PASS',self.report(graph))
            entry['files']=[];entry['evidence_ids']=[]
        entry['target_id']='EXTRA-CODE';entry['status']='EXTRA_UNJUSTIFIED'
        self.assert_rejected(graph,'requires exact files and accepted E')

    def test_synthesis_defers_only_canonical_plan_binding(self):
        context = copy.deepcopy(self.context)
        context.update({"stages": {}, "tasks": {}, "checks": {}, "input_contracts": {}, "output_contracts": {}, "task_stage": {}, "check_stage": {}, "plan_required": False})
        self.assertEqual(self.report(context=context)["structural_status"], "PASS", self.report(context=context))
        graph = copy.deepcopy(self.graph)
        next(node for node in graph["nodes"] if node.get("availability") == "PLANNED")["accepted_hash"] = "2" * 64
        self.assertEqual(self.report(graph, context)["structural_status"], "FAIL")

    def test_malformed_nested_metadata_is_addressed_failure(self):
        mutations = [
            lambda g: g.update(nodes=None), lambda g: g.update(edges=None),
            lambda g: g.update(project_kind=[]), lambda g: g.update(selected_mechanism_id={}),
            lambda g: g["nodes"][0].update(kind=[]), lambda g: g["nodes"][0].update(mechanism_id={}),
            lambda g: g["nodes"][0].update(contract_id=[]), lambda g: g["edges"][0].update(from_id=[]),
            lambda g: g["edges"][0].update(scope={}), lambda g: g["edges"][0].update(check_ids=[[]]),
            lambda g: g["contracts"][0].update(plan_refs=[{"stage_id": [], "kind": "INPUT", "contract_id": "INPUT-001"}]),
            lambda g: g["nodes"][0].update(availability_evidence_id=[]),
            lambda g: g["mechanisms"][0].update(status={}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                graph = copy.deepcopy(self.graph); mutation(graph)
                report = self.report(graph)
                self.assertEqual(report["structural_status"], "FAIL")
                self.assertTrue(report["errors"])
                self.assertTrue(all(error.startswith("architecture ") for error in report["errors"]))
        self.assertEqual(compare_architecture_implementation(self.graph, [{"target_id": "x", "status": []}], self.context)["errors"], ["architecture implementation x: status must be string"])


if __name__ == "__main__":
    unittest.main()
