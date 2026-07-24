"""Characterization tests for the agent tool registry.

These pin the wiring between ``TOOL_SPECS`` (the declarative schema Gemini sees,
now in agent/tool_specs.py), ``DISPATCH`` (name -> implementation), and
``TOOL_NAMES``. ``DISPATCH`` is built by convention in agent/tools.py --
``globals()[f"t_{name}"]`` per spec name -- so a spec whose name has no matching
``t_<name>`` would silently break that tool. Nothing else in tests/ covers agent/,
and eval_compare.py needs a live API key, so this is the only offline guard on
that seam.
"""
import unittest

from agent.tools import DISPATCH, TOOL_NAMES, TOOL_SPECS

# Frozen from AGENTS.md "Available agent tools". Update deliberately: adding or
# renaming a tool is a change to the agent's contract, not an implementation detail.
EXPECTED_TOOL_NAMES = frozenset(
    {
        "fetch_boundary",
        "find_candidates",
        "resolve_bboxes",
        "analyze_isolation_obligations",
        "analyze_isolation_schemes_and_relief",
        "list_unselected_sources",
        "investigate_source",
        "build_evidence",
        "analyze_instrument_context",
        "validate",
        "get_osha_guidance",
        "build_loto_procedure",
        "set_isolation_order",
        "analyze_downstream_impact",
        "finalize_plan",
    }
)


class AgentToolRegistryTests(unittest.TestCase):
    def test_registry_exposes_exactly_the_documented_tools(self):
        self.assertEqual({spec["name"] for spec in TOOL_SPECS}, set(EXPECTED_TOOL_NAMES))

    def test_spec_names_are_unique(self):
        names = [spec["name"] for spec in TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)))

    def test_dispatch_and_tool_names_agree_with_specs(self):
        spec_names = {spec["name"] for spec in TOOL_SPECS}
        self.assertEqual(set(DISPATCH), spec_names)
        self.assertEqual(set(TOOL_NAMES), spec_names)

    def test_every_tool_resolves_to_a_callable(self):
        for name, fn in DISPATCH.items():
            with self.subTest(tool=name):
                self.assertTrue(callable(fn), f"{name} does not resolve to a callable")

    def test_every_spec_carries_the_fields_the_model_loop_reads(self):
        # agent/loop.py builds its function declarations from exactly these three.
        for spec in TOOL_SPECS:
            with self.subTest(tool=spec.get("name")):
                self.assertTrue(str(spec.get("description") or "").strip())
                parameters = spec.get("parameters")
                self.assertIsInstance(parameters, dict)
                self.assertEqual(parameters.get("type"), "object")
                self.assertIsInstance(parameters.get("properties"), dict)

    def test_declared_required_parameters_are_declared_properties(self):
        for spec in TOOL_SPECS:
            parameters = spec.get("parameters") or {}
            properties = parameters.get("properties") or {}
            for field in parameters.get("required") or []:
                with self.subTest(tool=spec.get("name"), field=field):
                    self.assertIn(field, properties)


if __name__ == "__main__":
    unittest.main()
