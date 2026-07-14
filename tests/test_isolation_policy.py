import json
import tempfile
import unittest
from pathlib import Path

from bbox import (
    _hilt_context_sources,
    _hilt_nodes_by_id,
    _image_dimensions,
    _resolve_candidate_bboxes,
    _selectable_candidate_pool,
    _select_visually_nearest_per_source,
)
from candidates import find_candidates
from config import IsolationPolicy, RunConfig, apply_project_profile, load_project_profile
from evidence import build_evidence
from hilt_topology import resolve_nozzle_isolation, resolve_source_branch_isolation
from validator import validate


class IsolationPolicyTests(unittest.TestCase):
    def test_undefined_valve_is_selected_with_manual_review_policy(self):
        result = find_candidates(_boundary_with_candidate("undefined_valve"), IsolationPolicy())
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(len(result["_candidate_pool"]), 1)
        self.assertEqual(result["_candidate_pool"][0]["policy_decision"], "conditional_manual_review")
        self.assertEqual(result["_candidate_pool"][0]["classification"]["decision"], "conditional_manual_review")
        self.assertTrue(result["_candidate_pool"][0]["requires_manual_review"])
        self.assertEqual(result["candidates"][0]["policy_decision"], "conditional_manual_review")
        self.assertTrue(result["candidates"][0]["requires_manual_review"])

    def test_conditional_valve_can_be_enabled_explicitly(self):
        policy = IsolationPolicy(include_conditional_candidates=True)
        result = find_candidates(_boundary_with_candidate("undefined_valve"), policy)
        self.assertEqual(result["total_candidates"], 1)
        self.assertIn("undefined_valve", result["candidates"][0]["matched_keywords"])
        self.assertEqual(result["candidates"][0]["classification"]["decision"], "conditional_manual_review")

    def test_profile_policy_is_loaded_from_project_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project_config.json"
            path.write_text(
                json.dumps(
                    {
                        "active_profile": "demo",
                        "isolation_policy": {
                            "eligible_classes": ["gate_valve"],
                            "conditional_classes": ["undefined_valve"],
                            "include_conditional_candidates": True,
                        },
                        "profiles": {"demo": {"unigraph_project_id": "13"}},
                    }
                ),
                encoding="utf-8",
            )
            profile = load_project_profile(str(path), "")
        config = apply_project_profile(RunConfig(equipment_tag="T-1"), profile)

        self.assertEqual(config.policy.eligible_classes, ("gate_valve",))
        self.assertEqual(config.policy.conditional_classes, ("undefined_valve",))
        self.assertTrue(config.policy.include_conditional_candidates)

    def test_hilt_topology_uses_policy_for_valve_classes(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("N1", "equipment_nozzle", "N1_T-1"),
                    _hilt_node("U1", "undefined_valve", "UV-1"),
                    _hilt_node("G1", "gate_valve", "GV-1"),
                ],
                "links": [
                    _hilt_link("N1", "U1"),
                    _hilt_link("U1", "G1"),
                ],
            }
        }

        default_result = resolve_nozzle_isolation(payload, "T-1", policy=IsolationPolicy())
        self.assertEqual(default_result["N1_T-1"][0]["valve_id"], "U1")
        self.assertEqual(default_result["N1_T-1"][0]["entity_class"], "undefined_valve")

        conditional_result = resolve_nozzle_isolation(
            payload,
            "T-1",
            policy=IsolationPolicy(include_conditional_candidates=True),
        )
        self.assertEqual(conditional_result["N1_T-1"][0]["valve_id"], "U1")

    def test_hilt_source_uuid_branch_topology_selects_each_branch_valve(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("S1", "equipment_nozzle", ""),
                    _hilt_node("T1", "tee", ""),
                    _hilt_node("J1", "junction", ""),
                    _hilt_node("V1", "undefined_valve", ""),
                    _hilt_node("V2", "undefined_valve", ""),
                ],
                "links": [
                    _hilt_link("S1", "T1"),
                    _hilt_link("T1", "V1"),
                    _hilt_link("T1", "J1"),
                    _hilt_link("J1", "V2"),
                ],
            }
        }

        result = resolve_source_branch_isolation(
            payload,
            [{"equipment_tag": "T-1", "source_component_id": "SRC1", "source_visual_id": "S1"}],
            policy=IsolationPolicy(),
        )

        branches = result[0]["branches"]
        self.assertEqual([branch["status"] for branch in branches], ["isolated", "isolated"])
        self.assertEqual({branch["valve"]["valve_id"] for branch in branches}, {"V1", "V2"})

    def test_hilt_source_uuid_branch_topology_continues_past_check_valve(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("S1", "equipment_nozzle", ""),
                    _hilt_node("C1", "check_valve", ""),
                    _hilt_node("V1", "gate_valve", ""),
                ],
                "links": [
                    _hilt_link("S1", "C1"),
                    _hilt_link("C1", "V1"),
                ],
            }
        }

        result = resolve_source_branch_isolation(
            payload,
            [{"equipment_tag": "T-1", "source_component_id": "SRC1", "source_visual_id": "S1"}],
            policy=IsolationPolicy(),
        )

        branch = result[0]["branches"][0]
        self.assertEqual(branch["status"], "isolated")
        self.assertEqual(branch["valve"]["valve_id"], "V1")
        self.assertEqual(branch["context_devices"][0]["valve_id"], "C1")

    def test_hilt_source_uuid_branch_topology_marks_check_only_branch_unresolved(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("S1", "equipment_nozzle", ""),
                    _hilt_node("C1", "check_valve", ""),
                    _hilt_node("E1", "end_cap", ""),
                ],
                "links": [
                    _hilt_link("S1", "C1"),
                    _hilt_link("C1", "E1"),
                ],
            }
        }

        result = resolve_source_branch_isolation(
            payload,
            [{"equipment_tag": "T-1", "source_component_id": "SRC1", "source_visual_id": "S1"}],
            policy=IsolationPolicy(),
        )

        branch = result[0]["branches"][0]
        self.assertEqual(branch["status"], "unresolved")
        self.assertEqual(branch["context_devices"][0]["valve_id"], "C1")

    def test_hilt_branch_topology_skips_context_sources(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("S1", "equipment_nozzle", ""),
                    _hilt_node("V1", "gate_valve", ""),
                ],
                "links": [_hilt_link("S1", "V1")],
            }
        }

        result = resolve_source_branch_isolation(
            payload,
            [
                {
                    "equipment_tag": "T-1",
                    "source_component_id": "SRC1",
                    "source_visual_id": "S1",
                    "source_type": "instrument_context",
                }
            ],
            policy=IsolationPolicy(),
        )

        self.assertEqual(result, [])

    def test_graph_only_process_line_near_instrument_is_context_source(self):
        candidate_pool = [
            {
                "equipment_tag": "T-1",
                "source_component_id": "SRC1",
                "source_component_tag": "SRC1",
                "source_display_label": "",
                "source_label_confidence": "graph_only_unlabeled_component",
                "source_bbox": [100, 100, 10, 10],
                "source_hilt_lines": [{"entity_class": "main_process_line", "entity_type": "process_line"}],
                "candidate_id": "V1",
            }
        ]
        symbols = [{"entity_class": "locally_mounted_instrument", "tag": "LT-100", "bbox": [130, 100, 20, 20]}]

        sources, items = _hilt_context_sources(candidate_pool, symbols)

        self.assertEqual(sources, {("T-1", "SRC1")})
        self.assertEqual(items[0]["classification"], "instrument_adjacent_context")

    def test_named_process_line_near_instrument_stays_process_source(self):
        candidate_pool = [
            {
                "equipment_tag": "T-1",
                "source_component_id": "N1",
                "source_component_tag": "N1_T-1",
                "source_display_label": "N1_T-1",
                "source_label_confidence": "visible_nozzle_text",
                "source_bbox": [100, 100, 10, 10],
                "source_hilt_lines": [{"entity_class": "main_process_line", "entity_type": "process_line"}],
                "candidate_id": "V1",
            }
        ]
        symbols = [{"entity_class": "locally_mounted_instrument", "tag": "LI-100", "bbox": [130, 100, 20, 20]}]

        sources, items = _hilt_context_sources(candidate_pool, symbols)

        self.assertEqual(sources, set())
        self.assertEqual(items, [])

    def test_bbox_visual_selection_pool_includes_conditional_candidates_for_manual_review(self):
        pool = [
            {"candidate_id": "auto", "policy_decision": "automatic"},
            {"candidate_id": "manual", "policy_decision": "conditional_manual_review", "requires_manual_review": True},
        ]

        selectable = _selectable_candidate_pool(pool, IsolationPolicy())
        self.assertEqual([item["candidate_id"] for item in selectable], ["auto", "manual"])

        selectable_with_conditional = _selectable_candidate_pool(
            pool,
            IsolationPolicy(include_conditional_candidates=True),
        )
        self.assertEqual([item["candidate_id"] for item in selectable_with_conditional], ["auto", "manual"])

    def test_candidate_bbox_falls_back_to_exact_hilt_uuid(self):
        hilt = {
            "hilt_graph": {
                "nodes": [
                    _hilt_node("SRC1", "equipment_nozzle", ""),
                    _hilt_node("V1", "gate_valve", "XV-1"),
                ],
                "links": [],
            }
        }
        hilt_nodes = _hilt_nodes_by_id(hilt, y_flip=100)

        candidates, resolved = _resolve_candidate_bboxes(
            [
                {
                    "candidate_id": "V1",
                    "visual_id": "V1",
                    "source_visual_id": "SRC1",
                    "source_component_id": "SRC1",
                }
            ],
            {},
            {},
            hilt_nodes,
        )

        self.assertEqual(resolved, 1)
        self.assertEqual(candidates[0]["bbox"], [8, 88, 4, 4])
        self.assertEqual(candidates[0]["source_bbox"], [8, 88, 4, 4])
        self.assertEqual(candidates[0]["bbox_match_method"], "hilt_uuid")

    def test_png_dimensions_are_parsed_without_pillow(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (123).to_bytes(4, "big") + (456).to_bytes(4, "big")
        self.assertEqual(_image_dimensions(png), (123, 456))

    def test_conditional_only_source_is_selected_for_manual_review(self):
        all_pool = [
            {
                "equipment_tag": "T-1",
                "source_component_id": "N1",
                "source_component_tag": "N1_T-1",
                "candidate_id": "manual",
                "policy_decision": "conditional_manual_review",
                "requires_manual_review": True,
                "traversal_depth": 1,
                "bbox": [1, 2, 3, 4],
                "source_bbox": [5, 6, 7, 8],
            }
        ]

        selected, debug = _select_visually_nearest_per_source(all_pool, all_pool)

        self.assertEqual([item["candidate_id"] for item in selected], ["manual"])
        self.assertEqual(debug["bbox_unselected_source_component_count"], 0)

    def test_selected_conditional_candidate_creates_validation_hold(self):
        candidate = find_candidates(_boundary_with_candidate("undefined_valve"), IsolationPolicy())["candidates"][0]
        data = build_evidence({"candidates": [candidate], "debug": {}}, RunConfig(equipment_tag="T-1"))
        validation = validate({**data, "required_evidence_checks": []})

        self.assertEqual(data["evidence_state"]["manual_review_candidate_ids"], ["V1"])
        self.assertIn("require manual review", data["missing_evidence"][-1])
        self.assertEqual(validation["assurance_status"], "provisional_unproven_isolation")


def _boundary_with_candidate(entity_class):
    return {
        "equipment_boundaries": [
            {
                "equipment": {"id": "EQ1", "label": "Equipment", "properties": {"tag": "T-1"}},
                "component_boundaries": [
                    {
                        "component": {"id": "N1", "properties": {"tag": "N1_T-1"}},
                        "direct_neighbors": [
                            {
                                "id": "V1",
                                "label": "Component",
                                "traversal_depth": 1,
                                "properties": {"entity_class": entity_class, "tag": "V-1"},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _hilt_node(node_id, entity_class, tag):
    return {
        "id": node_id,
        "payload": {
            "id": node_id,
            "entity_class": entity_class,
            "attributes": [{"name": "tag", "value": tag}],
            "bounding_box_location": {"x": 10, "y": 10},
            "bounding_box_width": 4,
            "bounding_box_height": 4,
        },
    }


def _hilt_link(source, target):
    return {
        "source": source,
        "target": target,
        "payload": {"entity_class": "primary_process_line", "from": source, "to": target},
    }


if __name__ == "__main__":
    unittest.main()
