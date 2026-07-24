"""Characterization tests for the duplicated geometry/attribute helpers.

Several helpers exist in near-identical copies across bbox.py, impact.py,
instrument_context.py, obligations.py, relief.py, hilt_topology.py, flow.py and
viewer.py. Some copies are genuinely equivalent; others differ in ways that are
invisible at a glance (``None`` vs ``""`` on miss, int-truncation vs float math,
lower vs upper case). Consolidating a divergent pair silently changes behavior.

These tests pin each family against a shared edge-case table BEFORE any merge, so
"equivalent" is proven rather than assumed. Where copies genuinely differ, the
difference is asserted explicitly — those assertions are the record of why the
copies must NOT be merged.
"""
import unittest

import bbox
import bbox_geometry
import hilt_index
import impact
import instrument_context
import obligations
import relief
import viewer
from domain.topology import normalize_tag, tag_prefix

# Shared edge cases for bbox-shaped inputs.
BBOX_CASES = [
    None,
    [],
    [1, 2, 3],
    [1, 2, 3, 4, 5],
    [0, 0, 10, 10],
    [5, 5, 1, 1],
    [-5, -5, 10, 10],
    [0, 0, 0, 10],
    [0, 0, 10, 0],
    [0, 0, -3, 5],
    ["1", "2", "3", "4"],
    [1.9, 2.9, 3.9, 4.9],
    (1, 2, 3, 4),
    "not-a-bbox",
    {"x": 1},
]

ATTR_CASES = [
    (None, "tag"),
    ([], "tag"),
    ([{"name": "tag", "value": "V-1"}], "tag"),
    ([{"name": "TAG", "value": "V-1"}], "tag"),
    ([{"name": " tag ", "value": "V-1"}], "TAG"),
    ([{"name": "tag", "value": ""}], "tag"),
    ([{"name": "tag", "value": None}], "tag"),
    ([{"name": "tag", "value": []}], "tag"),
    ([{"name": "tag", "value": 0}], "tag"),
    ([{"name": "other", "value": "x"}], "tag"),
    (["not-a-dict", {"name": "tag", "value": "V-2"}], "tag"),
    ([{"name": "tag", "value": "first"}, {"name": "tag", "value": "second"}], "tag"),
]

TEXT_CASES = [
    None,
    [],
    [{"value": "A"}],
    [{"value": "A"}, {"value": "B"}],
    [{"value": ""}],
    [{"value": None}],
    [{"value": []}],
    [{"value": 0}],
    ["not-a-dict"],
    ["not-a-dict", {"value": "A"}],
    [{"no_value": "x"}],
]


class ValidBboxTests(unittest.TestCase):
    """instrument_context and obligations agree; viewer deliberately does not."""

    def test_instrument_context_and_obligations_are_equivalent(self):
        for case in BBOX_CASES:
            with self.subTest(case=case):
                self.assertEqual(
                    instrument_context._valid_bbox(case),
                    obligations._valid_bbox(case),
                )

    def test_viewer_accepts_zero_and_negative_area_that_others_reject(self):
        # viewer._valid_bbox has NO positivity check. Merging it into the shared
        # implementation would silently drop overlays from rendered HTML.
        for case in ([0, 0, 0, 10], [0, 0, 10, 0], [0, 0, -3, 5]):
            with self.subTest(case=case):
                self.assertEqual(instrument_context._valid_bbox(case), [])
                self.assertNotEqual(viewer._valid_bbox(case), [])

    def test_viewer_agrees_with_the_others_on_positive_area(self):
        for case in ([0, 0, 10, 10], [5, 5, 1, 1], [-5, -5, 10, 10], ["1", "2", "3", "4"]):
            with self.subTest(case=case):
                self.assertEqual(viewer._valid_bbox(case), instrument_context._valid_bbox(case))


class BboxNearTests(unittest.TestCase):
    """bbox and instrument_context are NOT interchangeable. Do not merge."""

    def test_int_truncation_diverges_on_fractional_boxes(self):
        # instrument_context truncates via _valid_bbox before centering; bbox does
        # float math. Here the centers land either side of the outer edge:
        #   bbox: centre of [0.5, 0, 3, 3] -> (2.0, 1.5)   -> inside x>=2
        #   i_c : truncated to [0, 0, 3, 3] -> (1.5, 1.5)  -> outside x>=2
        inner = [0.5, 0, 3, 3]
        outer = [2, 0, 10, 10]
        self.assertTrue(bbox_geometry._bbox_near(inner, outer, padding=0))
        self.assertFalse(instrument_context._bbox_near(inner, outer, padding=0))

    def test_bbox_rejects_tuples_that_instrument_context_accepts(self):
        inner = (0, 0, 4, 4)
        outer = (0, 0, 10, 10)
        self.assertFalse(bbox_geometry._bbox_near(inner, outer, padding=0))
        self.assertTrue(instrument_context._bbox_near(inner, outer, padding=0))

    def test_bbox_accepts_zero_area_that_instrument_context_rejects(self):
        self.assertTrue(bbox_geometry._bbox_near([0, 0, 0, 0], [0, 0, 10, 10]))
        self.assertFalse(instrument_context._bbox_near([0, 0, 0, 0], [0, 0, 10, 10]))

    def test_they_agree_on_plain_positive_integer_boxes(self):
        cases = [
            ([0, 0, 4, 4], [0, 0, 10, 10], 0, True),
            ([100, 100, 4, 4], [0, 0, 10, 10], 0, False),
            ([100, 100, 4, 4], [0, 0, 10, 10], 200, True),
        ]
        for inner, outer, padding, expected in cases:
            with self.subTest(inner=inner, padding=padding):
                self.assertIs(bbox_geometry._bbox_near(inner, outer, padding), expected)
                self.assertIs(instrument_context._bbox_near(inner, outer, padding), expected)


class AttrTests(unittest.TestCase):
    """Four copies return None on miss; relief and instrument_context return ''."""

    def test_none_returning_copies_are_equivalent(self):
        for attributes, name in ATTR_CASES:
            with self.subTest(attributes=attributes, name=name):
                self.assertEqual(
                    hilt_index._attr_value(attributes, name),
                    impact._attr(attributes, name),
                )

    def test_empty_string_copies_are_equivalent_to_each_other(self):
        for attributes, name in ATTR_CASES:
            with self.subTest(attributes=attributes, name=name):
                self.assertEqual(
                    relief._attr(attributes, name),
                    instrument_context._attr(attributes, name),
                )

    def test_the_two_families_differ_only_by_the_miss_sentinel(self):
        # This is the precise contract that makes an `or ""` shim safe.
        for attributes, name in ATTR_CASES:
            with self.subTest(attributes=attributes, name=name):
                none_style = hilt_index._attr_value(attributes, name)
                empty_style = relief._attr(attributes, name)
                self.assertEqual(none_style if none_style is not None else "", empty_style)

    def test_miss_sentinels_are_actually_different(self):
        self.assertIsNone(hilt_index._attr_value([], "tag"))
        self.assertEqual(relief._attr([], "tag"), "")


class HiltTextValueTests(unittest.TestCase):
    """bbox/impact return None on empty; instrument_context and relief return ''."""

    def test_none_returning_copies_are_equivalent(self):
        for case in TEXT_CASES:
            with self.subTest(case=case):
                self.assertEqual(hilt_index._hilt_text_value(case), impact._hilt_text_value(case))

    def test_empty_string_copies_are_equivalent_to_each_other(self):
        for case in TEXT_CASES:
            with self.subTest(case=case):
                self.assertEqual(instrument_context._hilt_text_value(case), relief._text(case))

    def test_the_two_families_differ_only_by_the_empty_sentinel(self):
        for case in TEXT_CASES:
            with self.subTest(case=case):
                none_style = hilt_index._hilt_text_value(case)
                empty_style = instrument_context._hilt_text_value(case)
                self.assertEqual(none_style if none_style is not None else "", empty_style)

    def test_empty_sentinels_are_actually_different(self):
        self.assertIsNone(hilt_index._hilt_text_value([]))
        self.assertEqual(instrument_context._hilt_text_value([]), "")


class TagPrefixTests(unittest.TestCase):
    """NOT duplication: each copy is paired with its own differently-cased set."""

    def test_case_differs_between_the_two_copies(self):
        self.assertEqual(tag_prefix("PI-100"), "pi")
        self.assertEqual(impact._tag_prefix("PI-100"), "PI")

    def test_each_copy_matches_its_own_lookup_set(self):
        # bbox's set is lowercase; impact's is uppercase. Swapping the functions
        # would make both lookups miss.
        self.assertIn(tag_prefix("FIC-1"), bbox.INSTRUMENT_TAG_PREFIXES)
        self.assertIn(impact._tag_prefix("FIC-1"), impact.INSTRUMENT_PREFIXES)
        self.assertNotIn(impact._tag_prefix("FIC-1"), bbox.INSTRUMENT_TAG_PREFIXES)


class NormTests(unittest.TestCase):
    """Six copies delegate to normalize_tag; candidates and run do NOT."""

    NORM_CASES = ["N-1", "n 1", "N_1", " N1 ", "", None, 0, "Valve-A B"]

    def test_the_six_normalize_tag_shims_are_all_equivalent(self):
        import flow
        import hilt_topology

        shims = [
            bbox._norm,
            impact._norm,
            instrument_context._norm,
            obligations._norm,
            flow._norm,
            hilt_topology._norm,
        ]
        for case in self.NORM_CASES:
            expected = normalize_tag(case)
            for shim in shims:
                with self.subTest(case=case, shim=shim.__module__):
                    self.assertEqual(shim(case), expected)

    def test_run_norm_is_a_different_function(self):
        import run

        # normalize_tag folds separators; run._norm does not. Merging would change
        # job-name / unit-name matching.
        self.assertEqual(normalize_tag("N-1"), normalize_tag("n 1"))
        self.assertNotEqual(run._norm("N-1"), run._norm("n 1"))

    def test_candidates_norm_normalizes_class_not_tag(self):
        import candidates
        from domain.classification import normalize_class

        for case in ("Ball Valve", "check-valve", None):
            with self.subTest(case=case):
                self.assertEqual(candidates._norm(case), normalize_class(case))


if __name__ == "__main__":
    unittest.main()
