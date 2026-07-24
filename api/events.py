"""SSE event formatting for compact agent progress updates."""
from __future__ import annotations

import json
from typing import Any


SUMMARY_KEYS = (
    "assurance_status",
    "total_candidates",
    "bbox_resolved_count",
    "barrier_count",
    "positive_count",
    "verification_count",
    "missing_boundary_count",
    "isolation_points_count",
    "warning_count",
    "error",
    "matched_equipment_count",
    "traversal_limit_hit",
    "job_resolution",
    "job_resolution_error",
    "fatal",
)


def compact_event(kind: str, payload: Any) -> dict:
    if kind == "tool_result" and isinstance(payload, dict):
        result = payload.get("result") or {}
        payload = {
            "name": payload.get("name"),
            "result": {key: result[key] for key in SUMMARY_KEYS if key in result},
        }
    elif kind == "model_text":
        payload = {"text": str(payload).strip().replace("\n", " ")[:240]}
    return {"kind": kind, "payload": payload}


def sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
