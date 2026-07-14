import unittest

from config import IsolationPolicy
from domain.classification import class_matches, classify_candidate
from domain.enums import ImpactSeverity, IsolationDecision
from domain.models import BBox, DownstreamImpactWarning, IsolationCandidate
from domain.serialization import to_jsonable


class DomainModelTests(unittest.TestCase):
    def test_generic_valve_does_not_match_undefined_valve(self):
        self.assertTrue(class_matches("generic_inline_valve", "valve"))
        self.assertFalse(class_matches("undefined_valve", "valve"))

    def test_candidate_classification_distinguishes_policy_decisions(self):
        automatic = classify_candidate({"entity_class": "gate_valve"}, "Component", IsolationPolicy())
        conditional = classify_candidate({"entity_class": "undefined_valve"}, "Component", IsolationPolicy())
        context = classify_candidate({"entity_class": "locally_mounted_instrument"}, "Component", IsolationPolicy())

        self.assertEqual(automatic.decision, IsolationDecision.AUTOMATIC)
        self.assertTrue(automatic.is_barrier)
        self.assertEqual(conditional.decision, IsolationDecision.CONDITIONAL_MANUAL_REVIEW)
        self.assertFalse(conditional.is_barrier)
        self.assertEqual(context.decision, IsolationDecision.EXCLUDED)

    def test_isolation_candidate_serializes_legacy_policy_fields(self):
        classification = classify_candidate({"entity_class": "gate_valve"}, "Component", IsolationPolicy(), method_text="close and lock valve")
        candidate = IsolationCandidate(
            equipment_tag="T-1",
            source_component_tag="N1_T-1",
            source_component_id="N1",
            candidate_id="V1",
            visual_id="V1",
            candidate_label="Component",
            tag_number="XV-1",
            isolation_method="close and lock valve",
            matched_keywords=("gate_valve",),
            classification=classification,
            traversal_depth=1,
            reason="test",
            properties={"entity_class": "gate_valve"},
            bbox=BBox(1, 2, 3, 4),
        ).to_dict()

        self.assertEqual(candidate["policy_decision"], "automatic")
        self.assertFalse(candidate["requires_manual_review"])
        self.assertEqual(candidate["bbox"], [1, 2, 3, 4])
        self.assertEqual(candidate["classification"]["decision"], "automatic")

    def test_to_jsonable_serializes_enums_and_dataclasses(self):
        warning = DownstreamImpactWarning(
            severity=ImpactSeverity.POSSIBLE,
            source_candidate_id="V1",
            source_tag="N1",
            affected_id="OPC1",
            affected_tag="OPC-1",
            affected_class="off_or_on_page_connector",
            affected_type="endpoint",
            impact_type="loss_of_feed_or_pressure",
            basis="test",
            path_hops=4,
            affected_bbox=BBox(1, 2, 3, 4),
        )

        payload = to_jsonable({"warning": warning, "severity": ImpactSeverity.POSSIBLE})
        self.assertEqual(payload["severity"], "possible")
        self.assertEqual(payload["warning"]["severity"], "possible")
        self.assertEqual(payload["warning"]["affected_bbox"], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
