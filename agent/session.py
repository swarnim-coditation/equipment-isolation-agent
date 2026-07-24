"""AgentSession: server-side state shared across agent tool calls.

Holds the heavy pipeline data (graph boundary, candidates, bboxes) so the LLM
never receives raw dumps -- only the compact summaries returned by each tool in
``tools.py``. Also records an audit trace of every tool call (name, args,
result/error, timestamp) which is the source of truth for a safety-critical,
non-deterministic system.
"""
from __future__ import annotations

from time import time
from typing import Any

from config import RunConfig
from pipeline.job_inference import _config_with_inferred_job


class AgentSession:
    def __init__(self, config: RunConfig):
        self.config: RunConfig = config
        self.boundary_data: dict | None = None
        self.candidate_data: dict | None = None
        self.bbox_data: dict | None = None
        self.isolation_obligations: dict | None = None
        self.relief_analysis: dict | None = None
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
        """Infer the P&ID job, using the SAME algorithm as run.py.

        Delegates to ``pipeline.job_inference``: candidate job properties, then
        boundary-derived unit names, then an STLM lookup fallback. Previously
        this implemented only the first of the three, so the agent could leave a
        job unresolved that run.py would have resolved -- which changes bbox
        resolution and therefore candidate selection.

        NOTE: the STLM fallback issues one API call per known job, inside a tool
        call. That is the cost of matching run.py's coverage.
        """
        if not self.candidate_data:
            return False
        config = _config_with_inferred_job(self.config, self.candidate_data, self.boundary_data)
        if config is self.config:  # returns the same object when nothing was inferred
            return False
        self.config = config
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
