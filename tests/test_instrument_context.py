import unittest
from types import SimpleNamespace

from instrument_context import analyze_hilt_instrument_context, parse_instrument_tag
from loto import build_loto_procedure


class InstrumentContextTests(unittest.TestCase):
    def test_parse_level_and_pressure_tags(self):
        li = parse_instrument_tag("LI-OGHC20CL002")
        lt = parse_instrument_tag("LT-OGHC20CL001")
        lic = parse_instrument_tag("LIC")
        self.assertEqual(li["prefix"], "LI")
        self.assertEqual(li["measured_variable"], "level")
        self.assertEqual(lt["instrument_type"], "transmitter")
        self.assertEqual(lic["instrument_type"], "controller")

    def test_hilt_connected_instrument_is_relevant(self):
        result = analyze_hilt_instrument_context(
            hilt_payload(
                nodes=[
                    node("EQ1", "equipment", "vessel", "T-100"),
                    node("N1", "piping_component", "equipment_nozzle", "N1_T-100"),
                    node("PI1", "instrument", "locally_mounted_instrument", "PI-100"),
                    node("LI1", "instrument", "locally_mounted_instrument", "LI-100"),
                ],
                links=[
                    link("EQ1", "N1", "primary_process_line"),
                    link("N1", "PI1", "piping_to_instrument_line"),
                    link("N1", "LI1", "piping_to_instrument_line"),
                ],
            ),
            stlm_payload={},
            equipment_tag="T-100",
            validation_data={"candidates": []},
        )

        self.assertEqual(result["status"], "completed")
        tags = {item["tag"] for item in result["instruments"]}
        self.assertEqual(tags, {"PI-100", "LI-100"})
        self.assertIn("before_isolation", result["checks"])
        self.assertIn("stored_energy_relief", result["checks"])
        self.assertIn("verification_before_work", result["checks"])
        pressure_checks = [
            check
            for checks in result["checks"].values()
            for check in checks
            if check.get("tag") == "PI-100"
        ]
        self.assertTrue(any("zero gauge pressure" in check.get("interpretation", "") for check in pressure_checks))

    def test_loto_uses_instrument_context_without_changing_assurance(self):
        instrument_context = {
            "status": "completed",
            "policy": "advisory_only",
            "instruments": [],
            "checks": {
                "before_isolation": [
                    {"instrument_id": "PI1", "tag": "PI-100", "action": "Record baseline pressure reading at PI-100 before shutdown/isolation."}
                ],
                "stored_energy_relief": [
                    {"instrument_id": "PI1", "tag": "PI-100", "action": "Monitor pressure trend at PI-100 while bleeding/venting."}
                ],
                "verification_before_work": [
                    {"instrument_id": "PI1", "tag": "PI-100", "action": "Use PI-100 as supporting indication only; field-verify zero energy by an approved method."}
                ],
                "restoration_reenergization": [
                    {"instrument_id": "PI1", "tag": "PI-100", "action": "Before and after controlled re-energization, confirm PI-100 is within expected safe operating range."}
                ],
            },
        }
        procedure = build_loto_procedure(
            {
                "candidates": [],
                "assurance_status": "not_isolated",
                "isolation_validation": {"missing_evidence": []},
                "instrument_context": instrument_context,
            },
            SimpleNamespace(
                equipment_tag="T-100",
                work_scope=SimpleNamespace(
                    intrusive_work=True,
                    confined_space_entry=False,
                    hot_work=False,
                    high_risk_service=True,
                ),
            ),
        )

        actions = [step["action"] for step in procedure["ordered_steps"]]
        self.assertIn("not_isolated", procedure["assurance_status"])
        self.assertTrue(any("PI-100" in action for action in actions))
        self.assertTrue(any(step.get("advisory") for step in procedure["ordered_steps"]))
        self.assertIn("Instrument context is advisory", " ".join(actions))
        self.assertIn("PI-100", procedure["release_note"])


def hilt_payload(nodes, links):
    return {"hilt_graph": {"nodes": nodes, "links": links}}


def node(node_id, entity_type, entity_class, tag):
    return {
        "id": node_id,
        "payload": {
            "id": node_id,
            "entity_type": entity_type,
            "entity_class": entity_class,
            "attributes": [{"name": "tag", "value": tag}],
            "bounding_box_location": {"x": 100, "y": 100},
            "bounding_box_width": 20,
            "bounding_box_height": 20,
        },
    }


def link(source, target, entity_class):
    return {
        "source": source,
        "target": target,
        "payload": {
            "entity_class": entity_class,
            "from": source,
            "to": target,
        },
    }


if __name__ == "__main__":
    unittest.main()
