import unittest

from config import RunConfig
from relief import analyze_isolation_schemes_and_relief


class ReliefAndSchemeTests(unittest.TestCase):
    def test_single_block_scheme_detected(self):
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("V1", "gate_valve"),
                    node("P1", "pipe"),
                ],
                links=[link("S1", "V1"), link("V1", "P1")],
                candidates=[candidate("S1", "V1", ["S1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        schemes = result["detected_isolation_schemes"]["items"]
        self.assertEqual(schemes[0]["scheme_type"], "single block")
        self.assertEqual(schemes[0]["barrier_ids"], ["V1"])

    def test_double_block_with_bleed_detected(self):
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("V1", "gate_valve"),
                    node("D1", "drain_valve", tag="DRAIN-1"),
                    node("V2", "gate_valve"),
                ],
                links=[link("S1", "V1"), link("V1", "D1"), link("D1", "V2")],
                candidates=[candidate("S1", "V1", ["S1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        scheme = result["detected_isolation_schemes"]["items"][0]
        relief = result["relief_candidates"]["items"][0]
        self.assertEqual(scheme["scheme_type"], "double block with bleed")
        self.assertEqual(scheme["barrier_ids"], ["V1", "V2"])
        self.assertEqual(scheme["relief_candidate_ids"], ["D1"])
        self.assertEqual(relief["relief_type"], "drain")
        self.assertEqual(relief["classified_by"], "deterministic")

    def test_second_block_across_tee_is_not_double_block(self):
        # V1 is the first block; a tee (3 process connections) sits between it and V2,
        # so V2 is on a parallel branch, not a proven in-series second block.
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("V1", "gate_valve"),
                    node("T1", "tee"),
                    node("V2", "gate_valve"),
                    node("P2", "pipe"),
                ],
                links=[link("S1", "V1"), link("V1", "T1"), link("T1", "V2"), link("T1", "P2")],
                candidates=[candidate("S1", "V1", ["S1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        scheme = result["detected_isolation_schemes"]["items"][0]
        self.assertEqual(scheme["scheme_type"], "single block")
        self.assertEqual(scheme["barrier_ids"], ["V1"])  # V2 is NOT a mandatory barrier
        self.assertEqual([d["id"] for d in scheme["unverified_additional_blocks"]], ["V2"])

    def test_second_block_behind_check_valve_is_not_double_block(self):
        # A check valve is directional and not a lockable manual block, so a block
        # reached through one is not a proven double block.
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("V1", "gate_valve"),
                    node("C1", "check_valve"),
                    node("V2", "gate_valve"),
                ],
                links=[link("S1", "V1"), link("V1", "C1"), link("C1", "V2")],
                candidates=[candidate("S1", "V1", ["S1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        scheme = result["detected_isolation_schemes"]["items"][0]
        self.assertEqual(scheme["scheme_type"], "single block")
        self.assertEqual(scheme["barrier_ids"], ["V1"])
        self.assertEqual([d["id"] for d in scheme["unverified_additional_blocks"]], ["V2"])

    def test_straight_series_second_block_still_double_block(self):
        # No junction between V1 and V2 -> still a genuine double block.
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("V1", "gate_valve"),
                    node("V2", "gate_valve"),
                ],
                links=[link("S1", "V1"), link("V1", "V2")],
                candidates=[candidate("S1", "V1", ["S1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        scheme = result["detected_isolation_schemes"]["items"][0]
        self.assertEqual(scheme["scheme_type"], "double block")
        self.assertEqual(scheme["barrier_ids"], ["V1", "V2"])
        self.assertNotIn("unverified_additional_blocks", scheme)

    def test_instrument_inside_envelope_is_not_relief_candidate(self):
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("PI1", "pressure_indicator", tag="PI-100"),
                    node("V1", "gate_valve"),
                ],
                links=[link("S1", "PI1"), link("PI1", "V1")],
                candidates=[candidate("S1", "V1", ["S1", "PI1", "V1"])],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        self.assertEqual(result["relief_candidates"]["items"], [])

    def test_positive_isolation_scheme_detected(self):
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("B1", "blind"),
                ],
                links=[link("S1", "B1")],
                candidates=[candidate("S1", "B1", ["S1", "B1"], entity_class="blind")],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        self.assertEqual(result["detected_isolation_schemes"]["items"][0]["scheme_type"], "positive isolation")

    def test_positive_isolation_does_not_emit_second_block_device(self):
        # A blind is complete positive isolation; a downstream valve must NOT be added
        # as a separate "close & lock" scheme device (only B1 is a barrier).
        result = analyze_isolation_schemes_and_relief(
            data(
                nodes=[
                    node("S1", "equipment_nozzle"),
                    node("B1", "blind"),
                    node("V1", "gate_valve"),
                ],
                links=[link("S1", "B1"), link("B1", "V1")],
                candidates=[candidate("S1", "B1", ["S1", "B1"], entity_class="blind")],
            ),
            RunConfig(equipment_tag="T-1"),
        )

        scheme = result["detected_isolation_schemes"]["items"][0]
        self.assertEqual(scheme["scheme_type"], "positive isolation")
        self.assertEqual(scheme["barrier_ids"], ["B1"])
        self.assertNotIn("unverified_additional_blocks", scheme)


def data(nodes, links, candidates):
    return {
        "_hilt_payload": {"hilt_graph": {"nodes": nodes, "links": links}},
        "candidates": candidates,
        "debug": {"hilt_y_flip_calibrated": 1000},
    }


def candidate(source, barrier, path, entity_class="gate_valve"):
    return {
        "candidate_id": barrier,
        "source_visual_id": source,
        "source_component_id": source,
        "source_component_tag": source,
        "branch_path_node_ids": path,
        "candidate_label": entity_class,
        "properties": {"entity_class": entity_class, "entity_type": "piping_component"},
    }


def node(node_id, entity_class, tag=""):
    return {
        "id": node_id,
        "payload": {
            "id": node_id,
            "entity_class": entity_class,
            "entity_type": "piping_component",
            "attributes": [{"name": "tag", "value": tag}] if tag else [],
            "bounding_box_location": {"x": 100, "y": 100},
            "bounding_box_width": 20,
            "bounding_box_height": 20,
        },
    }


def link(source, target):
    return {
        "source": source,
        "target": target,
        "payload": {
            "id": f"{source}-{target}",
            "entity_class": "main_process_line",
            "entity_type": "process_line",
        },
    }


if __name__ == "__main__":
    unittest.main()
