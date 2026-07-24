"""Guardrail ordering tests for the agent loop.

``_ensure_pipeline`` walks the pipeline forward from wherever the model stopped.
Its ORDER matters: run.py runs instrument context at stage 7, before evidence at
stage 8, so evidence is always built from data that already carries it. The agent
must converge on the same order even when the model skips a tool.

These run offline with a stub session -- no graph, no API key.
"""
import unittest

import agent.loop as loop


class _StubSession:
    """Everything present except instrument context and evidence."""

    def __init__(self):
        self.boundary_data = {"x": 1}
        self.candidate_data = {"x": 1}
        self.bbox_data = {"x": 1}
        self.isolation_obligations = {}
        self.relief_analysis = {}
        self.instrument_context = None
        self.evidence_data = None
        self.validation_data = {"x": 1}
        self.downstream_impact = {}
        self.final_payload = {"data": [{}]}
        self.loto_procedure = {}
        self.config = type("C", (), {"equipment_tag": "N7"})()
        self.trace = []


class GuardrailOrderingTests(unittest.TestCase):
    def _forced_calls(self, session):
        calls = []
        original = loop.call_tool
        loop.call_tool = lambda _session, name, args=None: (calls.append(name), {})[1]
        try:
            loop._ensure_pipeline(session, True, lambda *a, **k: None)
        finally:
            loop.call_tool = original
        return calls

    def test_instrument_context_is_forced_when_the_model_skips_it(self):
        calls = self._forced_calls(_StubSession())
        self.assertIn("analyze_instrument_context", calls)

    def test_instrument_context_is_forced_before_evidence(self):
        # This is the ordering run.py guarantees structurally (stage 7 -> 8).
        calls = self._forced_calls(_StubSession())
        self.assertLess(
            calls.index("analyze_instrument_context"),
            calls.index("build_evidence"),
            "evidence must never be built before instrument context",
        )

    def test_already_present_instrument_context_is_not_re_forced(self):
        session = _StubSession()
        session.instrument_context = {"status": "completed", "instruments": []}
        self.assertNotIn("analyze_instrument_context", self._forced_calls(session))

    def test_nothing_is_forced_when_the_session_is_already_complete(self):
        session = _StubSession()
        session.instrument_context = {"status": "completed"}
        session.evidence_data = {"x": 1}
        self.assertEqual(self._forced_calls(session), [])


if __name__ == "__main__":
    unittest.main()
