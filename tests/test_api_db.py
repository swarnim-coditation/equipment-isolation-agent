import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from api.db import postgres_config_from_env
from api.models import IsolationRunRequest
from api.runs import RunStore, event_stream


class _FakeRepository:
    def __init__(self):
        self.runs = {}
        self.events = []

    def insert_run(self, record, request_payload):
        self.runs[record.run_id] = {
            "run_id": record.run_id,
            "equipment_tag": record.equipment_tag,
            "runner": record.runner,
            "status": record.status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "agent": record.agent,
            "result": record.result,
            "trace": record.trace,
            "artifacts": record.artifacts,
            "error": record.error,
            "run_dir": str(record.run_dir),
            "request": request_payload,
        }

    def update_run(self, record):
        self.runs[record.run_id].update(
            {
                "status": record.status,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
                "agent": record.agent,
                "result": record.result,
                "trace": record.trace,
                "artifacts": record.artifacts,
                "error": record.error,
            }
        )

    def append_event(self, run_id, event):
        self.events.append({"id": len(self.events) + 1, "run_id": run_id, "event": event})

    def delete_run(self, run_id):
        self.runs.pop(run_id, None)
        self.events = [event for event in self.events if event["run_id"] != run_id]

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def list_runs(self, limit=100, offset=0):
        rows = sorted(self.runs.values(), key=lambda row: row["created_at"], reverse=True)
        return rows[offset : offset + limit]

    def list_events(self, run_id, after_id=0):
        return [event for event in self.events if event["run_id"] == run_id and event["id"] > after_id]


class _FailingWriteRepository(_FakeRepository):
    def insert_run(self, record, request_payload):
        raise RuntimeError("database unavailable")

    def update_run(self, record):
        raise RuntimeError("database unavailable")

    def append_event(self, run_id, event):
        raise RuntimeError("database unavailable")

    def list_events(self, run_id, after_id=0):
        raise RuntimeError("database unavailable")

    def list_runs(self, limit=100, offset=0):
        raise RuntimeError("database unavailable")


class ApiDbTests(unittest.TestCase):
    def test_postgres_config_uses_separate_env_fields(self):
        old_env = dict(os.environ)
        try:
            os.environ.update(
                {
                    "POSTGRES_HOST": "db",
                    "POSTGRES_PORT": "15432",
                    "POSTGRES_DB": "eqiso",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD": "secret",
                    "POSTGRES_SSLMODE": "disable",
                }
            )
            config = postgres_config_from_env()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        self.assertTrue(config.configured)
        self.assertEqual(config.host, "db")
        self.assertEqual(config.port, 15432)
        self.assertEqual(config.dbname, "eqiso")
        self.assertEqual(config.user, "postgres")
        self.assertEqual(config.password, "secret")
        self.assertEqual(config.sslmode, "disable")

    def test_run_store_mirrors_run_state_to_repository(self):
        repo = _FakeRepository()
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(tmp, max_workers=1, repository=repo)
            request = IsolationRunRequest(
                equipment_tag="P3",
                cnvrt_project_id="277",
                collection_id="206",
                unigraph_project_id="15",
                include_viewer=False,
            )

            class _Result:
                config = SimpleNamespace(equipment_tag="P3")
                final_payload = {"data": [{"assurance_status": "not_isolated"}]}
                agent_result = {"steps_used": 1, "forced": [], "assurance_status": "not_isolated"}
                trace = [{"tool": "validate"}]

            with mock.patch("api.service.run_agent_pipeline", return_value=_Result()), \
                 mock.patch("api.service.resolve_pid_image", return_value=("", {})):
                record = store.create(request, "token")
                for _ in range(100):
                    snapshot = store.snapshot(store.get(record.run_id))
                    if snapshot["status"] == "succeeded":
                        break
                    time.sleep(0.01)
            store.shutdown()
        persisted = repo.get_run(record.run_id)
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["request"]["equipment_tag"], "P3")
        self.assertEqual(persisted["request"]["unigraph_project_id"], "15")
        self.assertNotIn("auth_token", persisted["request"])
        self.assertEqual(persisted["result"]["data"][0]["assurance_status"], "not_isolated")
        listed = store.list()
        self.assertEqual(listed[0]["run_id"], record.run_id)
        self.assertNotIn("result", listed[0])

    def test_repository_write_failure_does_not_zombie_run(self):
        repo = _FailingWriteRepository()
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(tmp, max_workers=1, repository=repo)
            request = IsolationRunRequest(
                equipment_tag="P3",
                cnvrt_project_id="277",
                collection_id="206",
                unigraph_project_id="15",
                include_viewer=False,
            )

            class _Result:
                config = SimpleNamespace(equipment_tag="P3")
                final_payload = {"data": [{"assurance_status": "not_isolated"}]}
                agent_result = {"steps_used": 1, "forced": [], "assurance_status": "not_isolated"}
                trace = [{"tool": "validate"}]

            with mock.patch("api.service.run_agent_pipeline", return_value=_Result()), \
                 mock.patch("api.service.resolve_pid_image", return_value=("", {})):
                record = store.create(request, "token")
                for _ in range(100):
                    snapshot = store.snapshot(store.get(record.run_id))
                    if snapshot["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.01)
            store.shutdown()
        self.assertEqual(snapshot["status"], "succeeded")
        self.assertIsNone(snapshot["error"])

    def test_shutdown_interrupts_nonterminal_runs_and_deletes_repository_rows(self):
        repo = _FakeRepository()
        release = threading.Event()
        started = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(tmp, max_workers=1, repository=repo)
            first = IsolationRunRequest(
                equipment_tag="P3",
                cnvrt_project_id="277",
                collection_id="206",
                unigraph_project_id="15",
                include_viewer=False,
            )
            second = IsolationRunRequest(
                equipment_tag="P4",
                cnvrt_project_id="277",
                collection_id="206",
                unigraph_project_id="15",
                include_viewer=False,
            )

            def stuck(*_, **__):
                started.set()
                release.wait(5)
                return {"ok": True, "payload": {"data": []}, "agent": {}, "trace": [], "artifacts": {}}

            try:
                with mock.patch("api.runs.execute_agent_request", side_effect=stuck):
                    running = store.create(first, "token")
                    queued = store.create(second, "token")
                    self.assertTrue(started.wait(1))
                    store.shutdown()

                self.assertIsNone(repo.get_run(running.run_id))
                self.assertIsNone(repo.get_run(queued.run_id))
                self.assertEqual(store.get(running.run_id).status, "failed")
                self.assertEqual(store.get(queued.run_id).status, "failed")
                self.assertEqual(store.get(running.run_id).error["kind"], "server_shutdown")
                self.assertEqual(store.get(queued.run_id).error["kind"], "server_shutdown")
                self.assertEqual(repo.events, [])
            finally:
                release.set()

    def test_run_store_lists_file_backed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "b" * 32
            run_dir = Path(tmp) / run_id
            run_dir.mkdir()
            (run_dir / "status.json").write_text(
                f'{{"run_id": "{run_id}", "status": "succeeded", "equipment_tag": "P3", '
                '"runner": "agentic", "created_at": 10, "artifacts": {}, "error": null}\n',
                encoding="utf-8",
            )
            (run_dir / "result.json").write_text('{"data": [{"selected_equipment": ["P3"]}]}\n', encoding="utf-8")
            store = RunStore(tmp, max_workers=1)
            listed = store.list()
            record = store.get(run_id)
            store.shutdown()

        self.assertEqual(listed[0]["run_id"], run_id)
        self.assertNotIn("result", listed[0])
        self.assertEqual(record.result["data"][0]["selected_equipment"], ["P3"])

    def test_event_stream_can_read_events_from_repository(self):
        repo = _FakeRepository()
        with tempfile.TemporaryDirectory() as tmp:
            record = SimpleNamespace(run_id="r1", run_dir=Path(tmp), status="failed", events=None)
            repo.events.append(
                {
                    "id": 1,
                    "run_id": "r1",
                    "event": {"kind": "tool_call", "payload": {"name": "fetch_boundary"}},
                }
            )
            frames = list(event_stream(record, repository=repo))
        self.assertIn("fetch_boundary", frames[0])
        self.assertIn("event: done", frames[-1])

    def test_event_stream_falls_back_to_file_when_repository_read_fails(self):
        repo = _FailingWriteRepository()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "events.jsonl").write_text(
                '{"kind": "error", "payload": {"message": "boom"}}\n',
                encoding="utf-8",
            )
            record = SimpleNamespace(run_id="r1", run_dir=run_dir, status="failed", events=None)
            frames = list(event_stream(record, repository=repo))
        self.assertIn("boom", frames[0])
        self.assertIn("event: done", frames[-1])


if __name__ == "__main__":
    unittest.main()
