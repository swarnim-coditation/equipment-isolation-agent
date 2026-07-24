"""In-process run registry for the API POC."""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.session import jsonable

from api.events import compact_event, sse_frame
from api.service import execute_agent_request

LOGGER = logging.getLogger(__name__)
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_PATTERN.fullmatch(str(run_id or "")))


class _DaemonWorkerPool:
    def __init__(self, max_workers: int):
        self._tasks: queue.Queue = queue.Queue()
        self._shutdown = False
        self._lock = threading.Lock()
        self._workers = []
        for index in range(max(1, int(max_workers or 1))):
            thread = threading.Thread(target=self._work, name=f"isolation-run-worker-{index + 1}", daemon=True)
            thread.start()
            self._workers.append(thread)

    def submit(self, callback, *args, **kwargs) -> None:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("run worker pool is shut down")
            self._tasks.put((callback, args, kwargs))

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if cancel_futures:
                self._drain_pending_tasks()
            for _thread in self._workers:
                self._tasks.put(None)
        if wait:
            for thread in self._workers:
                thread.join()

    def _drain_pending_tasks(self) -> None:
        while True:
            try:
                self._tasks.get_nowait()
            except queue.Empty:
                return
            else:
                self._tasks.task_done()

    def _work(self) -> None:
        while True:
            task = self._tasks.get()
            try:
                if task is None:
                    return
                callback, args, kwargs = task
                callback(*args, **kwargs)
            except Exception:
                LOGGER.exception("Run worker task failed")
            finally:
                self._tasks.task_done()


@dataclass
class RunRecord:
    run_id: str
    equipment_tag: str
    runner: str
    run_dir: Path
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    agent: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    trace: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    events: queue.Queue = field(default_factory=queue.Queue)


class RunStore:
    def __init__(self, runs_dir: str | Path, max_workers: int = 2, run_timeout_seconds: int = 900, repository=None):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._executor = _DaemonWorkerPool(max_workers=max_workers)
        self._records: dict[str, RunRecord] = {}
        self._lock = threading.Lock()
        self._repository_deleted_run_ids: set[str] = set()
        self._closing = False
        self.run_timeout_seconds = run_timeout_seconds
        self.repository = repository

    def create(self, request, auth_token: str) -> RunRecord:
        run_id = uuid.uuid4().hex
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        record = RunRecord(
            run_id=run_id,
            equipment_tag=request.equipment_tag,
            runner=request.runner,
            run_dir=run_dir,
        )
        with self._lock:
            self._records[run_id] = record
        if self.repository:
            self._safe_repository_call("insert_run", self.repository.insert_run, record, _request_payload(request))
        self._persist(record)
        self._executor.submit(self._run, record, request, auth_token)
        return record

    def get(self, run_id: str) -> RunRecord | None:
        if not is_valid_run_id(run_id):
            return None
        with self._lock:
            record = self._records.get(run_id)
        if record is not None:
            return record
        if self.repository:
            try:
                row = self.repository.get_run(run_id)
            except Exception as exc:
                LOGGER.warning("Run repository get_run failed; falling back to local run files: %s", exc)
            else:
                if row:
                    return _record_from_row(row)
        return self._load_file_record(run_id)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict]:
        summaries: dict[str, dict] = {}
        if self.repository and hasattr(self.repository, "list_runs"):
            try:
                for row in self.repository.list_runs(limit=limit + offset, offset=0):
                    record = _record_from_row(row)
                    summaries[record.run_id] = self.snapshot(record, include_result=False)
            except Exception as exc:
                LOGGER.warning("Run repository list_runs failed; falling back to local run state: %s", exc)
        with self._lock:
            records = list(self._records.values())
        for record in records:
            summaries[record.run_id] = self.snapshot(record, include_result=False)
        for record in self._load_file_records():
            summaries.setdefault(record.run_id, self.snapshot(record, include_result=False))
        items = sorted(summaries.values(), key=lambda item: item.get("created_at") or 0, reverse=True)
        return items[offset : offset + limit]

    def shutdown(self) -> None:
        interrupted = self._interrupt_nonterminal_runs()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for record in interrupted:
            self._delete_repository_run(record)
        close = getattr(self.repository, "close", None)
        if close:
            close()

    def snapshot(self, record: RunRecord, include_result: bool = False) -> dict:
        payload = {
            "run_id": record.run_id,
            "status": record.status,
            "equipment_tag": record.equipment_tag,
            "runner": record.runner,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "agent": record.agent,
            "artifacts": dict(record.artifacts),
            "error": record.error,
        }
        if include_result:
            payload["result"] = record.result
        return payload

    def _run(self, record: RunRecord, request, auth_token: str) -> None:
        timer: threading.Timer | None = None

        def on_event(kind, payload):
            event = compact_event(kind, jsonable(payload))
            self._emit_event(record, event)

        try:
            if self._is_interrupted(record):
                return
            if not self._mark(record, status="running", started_at=time.time()):
                return
            if self.run_timeout_seconds > 0:
                timer = threading.Timer(self.run_timeout_seconds, self._timeout, args=(record,))
                timer.daemon = True
                timer.start()

            outcome = execute_agent_request(
                run_id=record.run_id,
                request=request,
                auth_token=auth_token,
                run_dir=record.run_dir,
                on_event=on_event,
            )
            if outcome.get("ok"):
                if not self._mark(
                    record,
                    status="succeeded",
                    finished_at=time.time(),
                    result=outcome.get("payload"),
                    agent=outcome.get("agent"),
                    trace=outcome.get("trace"),
                    artifacts=outcome.get("artifacts") or {},
                    error=None,
                ):
                    return
                record.events.put({"kind": "done", "payload": {"status": "succeeded"}})
            else:
                error = outcome.get("error") or {"kind": "pipeline_error", "message": "Run failed."}
                if not self._mark(
                    record,
                    status="failed",
                    finished_at=time.time(),
                    trace=outcome.get("trace"),
                    error=error,
                ):
                    return
                self._emit_event(record, {"kind": "error", "payload": error})
        except Exception as exc:
            error = _error_detail(exc)
            if self._mark(
                record,
                status="failed",
                finished_at=time.time(),
                error=error,
            ):
                self._emit_event(record, {"kind": "error", "payload": error})
        finally:
            if timer:
                timer.cancel()
            record.events.put(None)

    def _mark(self, record: RunRecord, **updates) -> bool:
        with self._lock:
            if record.status in {"succeeded", "failed"}:
                return False
            for key, value in updates.items():
                setattr(record, key, value)
        self._persist(record)
        return True

    def _timeout(self, record: RunRecord) -> None:
        if not self._mark(
            record,
            status="failed",
            finished_at=time.time(),
            error={
                "kind": "timeout",
                "message": f"Run exceeded timeout of {self.run_timeout_seconds} seconds.",
            },
        ):
            return
        event = {"kind": "error", "payload": record.error}
        self._emit_event(record, event)
        record.events.put(None)

    def _persist(self, record: RunRecord) -> None:
        self._write(record.run_dir / "status.json", self.snapshot(record, include_result=False))
        if record.error:
            self._write(record.run_dir / "error.json", record.error)
        if self.repository and not self._repository_deleted(record.run_id):
            self._safe_repository_call("update_run", self.repository.update_run, record)

    def _write(self, path: Path, payload: Any) -> None:
        path.write_text(json.dumps(jsonable(payload), indent=2, default=str) + "\n", encoding="utf-8")

    def _append_event(self, record: RunRecord, event: dict) -> None:
        with (record.run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(jsonable(event), default=str) + "\n")
        if self.repository and not self._repository_deleted(record.run_id):
            self._safe_repository_call("append_event", self.repository.append_event, record.run_id, event)

    def _emit_event(self, record: RunRecord, event: dict) -> None:
        self._append_event(record, event)
        record.events.put(event)

    def _safe_repository_call(self, operation: str, callback, *args, **kwargs) -> bool:
        try:
            callback(*args, **kwargs)
            return True
        except Exception as exc:
            LOGGER.warning("Run repository %s failed; continuing with local run state: %s", operation, exc)
            return False

    def _interrupt_nonterminal_runs(self) -> list[RunRecord]:
        error = {
            "kind": "server_shutdown",
            "message": "API server shut down before this run completed.",
        }
        now = time.time()
        with self._lock:
            self._closing = True
            records = [record for record in self._records.values() if record.status not in {"succeeded", "failed"}]
            for record in records:
                record.status = "failed"
                record.finished_at = now
                record.error = error
                self._repository_deleted_run_ids.add(record.run_id)
        for record in records:
            self._write(record.run_dir / "status.json", self.snapshot(record, include_result=False))
            self._write(record.run_dir / "error.json", error)
            event = {"kind": "error", "payload": error}
            self._append_event(record, event)
            record.events.put(event)
            record.events.put(None)
        return records

    def _delete_repository_run(self, record: RunRecord) -> None:
        delete = getattr(self.repository, "delete_run", None)
        if delete:
            self._safe_repository_call("delete_run", delete, record.run_id)

    def _repository_deleted(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._repository_deleted_run_ids

    def _is_interrupted(self, record: RunRecord) -> bool:
        with self._lock:
            return self._closing and record.status == "failed" and record.error and record.error.get("kind") == "server_shutdown"

    def _load_file_record(self, run_id: str) -> RunRecord | None:
        if not is_valid_run_id(run_id):
            return None
        run_dir = self.runs_dir / run_id
        status_path = run_dir / "status.json"
        if not status_path.exists():
            return None
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Run status file read failed for %s: %s", run_id, exc)
            return None
        return _record_from_status_payload(run_dir, payload)

    def _load_file_records(self) -> list[RunRecord]:
        records = []
        for status_path in sorted(self.runs_dir.glob("*/status.json")):
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception as exc:
                LOGGER.warning("Run status file read failed for %s: %s", status_path, exc)
                continue
            records.append(_record_from_status_payload(status_path.parent, payload))
        return records


def event_stream(record: RunRecord, repository=None):
    path = record.run_dir / "events.jsonl"
    offset = 0
    last_db_event_id = 0
    while True:
        if repository:
            try:
                rows = repository.list_events(record.run_id, after_id=last_db_event_id)
            except Exception as exc:
                LOGGER.warning("Run repository list_events failed; falling back to events.jsonl: %s", exc)
                repository = None
            else:
                for row in rows:
                    last_db_event_id = row["id"]
                    item = row["event"]
                    yield sse_frame(str(item.get("kind") or "message"), item)
        if not repository and path.exists():
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                for line in handle:
                    if line.strip():
                        item = json.loads(line)
                        yield sse_frame(str(item.get("kind") or "message"), item)
                offset = handle.tell()
        if record.status in {"succeeded", "failed"}:
            yield sse_frame("done", {"status": record.status})
            break
        try:
            record.events.get(timeout=15)
        except queue.Empty:
            yield ": heartbeat\n\n"


def _error_detail(exc: Exception) -> dict:
    message = str(exc)
    if "Configured project metadata failed" in message:
        kind = "project_metadata"
    elif "Configured CNVRT job resolution failed" in message:
        kind = "job_resolution"
    else:
        kind = "pipeline_error"
    return {"kind": kind, "message": f"{type(exc).__name__}: {message}"}


def _request_payload(request) -> dict:
    return request.model_dump(mode="json", exclude={"auth_token"})


def _record_from_row(row: dict) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        equipment_tag=row["equipment_tag"],
        runner=row["runner"],
        run_dir=Path(row["run_dir"]),
        status=row["status"],
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        agent=row.get("agent"),
        result=row.get("result"),
        trace=row.get("trace"),
        artifacts=row.get("artifacts") or {},
        error=row.get("error"),
    )


def _record_from_status_payload(run_dir: Path, payload: dict) -> RunRecord:
    result = payload.get("result")
    result_path = run_dir / "result.json"
    if result is None and result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Run result file read failed for %s: %s", result_path, exc)
    return RunRecord(
        run_id=str(payload.get("run_id") or run_dir.name),
        equipment_tag=str(payload.get("equipment_tag") or ""),
        runner=str(payload.get("runner") or ""),
        run_dir=run_dir,
        status=str(payload.get("status") or "unknown"),
        created_at=float(payload.get("created_at") or 0),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        agent=payload.get("agent"),
        result=result,
        trace=payload.get("trace"),
        artifacts=payload.get("artifacts") or {},
        error=payload.get("error"),
    )
