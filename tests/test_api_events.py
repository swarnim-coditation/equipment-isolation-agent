import json
import tempfile
import unittest
from itertools import islice
from pathlib import Path
from types import SimpleNamespace

from api.events import compact_event, sse_frame
from api.runs import event_stream


class ApiEventTests(unittest.TestCase):
    def test_tool_result_events_are_compact(self):
        event = compact_event(
            "tool_result",
            {
                "name": "validate",
                "result": {
                    "assurance_status": "not_isolated",
                    "missing_boundary_count": 2,
                    "large_payload": ["x"] * 100,
                },
            },
        )
        self.assertEqual(event["payload"]["name"], "validate")
        self.assertEqual(event["payload"]["result"]["missing_boundary_count"], 2)
        self.assertNotIn("large_payload", event["payload"]["result"])

    def test_sse_frame_is_json_data(self):
        frame = sse_frame("done", {"status": "succeeded"})
        self.assertTrue(frame.startswith("event: done\n"))
        data = frame.split("data: ", 1)[1].strip()
        self.assertEqual(json.loads(data), {"status": "succeeded"})

    def test_event_stream_replays_persisted_events_for_each_subscriber(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "events.jsonl").write_text(
                json.dumps({"kind": "tool_call", "payload": {"name": "fetch_boundary"}}) + "\n",
                encoding="utf-8",
            )
            record = SimpleNamespace(run_dir=run_dir, status="succeeded", events=None)
            first = list(islice(event_stream(record), 2))
            second = list(islice(event_stream(record), 2))
        self.assertEqual(first, second)
        self.assertIn("fetch_boundary", first[0])


if __name__ == "__main__":
    unittest.main()
