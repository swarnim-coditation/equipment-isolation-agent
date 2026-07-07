import unittest

from impact import analyze_hilt_downstream_impact


def node(node_id, entity_class, tag, entity_type="component", bbox=None):
    payload = {
        "id": node_id,
        "entity_class": entity_class,
        "entity_type": entity_type,
        "attributes": [{"name": "tag", "value": tag}],
    }
    if bbox:
        x, y, w, h = bbox
        payload.update(
            {
                "bounding_box_location": {"x": x + w / 2, "y": y + h / 2},
                "bounding_box_width": w,
                "bounding_box_height": h,
            }
        )
    return {
        "id": node_id,
        "payload": payload,
    }


def line(source, target, flow="ONE_WAY", arrow=True, entity_class="primary_process_line"):
    payload = {
        "entity_class": entity_class,
        "flow": flow,
        "from": source,
        "to": target,
    }
    if arrow:
        payload["arrow"] = [{"from_id": source, "to_id": target}]
    return {"source": source, "target": target, "payload": payload}


def validation(candidates, barrier_ids=None):
    return {
        "candidates": candidates,
        "isolation_validation": {
            "barrier_candidate_ids": barrier_ids if barrier_ids is not None else [c["candidate_id"] for c in candidates]
        },
    }


def candidate(candidate_id, source="N1_FT18", role="outlet", tag="XV-1"):
    return {
        "candidate_id": candidate_id,
        "visual_id": candidate_id,
        "tag_number": tag,
        "source_component_tag": source,
        "source_flow_role": role,
        "properties": {"entity_class": "gate_valve"},
    }


class DownstreamImpactTests(unittest.TestCase):
    def test_one_way_closure_reaches_downstream_pump_as_likely(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    node("N1", "equipment_nozzle", "N1_FT18"),
                    node("V1", "gate_valve", "XV-1"),
                    node("P1", "centrifugal_pump", "PT-19", entity_type="equipment", bbox=[100, 200, 30, 40]),
                ],
                "links": [
                    line("N1", "V1", flow="UNKNOWN_FLOW", arrow=False),
                    line("V1", "P1"),
                ],
            }
        }
        result = analyze_hilt_downstream_impact(payload, validation([candidate("V1")]), equipment_tag="FT-18")
        warnings = result["warnings"]

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["severity"], "likely")
        self.assertEqual(warnings[0]["affected_tag"], "PT-19")
        self.assertEqual(warnings[0]["affected_type"], "equipment")
        self.assertEqual(warnings[0]["affected_bbox"], [100, 200, 30, 40])

    def test_unknown_flow_branch_emits_possible(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    node("N1", "equipment_nozzle", "N1_FT18"),
                    node("V1", "gate_valve", "XV-1"),
                    node("F1", "locally_mounted_instrument", "FI-101"),
                    node("L1", "junction", "J1"),
                    node("LI1", "junction", "LI-77"),
                ],
                "links": [
                    line("N1", "V1", flow="UNKNOWN_FLOW", arrow=False),
                    line("V1", "F1"),
                    line("V1", "L1", flow="UNKNOWN_FLOW", arrow=False),
                    line("L1", "LI1", flow="UNKNOWN_FLOW", arrow=False),
                ],
            }
        }
        result = analyze_hilt_downstream_impact(payload, validation([candidate("V1")]), equipment_tag="FT-18")
        severities_by_tag = {item["affected_tag"]: item["severity"] for item in result["warnings"]}

        self.assertEqual(severities_by_tag["FI-101"], "likely")
        self.assertEqual(severities_by_tag["LI-77"], "possible")

    def test_traversal_stops_at_selected_isolation_barriers(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    node("N1", "equipment_nozzle", "N1_FT18"),
                    node("V1", "gate_valve", "XV-1"),
                    node("V2", "gate_valve", "XV-2"),
                    node("P1", "centrifugal_pump", "PT-19", entity_type="equipment"),
                ],
                "links": [
                    line("N1", "V1", flow="UNKNOWN_FLOW", arrow=False),
                    line("V1", "V2"),
                    line("V2", "P1"),
                ],
            }
        }
        result = analyze_hilt_downstream_impact(
            payload,
            validation([candidate("V1"), candidate("V2", source="N2_FT18", tag="XV-2")]),
            equipment_tag="FT-18",
        )

        self.assertFalse(
            [
                item
                for item in result["warnings"]
                if item["source_candidate_id"] == "V1" and item["affected_tag"] == "PT-19"
            ]
        )

    def test_instrument_prefix_and_endpoint_are_classified(self):
        payload = {
            "hilt_graph": {
                "nodes": [
                    node("N1", "equipment_nozzle", "N1_FT18"),
                    node("V1", "gate_valve", "XV-1"),
                    node("A1", "junction", "LAH-10"),
                    node("S1", "sta", "STA-1"),
                    node("O1", "off_or_on_page_connector", "OPC-1"),
                ],
                "links": [
                    line("N1", "V1", flow="UNKNOWN_FLOW", arrow=False),
                    line("V1", "A1"),
                    line("A1", "S1"),
                    line("V1", "O1"),
                ],
            }
        }
        result = analyze_hilt_downstream_impact(payload, validation([candidate("V1")]), equipment_tag="FT-18")
        by_tag = {item["affected_tag"]: item for item in result["warnings"]}

        self.assertEqual(by_tag["LAH-10"]["affected_type"], "instrument_or_control_loop")
        self.assertEqual(by_tag["STA-1"]["affected_type"], "endpoint")
        self.assertEqual(by_tag["OPC-1"]["affected_type"], "endpoint")


if __name__ == "__main__":
    unittest.main()
