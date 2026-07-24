import unittest

from secondary_context import build_secondary_energy_context


class SecondaryContextTests(unittest.TestCase):
    def test_nearby_candidate_ids_resolve_to_candidate_summaries(self):
        context = build_secondary_energy_context(
            {
                "candidates": [
                    {
                        "candidate_id": "valve-1",
                        "tag_number": "XV-1",
                        "candidate_label": "gate_valve",
                        "properties": {"entity_class": "gate_valve"},
                        "bbox": [10, 20, 30, 40],
                    }
                ],
                "boundary_context_sources": [
                    {
                        "source_component": "src-1",
                        "source_component_tag_raw": "L6",
                        "source_hilt_lines": [{"entity_class": "companion_line"}],
                        "nearby_candidate_ids": ["valve-1", "missing-valve"],
                    }
                ],
            }
        )

        item = context["items"][0]
        self.assertEqual(item["nearby_candidate_ids"], ["valve-1", "missing-valve"])
        self.assertEqual(len(item["nearby_candidates"]), 1)
        self.assertEqual(item["nearby_candidates"][0]["uuid"], "valve-1")
        self.assertEqual(item["nearby_candidates"][0]["operation_kind"], "valve_isolation")


if __name__ == "__main__":
    unittest.main()
