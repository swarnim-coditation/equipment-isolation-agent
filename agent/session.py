"""AgentSession: server-side state shared across agent tool calls.

Holds the heavy pipeline data (graph boundary, candidates, bboxes) so the LLM
never receives raw dumps -- only the compact summaries returned by each tool in
``tools.py``. Also records an audit trace of every tool call (name, args,
result/error, timestamp) which is the source of truth for a safety-critical,
non-deterministic system.
"""
from __future__ import annotations

from dataclasses import replace
from time import time
from typing import Any

from config import JOB_IDS_BY_NAME, RunConfig


class AgentSession:
    def __init__(self, config: RunConfig):
        self.config: RunConfig = config
        self.boundary_data: dict | None = None
        self.candidate_data: dict | None = None
        self.bbox_data: dict | None = None
        self.isolation_obligations: dict | None = None
        self.evidence_data: dict | None = None
        self.planner_data: dict | None = None
        self.validation_data: dict | None = None
        self.downstream_impact: dict | None = None
        self.instrument_context: dict | None = None
        self.final_payload: dict | None = None
        self.loto_procedure: dict | None = None
        self.isolation_order: list | None = None
        self.trace: list[dict] = []
        self._step = 0

    def record(self, tool: str, args: dict, result: Any, error: Any = None) -> dict:
        self._step += 1
        entry: dict = {
            "step": self._step,
            "tool": tool,
            "args": jsonable(args),
            "ts": time(),
            "ok": error is None,
        }
        if error is not None:
            entry["error"] = str(error)
        else:
            entry["result"] = jsonable(result)
        self.trace.append(entry)
        return entry

    def infer_job_from_candidates(self) -> bool:
        """Mirror ``run._config_with_inferred_job``: infer the P&ID job from the
        job/P&ID property of selected candidates, updating ``self.config`` in place.
        Called automatically by the ``resolve_bboxes`` tool before bbox lookup.
        """
        if not self.candidate_data:
            return False
        counts: dict[str, int] = {}
        for cand in self.candidate_data.get("candidates", []) or []:
            job_name = _candidate_job_name(cand, self.config.job_ids_by_name)
            if job_name:
                counts[job_name] = counts.get(job_name, 0) + 1
        if not counts:
            return False
        inferred_job_name = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if inferred_job_name == self.config.job_name:
            return False
        inferred_job_id = self.config.job_ids_by_name.get(inferred_job_name, "") or JOB_IDS_BY_NAME.get(inferred_job_name, "")
        debug = self.candidate_data.setdefault("debug", {})
        debug["input_job_name"] = self.config.job_name
        debug["input_job_id"] = self.config.resolved_job_id
        debug["inferred_job_name"] = inferred_job_name
        debug["inferred_job_id"] = inferred_job_id
        debug["inferred_job_source"] = "selected_candidate_job_property"
        self.config = replace(self.config, job_name=inferred_job_name, job_id=inferred_job_id)
        self.candidate_data["context"] = self.config.context
        return True


def jsonable(value: Any) -> Any:
    """Best-effort conversion of arbitrary pipeline values into JSON-safe types
    so the trace can be serialized for audit. Handles Path, enums, sets, tuples,
    and recurses into dict/list.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(val) for key, val in value.items()}
    if hasattr(value, "__dict__"):
        try:
            return {str(key): jsonable(val) for key, val in vars(value).items()}
        except Exception:
            pass
    return str(value)


def _candidate_job_name(candidate: dict, job_ids_by_name: dict[str, str] | None = None) -> str:
    properties = candidate.get("properties") or {}
    known_jobs = job_ids_by_name or JOB_IDS_BY_NAME
    for key in ("unit_name", "pnid", "pnid_name", "job_name", "job"):
        value = str(properties.get(key) or candidate.get(key) or "").strip()
        if value in known_jobs:
            return value
    return ""
