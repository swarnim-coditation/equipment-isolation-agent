import unittest

from validator import validate


class ValidatorTests(unittest.TestCase):
    def test_manual_review_does_not_mask_missing_barrier_boundary(self):
        result = validate(
            {
                "candidates": [{"candidate_id": "manual-1"}],
                "evidence_state": {
                    "barrier_candidate_ids": [],
                    "manual_review_candidate_ids": ["manual-1"],
                    "missing_boundary_count": 1,
                    "missing_evidence": ["Selected conditional isolation candidate(s) require manual review before acceptance."],
                },
                "required_evidence_checks": [],
            }
        )

        validation = result["isolation_validation"]
        self.assertEqual(validation["assurance_status"], "not_isolated")
        self.assertTrue(validation["terminal"])
        self.assertIn("No selected candidate has deterministic isolation barrier evidence", validation["rationale"])

    def test_manual_review_downgrades_only_after_barrier_coverage_exists(self):
        result = validate(
            {
                "candidates": [{"candidate_id": "manual-1"}],
                "evidence_state": {
                    "barrier_candidate_ids": ["manual-1"],
                    "manual_review_candidate_ids": ["manual-1"],
                    "missing_boundary_count": 0,
                    "missing_evidence": ["Selected conditional isolation candidate(s) require manual review before acceptance."],
                },
                "required_evidence_checks": [],
            }
        )

        validation = result["isolation_validation"]
        self.assertEqual(validation["assurance_status"], "provisional_unproven_isolation")
        self.assertFalse(validation["terminal"])
        self.assertIn("manual review", validation["rationale"])


if __name__ == "__main__":
    unittest.main()
