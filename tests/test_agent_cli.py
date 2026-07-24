import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agent import cli


class AgentCliTests(unittest.TestCase):
    def test_metadata_fatal_still_writes_trace(self):
        old_env = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GEMINI_API_KEY"] = "gemini-key"
            argv = ["agent", "--equipment", "P3", "--output-dir", tmp, "--quiet"]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch("agent.cli.build_config", return_value=SimpleNamespace(equipment_tag="P3")), \
                 mock.patch("agent.cli.run_agent_pipeline", side_effect=RuntimeError("Configured project metadata failed for equipment P3")), \
                 mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as caught:
                    cli.main()

            self.assertEqual(caught.exception.code, 1)
            trace_path = Path(tmp) / "P3_trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(trace["equipment"], "P3")
            self.assertTrue(trace["agent_result"]["error"])
            self.assertIn("Configured project metadata failed", trace["agent_result"]["message"])
            self.assertEqual(trace["trace"][0]["kind"], "fatal_error")
        os.environ.clear()
        os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
