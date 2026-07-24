"""Characterization tests for the final UI payload builder.

``build_final_payload`` is pipeline stage 13 and produces exactly the object the
golden harness compares. It had no test coverage, and the golden harness needs a
live JanusGraph, so this is the only offline guard on the payload envelope.
"""
import unittest

from config import RunConfig
from output import build_final_payload


def _config(**overrides):
    """RunConfig.context is a computed property, so drive it via real fields."""
    return RunConfig(
        **{
            "equipment_tag": "BT-11",
            "job_name": "pnid_2_bio_final",
            "job_id": "2100",
            "collection_id": "206",
            "collection_name": "coll",
            **overrides,
        }
    )


class PayloadEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.config = _config()

    def test_envelope_shape(self):
        payload = build_final_payload({}, self.config)
        self.assertEqual(sorted(payload), ["data", "debug", "error", "message"])
        self.assertIs(payload["error"], False)
        self.assertEqual(payload["message"], "Completed")
        self.assertEqual(len(payload["data"]), 1)

    def test_debug_envelope_is_passed_through_untouched(self):
        debug = {"bbox_resolved_count": 4, "planner_mode": "deterministic"}
        payload = build_final_payload({"debug": debug}, self.config)
        self.assertEqual(payload["debug"], debug)

    def test_debug_defaults_to_empty_dict(self):
        self.assertEqual(build_final_payload({}, self.config)["debug"], {})

    def test_record_carries_the_expected_field_set(self):
        record = build_final_payload({}, self.config)["data"][0]
        self.assertEqual(
            sorted(record),
            [
                "assurance_status",
                "boundary_context_sources",
                "collection_id",
                "collection_name",
                "context_instruments",
                "detected_isolation_schemes",
                "downstream_impact",
                "input_details",
                "instrument_context",
                "isolated_envelope",
                "isolation_obligations",
                "isolation_points",
                "isolation_validation",
                "job_id",
                "job_name",
                "manual_visual_isolation_checks",
                "project_id",
                "project_name",
                "relief_candidates",
                "secondary_energy_context",
                "selected_equipment",
                "selected_equipment_overlays",
                "unselected_boundary_sources",
            ],
        )

    def test_numeric_context_ids_are_coerced_to_int(self):
        record = build_final_payload({}, self.config)["data"][0]
        self.assertEqual(record["job_id"], 2100)
        self.assertEqual(record["collection_id"], 206)

    def test_non_numeric_context_ids_pass_through_unchanged(self):
        config = _config(job_id="not-a-number", collection_id="")
        record = build_final_payload({}, config)["data"][0]
        self.assertEqual(record["job_id"], "not-a-number")
        self.assertEqual(record["collection_id"], "")

    def test_selected_equipment_comes_from_config(self):
        record = build_final_payload({}, self.config)["data"][0]
        self.assertEqual(record["selected_equipment"], ["BT-11"])
        self.assertEqual(record["input_details"]["target_mode"], "selected_equipment")

    def test_validation_context_overrides_config_context(self):
        record = build_final_payload({"context": {"job_id": 999}}, self.config)["data"][0]
        self.assertEqual(record["job_id"], 999)


class IsolationPointTests(unittest.TestCase):
    def setUp(self):
        self.config = _config()

    def test_candidate_is_projected_with_stable_keys(self):
        record = build_final_payload(
            {
                "candidates": [
                    {
                        "candidate_id": 7,
                        "equipment_tag": "BT-11",
                        "bbox": [1, 2, 3, 4],
                        "properties": {"entity_class": "gate_valve"},
                        "tag_number": "V-1",
                        "source_component_tag": "N1",
                        "reason": "adjacent",
                    }
                ]
            },
            self.config,
        )["data"][0]

        self.assertEqual(len(record["isolation_points"]), 1)
        point = record["isolation_points"][0]
        self.assertEqual(point["uuid"], "7")  # always stringified
        self.assertEqual(point["entity_class"], "gate_valve")
        self.assertEqual(point["bbox"], [1, 2, 3, 4])
        self.assertEqual(point["energy_type"], "process")  # default
        self.assertIn("Candidate vertex id: 7", point["reason"])

    def test_entity_class_falls_back_to_candidate_label(self):
        record = build_final_payload(
            {"candidates": [{"candidate_id": 1, "candidate_label": "ball_valve"}]},
            self.config,
        )["data"][0]
        self.assertEqual(record["isolation_points"][0]["entity_class"], "ball_valve")

    def test_isolation_action_fields_are_derived_from_entity_class(self):
        record = build_final_payload(
            {"candidates": [{"candidate_id": 1, "properties": {"entity_class": "gate_valve"}}]},
            self.config,
        )["data"][0]
        point = record["isolation_points"][0]
        self.assertIn("operation_kind", point)
        self.assertIn("positive_isolation_requires_field_confirmation", point)

    def test_explicit_downstream_impact_beats_the_validation_copy(self):
        record = build_final_payload(
            {"downstream_impact": {"from": "validation"}},
            self.config,
            downstream_impact={"from": "argument"},
        )["data"][0]
        self.assertEqual(record["downstream_impact"], {"from": "argument"})

    def test_downstream_impact_falls_back_to_validation_data(self):
        record = build_final_payload({"downstream_impact": {"from": "validation"}}, self.config)["data"][0]
        self.assertEqual(record["downstream_impact"], {"from": "validation"})


if __name__ == "__main__":
    unittest.main()
