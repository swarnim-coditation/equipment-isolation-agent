import tempfile
import unittest
from pathlib import Path

from output import write_viewer
from viewer import render_viewer_html


def payload(**data_overrides):
    data = {
        "assurance_status": "provisional_unproven_isolation",
        "selected_equipment": ["AP001"],
        "selected_equipment_overlays": [],
        "isolation_points": [],
        "manual_visual_isolation_checks": [],
        "boundary_context_sources": [],
        "downstream_impact": {},
    }
    data.update(data_overrides)
    return {"data": [data]}


class ViewerTests(unittest.TestCase):
    def test_target_only_overlay_scrolls_to_target_bbox(self):
        html = render_viewer_html(
            payload(
                selected_equipment_overlays=[
                    {
                        "uuid": "eq-1",
                        "tag": "AP001",
                        "entity_class": "centrifugal_pump",
                        "bbox": [1000, 900, 50, 50],
                    }
                ]
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("target-box", html)
        self.assertIn('data-scroll-x="820"', html)
        self.assertIn('data-scroll-y="720"', html)
        self.assertNotIn('data-scroll-y="850"', html)

    def test_plain_english_status_callout_renders(self):
        html = render_viewer_html(
            payload(
                assurance_status="not_isolated",
                isolation_obligations={
                    "status": "completed",
                    "summary": {
                        "process_obligation_count": 2,
                        "isolated_count": 1,
                        "unresolved_count": 1,
                        "manual_candidate_count": 0,
                    },
                    "items": [],
                },
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("Not isolated with current evidence", html)
        self.assertIn("1 process path still needs a selected isolation point", html)
        self.assertIn("status-not-isolated", html)

    def test_possible_endpoint_with_valid_bbox_renders_impact_overlay(self):
        html = render_viewer_html(
            payload(
                downstream_impact={
                    "status": "completed",
                    "warnings": [
                        {
                            "severity": "possible",
                            "source_tag": "N2_AP001",
                            "affected_tag": "OPC-1",
                            "affected_id": "impact-1",
                            "affected_class": "off_or_on_page_connector",
                            "affected_type": "endpoint",
                            "affected_bbox": [300, 400, 20, 20],
                            "path_hops": 5,
                        }
                    ],
                }
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("impact-box impact-possible", html)
        self.assertIn("Impact 1 endpoint", html)

    def test_downstream_warning_without_bbox_stays_in_sop_but_not_overlay(self):
        html = render_viewer_html(
            payload(
                downstream_impact={
                    "status": "completed",
                    "warnings": [
                        {
                            "severity": "possible",
                            "source_tag": "N2_AP001",
                            "affected_tag": "OPC-1",
                            "affected_class": "off_or_on_page_connector",
                            "affected_type": "endpoint",
                            "affected_bbox": [],
                            "path_hops": 5,
                        }
                    ],
                }
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("Downstream impact (May affect)", html)
        self.assertNotIn('class="impact-box', html)

    def test_mixed_primary_overlays_have_expected_box_count(self):
        html = render_viewer_html(
            payload(
                selected_equipment_overlays=[{"uuid": "eq-1", "tag": "AP001", "bbox": [10, 10, 20, 20]}],
                isolation_points=[{"uuid": "valve-1", "tag_number": "XV-1", "bbox": [40, 40, 20, 20]}],
                downstream_impact={
                    "status": "completed",
                    "warnings": [
                        {
                            "severity": "likely",
                            "source_tag": "N1_AP001",
                            "affected_tag": "P-1",
                            "affected_id": "pump-1",
                            "affected_class": "centrifugal_pump",
                            "affected_type": "equipment",
                            "affected_bbox": [80, 80, 20, 20],
                        }
                    ],
                },
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("Boxes: 3.", html)

    def test_instrument_context_renders_panel_and_overlay(self):
        html = render_viewer_html(
            payload(
                loto_procedure={
                    "ordered_steps": [
                        {
                            "phase": 1,
                            "ref": "1910.147(d)(1)",
                            "title": "Preparation for shutdown",
                            "action": "Record baseline pressure reading at PI-100 before shutdown/isolation.",
                            "purpose": "Establish initial pressure condition before isolation.",
                            "interpretation": "Baseline pressure helps compare depressurization trend after shutdown and relief.",
                            "acceptance_criteria": "Record value before changing isolation state.",
                            "limitation": "Baseline reading is context, not proof of isolation.",
                        }
                    ]
                },
                instrument_context={
                    "status": "completed",
                    "policy": "advisory_only",
                    "instruments": [
                        {
                            "id": "pi-1",
                            "tag": "PI-100",
                            "name": "pressure indicator",
                            "measured_variable": "pressure",
                            "instrument_type": "local_indicator",
                            "bbox": [120, 130, 20, 20],
                            "verification_note": "supporting only",
                        }
                    ],
                    "checks": {
                        "before_isolation": [
                            {
                                "tag": "PI-100",
                                "action": "Record baseline pressure reading at PI-100 before shutdown/isolation.",
                                "purpose": "Establish initial pressure condition before isolation.",
                                "interpretation": "Baseline pressure helps compare depressurization trend after shutdown and relief.",
                                "acceptance_criteria": "Record value before changing isolation state.",
                                "limitation": "Baseline reading is context, not proof of isolation.",
                            }
                        ],
                        "restoration_reenergization": [
                            {"tag": "PI-100", "action": "Before and after controlled re-energization, confirm PI-100 is within expected safe operating range."}
                        ],
                    },
                }
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertNotIn("Instrument Checks", html)
        self.assertIn("PI-100", html)
        self.assertIn("Meaning", html)
        self.assertIn("Baseline pressure helps compare", html)
        self.assertIn("step-detail", html)
        self.assertIn("instrument-box", html)

    def test_obligation_manual_candidate_renders_orange_overlay_and_coverage(self):
        html = render_viewer_html(
            payload(
                isolation_obligations={
                    "status": "completed",
                    "summary": {
                        "process_obligation_count": 1,
                        "isolated_count": 1,
                        "unresolved_count": 0,
                        "manual_candidate_count": 1,
                    },
                    "items": [
                        {
                            "source_component": "src-1",
                            "source_component_tag": "N1_AP001",
                            "source_type": "process",
                            "status": "isolated",
                            "selected_candidate_ids": ["valve-1"],
                            "manual_candidates": [
                                {
                                    "uuid": "valve-2",
                                    "bbox": [140, 160, 20, 20],
                                    "entity_class": "gate_valve",
                                    "reason": "Additional same-source candidate.",
                                }
                            ],
                        }
                    ],
                }
            ),
            image_url="file:///tmp/pid.png",
        )

        self.assertIn("manual-box", html)
        self.assertIn("manual isolation check", html)
        self.assertIn("Isolation Coverage", html)
        self.assertIn("Manual bypass/parallel-route check required", html)

    def test_output_write_viewer_writes_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "viewer.html"
            write_viewer(path, payload(), image_url="")
            text = path.read_text(encoding="utf-8")

        self.assertIn("<!doctype html>", text)
        self.assertIn("Equipment Isolation Overlay", text)


if __name__ == "__main__":
    unittest.main()
