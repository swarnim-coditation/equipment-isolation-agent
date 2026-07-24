import unittest

from domain.isolation_actions import (
    is_installed_positive_isolation,
    is_operable_barrier,
    operation_kind,
    requires_positive_field_confirmation,
)


class IsolationActionTests(unittest.TestCase):
    def test_blind_flange_is_installed_positive_not_field_confirmed_flange(self):
        self.assertEqual(operation_kind("blind_flange"), "installed_positive_isolation")
        self.assertTrue(is_installed_positive_isolation("blind_flange"))
        self.assertFalse(requires_positive_field_confirmation("blind_flange"))

    def test_plain_flange_requires_field_confirmation_and_is_not_barrier_proof(self):
        self.assertEqual(operation_kind("weld_neck_flange"), "field_confirmed_positive_isolation")
        self.assertTrue(requires_positive_field_confirmation("flange"))
        self.assertFalse(is_operable_barrier("flange"))
        self.assertFalse(is_installed_positive_isolation("flange"))

    def test_check_and_control_valves_are_context_not_close_lock_devices(self):
        self.assertEqual(operation_kind("check_valve"), "directional_context")
        self.assertEqual(operation_kind("control_valve"), "control_context")
        self.assertFalse(is_operable_barrier("check_valve"))
        self.assertFalse(is_operable_barrier("control_valve"))


if __name__ == "__main__":
    unittest.main()
