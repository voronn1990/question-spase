"""QG-01/02 regression on the exact graph audited in the prior role trial.

These checks cover projection topology and Mermaid text, not pixel rendering or
the semantic validity of the prior example's original implementation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from architecture_validation import architecture_graph_sha256, derive_architecture_views, render_architecture_mermaid


class ArchitectureViewsRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads((Path(__file__).parent / "fixtures" / "r421-original-trial-graph.json").read_text(encoding="utf-8"))
        cls.nodes = {node["id"]: node for node in cls.graph["nodes"] if node["mechanism_id"] == cls.graph["selected_mechanism_id"]}
        cls.edges = {edge["id"]: edge for edge in cls.graph["edges"] if edge["from_id"] in cls.nodes and edge["to_id"] in cls.nodes}

    def test_each_subsystem_crossing_has_explicit_original_endpoints(self):
        for subsystem, expected_count in (("runtime", 35), ("acceptance", 114)):
            internal = {key for key, node in self.nodes.items() if node["subsystem_id"] == subsystem}
            expected = {key: edge for key, edge in self.edges.items() if (edge["from_id"] in internal) != (edge["to_id"] in internal)}
            self.assertEqual(len(expected), expected_count)
            for name, view in derive_architecture_views(self.graph, subsystem_id=subsystem).items():
                with self.subTest(subsystem=subsystem, view=name):
                    self.assertEqual(view["boundary_edge_count"], expected_count)
                    self.assertEqual({edge["id"] for edge in view["boundary_edges"]}, set(expected))
                    displayed = {node["id"] for node in view["nodes"]}
                    displayed_edges = {edge["id"] for edge in view["edges"]}
                    self.assertTrue(displayed <= set(self.nodes))
                    self.assertTrue(set(expected) <= displayed_edges)
                    self.assertEqual(set(view["internal_node_ids"]), internal)
                    for edge in view["boundary_edges"]:
                        canonical = expected[edge["id"]]
                        for key, value in canonical.items():
                            self.assertEqual(edge[key], value)
                        self.assertIn(edge["from_id"], displayed)
                        self.assertIn(edge["to_id"], displayed)
                        self.assertIn(edge["inside_node_id"], internal)
                        self.assertNotIn(edge["outside_node_id"], internal)
                        self.assertEqual(edge["direction"], "OUTBOUND" if edge["from_id"] in internal else "INBOUND")
                    rendered = render_architecture_mermaid(self.graph, name, subsystem_id=subsystem)
                    self.assertIn("EXTERNAL PORT:", rendered)
                    self.assertIn("classDef external_port", rendered)
                    for identifier in expected:
                        self.assertIn(identifier, rendered)

    def test_one_hop_ports_do_not_expand_the_whole_external_subsystem(self):
        subsystem = "runtime"
        internal = {key for key, node in self.nodes.items() if node["subsystem_id"] == subsystem}
        expected_nodes = set(internal)
        for edge in self.edges.values():
            if (edge["from_id"] in internal) != (edge["to_id"] in internal):
                expected_nodes.update((edge["from_id"], edge["to_id"]))
        for view in derive_architecture_views(self.graph, subsystem_id=subsystem).values():
            self.assertEqual({node["id"] for node in view["nodes"]}, expected_nodes)
            for edge in view["edges"]:
                self.assertTrue(edge["from_id"] in internal or edge["to_id"] in internal)

    def test_compact_functional_overview_retains_all_details(self):
        view = derive_architecture_views(self.graph)["functional"]
        self.assertEqual(len(view["nodes"]), 13)
        self.assertLess(len(view["edges"]), 40)
        self.assertTrue({node["kind"] for node in view["nodes"]} <= {"G", "R", "CAPABILITY", "COMPONENT", "ALGORITHM"})
        self.assertEqual({node["id"] for node in view["detail"]["nodes"]}, set(self.nodes))
        self.assertEqual({edge["id"] for edge in view["detail"]["edges"]}, set(self.edges))
        self.assertEqual({card["id"]: card for card in view["detail"]["contracts"]}, {card["id"]: card for card in self.graph["contracts"]})
        all_detail_nodes = {node["id"] for node in view["detail"]["nodes"]}
        all_detail_edges = {edge["id"] for edge in view["detail"]["edges"]}
        for alternative in view["alternatives"]:
            all_detail_nodes.update(node["id"] for node in alternative["detail_nodes"])
            all_detail_edges.update(edge["id"] for edge in alternative["detail_edges"])
        self.assertEqual(all_detail_nodes, {node["id"] for node in self.graph["nodes"]})
        self.assertEqual(all_detail_edges, {edge["id"] for edge in self.graph["edges"]})
        self.assertIn("COMP-INGEST", {node["id"] for node in view["nodes"]})
        self.assertIn("ALG-REDUCE", {node["id"] for node in view["nodes"]})

    def test_module_flow_projection_has_replayable_canonical_provenance(self):
        view = derive_architecture_views(self.graph)["functional"]
        derived = [edge for edge in view["edges"] if edge.get("kind") == "VIEW_FLOW"]
        self.assertTrue(derived)
        self.assertIn(("COMP-INGEST", "ALG-REDUCE"), {(edge["from_id"], edge["to_id"]) for edge in derived})
        for edge in derived:
            self.assertEqual(edge["scope"], "DERIVED_VIEW")
            self.assertEqual(edge["derived"], "true")
            self.assertTrue(set(edge["source_edge_ids"]) <= set(self.edges))
            self.assertTrue(edge["paths"])
            for path in edge["paths"]:
                self.assertEqual(path["node_ids"][0], edge["from_id"])
                self.assertEqual(path["node_ids"][-1], edge["to_id"])
                self.assertEqual(len(path["node_ids"]), len(path["steps"]) + 1)
                for source, target, step in zip(path["node_ids"], path["node_ids"][1:], path["steps"]):
                    original = self.edges[step["edge_id"]]
                    if step["direction"] == "REVERSE_OWNERSHIP":
                        self.assertEqual(original["kind"], "IMPLEMENTS")
                        self.assertEqual((source, target), (original["to_id"], original["from_id"]))
                    else:
                        self.assertEqual(step["direction"], "FORWARD")
                        self.assertEqual((source, target), (original["from_id"], original["to_id"]))

    def test_derived_flow_ids_cannot_collide_with_canonical_edge_ids(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["id"] = "VIEW-FLOW-001"
        view = derive_architecture_views(graph)["functional"]
        identifiers = [edge["id"] for edge in view["edges"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        derived = [edge for edge in view["edges"] if edge.get("kind") == "VIEW_FLOW"]
        self.assertTrue(derived)
        self.assertNotIn("VIEW-FLOW-001", {edge["id"] for edge in derived})

    def test_data_view_full_cards_are_exact_and_compact_legend_occurs_once(self):
        view = derive_architecture_views(self.graph)["data_contracts"]
        self.assertEqual(len(view["contracts"]), 14)
        originals = {card["id"]: card for card in self.graph["contracts"]}
        rendered = render_architecture_mermaid(self.graph, "data")
        self.assertEqual(rendered.count("subgraph contract_catalog"), 1)
        for card in view["contracts"]:
            self.assertEqual(card, originals[card["id"]])
            self.assertEqual(rendered.count(f'{card["id"]} v{card["version"]}'), 1)
            self.assertNotIn(card["semantics"], rendered)
        # Frozen audited baseline was 99,769 UTF-8 bytes / 333 lines.  The same
        # topology now labels contracts briefly and stores their full cards once.
        self.assertLess(len(rendered.encode("utf-8")), 99769 // 2)
        self.assertLess(len(rendered.splitlines()), 333)
        self.assertEqual(sum(edge["kind"] == "CONSUMES" for edge in view["edges"]), 88)

    def test_all_views_are_deterministic_and_leave_canonical_graph_intact(self):
        before = copy.deepcopy(self.graph)
        digest = architecture_graph_sha256(self.graph)
        for subsystem in (None, "runtime", "acceptance", "pipeline"):
            for goal_path in (False, True):
                self.assertEqual(derive_architecture_views(self.graph, subsystem_id=subsystem, goal_path=goal_path), derive_architecture_views(self.graph, subsystem_id=subsystem, goal_path=goal_path))
                for name in ("functional", "data", "implementation"):
                    self.assertEqual(render_architecture_mermaid(self.graph, name, subsystem_id=subsystem, goal_path=goal_path), render_architecture_mermaid(self.graph, name, subsystem_id=subsystem, goal_path=goal_path))
        self.assertEqual(self.graph, before)
        self.assertEqual(architecture_graph_sha256(self.graph), digest)


if __name__ == "__main__":
    unittest.main()
