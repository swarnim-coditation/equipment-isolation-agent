"""Characterization tests for the agent's stage summarizers.

The ``_summarize_*`` functions compact heavy pipeline dicts into the small
payloads Gemini actually sees. They are pure (dict in, dict out) but had no test
coverage, and they are what a shared-pipeline-stage extraction is most likely to
perturb. These pin their key sets and truncation limits so the summaries stay
stable when the functions move to their own module.
"""
import unittest

from agent.summaries import (
    _looks_like_uuid,
    _short,
    _summarize_boundary,
    _summarize_candidates,
    _summarize_payload,
    _summarize_validation,
    _tag,
)

UUID = "12345678-1234-1234-1234-123456789abc"


class HelperTests(unittest.TestCase):
    def test_tag_prefers_the_first_populated_known_key(self):
        self.assertEqual(_tag({"tag_number": "V-1", "name": "other"}), "V-1")
        self.assertEqual(_tag({"name": "N-2"}), "N-2")
        self.assertEqual(_tag({}), "")

    def test_tag_skips_uuid_shaped_values(self):
        self.assertEqual(_tag({"tag_number": UUID, "label": "V-9"}), "V-9")

    def test_looks_like_uuid_matches_only_the_8_4_4_4_12_shape(self):
        self.assertTrue(_looks_like_uuid(UUID))
        self.assertFalse(_looks_like_uuid("V-1"))
        self.assertFalse(_looks_like_uuid(""))
        self.assertFalse(_looks_like_uuid(None))

    def test_short_truncates_with_ellipsis_at_the_limit(self):
        self.assertEqual(_short("abc"), "abc")
        self.assertEqual(_short("", 10), "")
        self.assertEqual(len(_short("x" * 500)), 240)
        self.assertTrue(_short("x" * 500).endswith("..."))
        self.assertEqual(_short("x" * 500, 10), "x" * 7 + "...")


class SummarizeBoundaryTests(unittest.TestCase):
    def test_key_set_is_stable(self):
        self.assertEqual(
            sorted(_summarize_boundary({})),
            [
                "boundary_source_count",
                "boundary_sources",
                "cnvrt_project_id",
                "collection_id",
                "component_count",
                "components",
                "fatal",
                "job_id",
                "job_name",
                "job_resolution",
                "job_resolution_error",
                "matched_equipment_count",
                "message",
                "pnid_names",
                "traversal_limit_hit",
            ],
        )

    def test_components_and_sources_are_capped_at_25_but_counts_are_not(self):
        boundary = {
            "components": [{"id": i, "label": "c", "properties": {"tag_number": f"C-{i}"}} for i in range(40)],
            "component_boundaries": [
                {"component": {"id": i, "label": "s", "properties": {"tag_number": f"S-{i}"}}} for i in range(40)
            ],
        }
        summary = _summarize_boundary({"equipment_boundaries": [boundary]})
        self.assertEqual(summary["component_count"], 40)
        self.assertEqual(summary["boundary_source_count"], 40)
        self.assertEqual(len(summary["components"]), 25)
        self.assertEqual(len(summary["boundary_sources"]), 25)

    def test_fatal_is_always_a_bool(self):
        self.assertIs(_summarize_boundary({})["fatal"], False)
        self.assertIs(_summarize_boundary({"debug": {"fatal": "yes"}})["fatal"], True)

    def test_nozzle_is_read_from_either_property_spelling(self):
        def source(props):
            return {"equipment_boundaries": [{"component_boundaries": [{"component": {"properties": props}}]}]}

        self.assertEqual(_summarize_boundary(source({"Nozzle Id": "N1"}))["boundary_sources"][0]["nozzle"], "N1")
        self.assertEqual(_summarize_boundary(source({"nozzle_id": "N2"}))["boundary_sources"][0]["nozzle"], "N2")
        self.assertEqual(_summarize_boundary(source({}))["boundary_sources"][0]["nozzle"], "")


class SummarizeCandidatesTests(unittest.TestCase):
    def test_key_set_is_stable(self):
        self.assertEqual(
            sorted(_summarize_candidates({})),
            ["all_before_ranking", "candidates", "raw_before_dedupe", "skipped", "total_candidates"],
        )

    def test_candidate_preview_shape(self):
        summary = _summarize_candidates(
            {
                "candidates": [
                    {
                        "tag_number": "V-1",
                        "properties": {"entity_class": "gate_valve"},
                        "isolation_method": "close",
                        "traversal_depth": 2,
                        "source_component_tag": "N1",
                        "bbox": [1, 2, 3, 4],
                    }
                ]
            }
        )
        self.assertEqual(
            summary["candidates"][0],
            {
                "tag": "V-1",
                "class": "gate_valve",
                "method": "close",
                "depth": 2,
                "source": "N1",
                "bbox_resolved": True,
            },
        )

    def test_bbox_resolved_is_false_when_bbox_missing_or_empty(self):
        summary = _summarize_candidates({"candidates": [{"bbox": []}, {}]})
        self.assertFalse(summary["candidates"][0]["bbox_resolved"])
        self.assertFalse(summary["candidates"][1]["bbox_resolved"])

    def test_candidate_previews_are_not_truncated(self):
        summary = _summarize_candidates({"candidates": [{"tag_number": f"V-{i}"} for i in range(40)]})
        self.assertEqual(len(summary["candidates"]), 40)


class SummarizeValidationTests(unittest.TestCase):
    def test_key_set_is_stable(self):
        self.assertEqual(
            sorted(_summarize_validation({})),
            [
                "assurance_status",
                "authoritative",
                "candidate_count",
                "covered_boundary_source_count",
                "expected_boundary_count",
                "missing_boundary_count",
                "missing_evidence",
                "rationale",
                "terminal",
                "unresolved_evidence_checks",
            ],
        )

    def test_authoritative_is_always_true(self):
        # validate() is the sole authority for assurance_status; the summary must
        # never suggest otherwise.
        self.assertIs(_summarize_validation({})["authoritative"], True)

    def test_unresolved_checks_are_flattened_to_names(self):
        summary = _summarize_validation(
            {"isolation_validation": {"unresolved_evidence_checks": [{"check_name": "a"}, {"check_name": "b"}]}}
        )
        self.assertEqual(summary["unresolved_evidence_checks"], ["a", "b"])

    def test_assurance_status_is_read_from_the_top_level_not_the_nested_block(self):
        summary = _summarize_validation(
            {"assurance_status": "isolated", "isolation_validation": {"assurance_status": "ignored"}}
        )
        self.assertEqual(summary["assurance_status"], "isolated")


class SummarizePayloadTests(unittest.TestCase):
    def test_key_set_is_stable(self):
        self.assertEqual(
            sorted(_summarize_payload({})),
            ["assurance_status", "isolation_points", "isolation_points_count", "job_id", "job_name"],
        )

    def test_empty_payload_does_not_raise(self):
        self.assertEqual(_summarize_payload({})["isolation_points_count"], 0)

    def test_points_are_capped_at_20_but_the_count_is_not(self):
        payload = {"data": [{"isolation_points": [{"uuid": str(i)} for i in range(35)]}]}
        summary = _summarize_payload(payload)
        self.assertEqual(summary["isolation_points_count"], 35)
        self.assertEqual(len(summary["isolation_points"]), 20)


if __name__ == "__main__":
    unittest.main()
