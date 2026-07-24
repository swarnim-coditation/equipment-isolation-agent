"""The run's equipment tag is authoritative; the model cannot redirect it.

``t_fetch_boundary`` used to do ``equipment_tag or session.config.equipment_tag``,
so a model-supplied tag silently won. Observed live: the model passed "BT 11" and
"BT11" for a run configured as "BT-11". The boundary came back empty, which reads
as missing data rather than as the model analysing the wrong equipment.

run.py cannot do this -- it always uses config.equipment_tag. These tests pin the
agent to the same guarantee.
"""
import unittest
from unittest import mock

from config import RunConfig


class _Session:
    def __init__(self, tag):
        self.config = RunConfig(equipment_tag=tag)
        self.boundary_data = None


def _fetch(session, **kwargs):
    """Call t_fetch_boundary with the graph and job resolution stubbed out."""
    import agent.tools as tools

    with mock.patch.object(tools, "fetch_boundaries", return_value={"equipment_boundaries": []}), \
         mock.patch.object(tools, "resolve_job_from_boundary", side_effect=lambda c, d: (c, {})):
        return tools.t_fetch_boundary(session, **kwargs)


class EquipmentTagScopeTests(unittest.TestCase):
    def test_config_tag_is_used_when_the_model_supplies_nothing(self):
        session = _Session("BT-11")
        summary = _fetch(session)
        self.assertEqual(session.config.equipment_tag, "BT-11")
        self.assertNotIn("ignored_equipment_tag", summary)

    def test_a_conflicting_model_tag_is_ignored_not_obeyed(self):
        session = _Session("BT-11")
        summary = _fetch(session, equipment_tag="P-99")
        self.assertEqual(session.config.equipment_tag, "BT-11")
        self.assertEqual(summary["ignored_equipment_tag"], "P-99")
        self.assertIn("BT-11", summary["note"])

    def test_the_observed_live_failures_are_handled(self):
        # "BT 11" normalizes equal to "BT-11" (a formatting variant, tolerated).
        # "BT11" does not, so it is reported. Either way the run stays on BT-11.
        for supplied, expect_reported in (("BT 11", False), ("BT11", True)):
            with self.subTest(supplied=supplied):
                session = _Session("BT-11")
                summary = _fetch(session, equipment_tag=supplied)
                self.assertEqual(session.config.equipment_tag, "BT-11")
                self.assertEqual("ignored_equipment_tag" in summary, expect_reported)

    def test_an_empty_config_tag_is_still_an_error(self):
        self.assertIn("error", _fetch(_Session("")))

    def test_a_model_tag_cannot_rescue_an_empty_config_tag(self):
        # Redirecting an unscoped run to arbitrary equipment is exactly the
        # behavior being removed.
        session = _Session("")
        self.assertIn("error", _fetch(session, equipment_tag="P-99"))
        self.assertEqual(session.config.equipment_tag, "")


if __name__ == "__main__":
    unittest.main()
