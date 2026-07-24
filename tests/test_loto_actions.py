import unittest

from config import RunConfig
from loto import build_loto_procedure


def candidate(candidate_id, entity_class, source="N1", tag=None):
    return {
        "equipment_tag": "P3",
        "candidate_id": candidate_id,
        "candidate_label": entity_class,
        "tag_number": tag,
        "properties": {"entity_class": entity_class},
        "source_component_tag": source,
        "source_flow_role": "inlet",
        "traversal_depth": 1,
        "bbox": [10, 10, 20, 20],
    }


class LotoActionTests(unittest.TestCase):
    def test_flange_is_positive_hold_not_close_lock_valve(self):
        procedure = build_loto_procedure(
            {
                "assurance_status": "provisional_unproven_isolation",
                "candidates": [candidate("flange-1", "flange", source="P2A")],
                "isolation_validation": {},
            },
            RunConfig(equipment_tag="P3"),
        )

        actions = [step["action"] for step in procedure["ordered_steps"]]
        self.assertTrue(any("Field-verify flange/line-break point" in action for action in actions))
        self.assertTrue(any("approved blind/spade" in action for action in actions))
        self.assertFalse(any("Close & lock flange" in action for action in actions))
        device = procedure["phases"][2]["field_confirmed_positive_devices"][0]
        self.assertEqual(device["operation_kind"], "field_confirmed_positive_isolation")
        self.assertTrue(device["positive_isolation_requires_field_confirmation"])

    def test_check_valve_does_not_close_and_lock(self):
        procedure = build_loto_procedure(
            {
                "assurance_status": "not_isolated",
                "candidates": [candidate("check-1", "check_valve", source="N1", tag="CHK-1")],
                "isolation_validation": {},
            },
            RunConfig(equipment_tag="P3"),
        )

        actions = [step["action"] for step in procedure["ordered_steps"]]
        self.assertTrue(any("no isolation devices identified" in action for action in actions))
        self.assertFalse(any("Close & lock CHK-1" in action for action in actions))

    def test_valve_still_closes_and_locks(self):
        procedure = build_loto_procedure(
            {
                "assurance_status": "provisional_unproven_isolation",
                "candidates": [candidate("valve-1", "gate_valve", source="N1", tag="XV-1")],
                "isolation_validation": {},
            },
            RunConfig(equipment_tag="P3"),
        )

        actions = [step["action"] for step in procedure["ordered_steps"]]
        self.assertTrue(any("Close & lock XV-1" in action for action in actions))

    def test_companion_line_context_adds_phase_one_review_step(self):
        procedure = build_loto_procedure(
            {
                "assurance_status": "provisional_unproven_isolation",
                "candidates": [candidate("valve-1", "gate_valve", source="N1")],
                "isolation_validation": {
                    "boundary_context_sources": [
                        {
                            "source_component": "409704",
                            "source_component_tag": "unlabeled graph-only source",
                            "source_component_tag_raw": "L6",
                            "classification": "companion_line_context",
                            "source_hilt_lines": [{"entity_class": "companion_line"}],
                            "reason": "HILT graph connects this source through a companion line.",
                        }
                    ]
                },
            },
            RunConfig(equipment_tag="P3"),
        )

        actions = [step["action"] for step in procedure["ordered_steps"]]
        self.assertTrue(any("Review secondary/context line L6" in action for action in actions))
        context_steps = [step for step in procedure["ordered_steps"] if step.get("secondary_context_tag") == "L6"]
        self.assertEqual(context_steps[0]["secondary_context_line_class"], "companion_line")
        self.assertTrue(context_steps[0]["advisory"])


if __name__ == "__main__":
    unittest.main()
