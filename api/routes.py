"""HTTP route handlers for the isolation API."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from api.models import EquipmentListRequest, IsolationRunRequest, RunAccepted, RunList, RunStatus
from api.runs import RunStore, event_stream
from api.service import list_project_equipment
from api.db import postgres_configured

router = APIRouter()


def _store(request: Request) -> RunStore:
    return request.app.state.run_store


def _bearer_token(authorization: str = "") -> str:
    value = str(authorization or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _plant360_token(authorization: str = "") -> str:
    return _bearer_token(authorization) or os.environ.get("PLANT360_AUTH_TOKEN", "").strip()


def _require_run_read_auth(authorization: str = "") -> str:
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"kind": "missing_auth_token", "message": "Bearer authorization is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


@router.get("/health")
def health():
    return {
        "ok": True,
        "gemini_api_key_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "plant360_server_token_configured": bool(os.environ.get("PLANT360_AUTH_TOKEN")),
        "postgres_configured": postgres_configured(),
        "default_model": os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash",
    }


@router.post("/equipment")
def equipment(request_body: EquipmentListRequest, authorization: str = Header(default="")):
    token = _plant360_token(authorization)
    if not token:
        raise HTTPException(status_code=400, detail={"kind": "missing_auth_token", "message": "Plant360 auth token is required."})
    return {"items": list_project_equipment(request_body, token)}


@router.post("/isolation-runs", response_model=RunAccepted, status_code=202)
def create_run(request: Request, request_body: IsolationRunRequest, authorization: str = Header(default="")):
    token = _plant360_token(authorization)
    if not token:
        raise HTTPException(status_code=400, detail={"kind": "missing_auth_token", "message": "Plant360 auth token is required."})
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(status_code=503, detail={"kind": "missing_gemini_api_key", "message": "GEMINI_API_KEY is not configured."})
    record = _store(request).create(request_body, token)
    return RunAccepted(
        run_id=record.run_id,
        status=record.status,
        status_url=f"/isolation-runs/{record.run_id}",
        events_url=f"/isolation-runs/{record.run_id}/events",
    )


@router.get("/isolation-runs", response_model=RunList)
def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    authorization: str = Header(default=""),
):
    _require_run_read_auth(authorization)
    return {"items": _store(request).list(limit=limit, offset=offset)}


@router.get("/isolation-runs/{run_id}", response_model=RunStatus)
def run_status(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    return _store(request).snapshot(record, include_result=False)


@router.get("/isolation-runs/{run_id}/result")
def run_result(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    if record.status != "succeeded":
        raise HTTPException(status_code=409, detail={"kind": "result_not_ready", "status": record.status, "error": record.error})
    if record.result is None:
        raise HTTPException(status_code=404, detail={"kind": "result_not_available", "message": "Result is not available."})
    return record.result


@router.get("/isolation-runs/{run_id}/trace")
def run_trace(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    trace_path = record.run_dir / "trace.json"
    if trace_path.exists():
        return FileResponse(trace_path, media_type="application/json")
    if record.trace is not None:
        return record.trace
    raise HTTPException(status_code=404, detail={"kind": "trace_not_available", "message": "Trace is not available."})


@router.get("/isolation-runs/{run_id}/events")
def run_events(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    return StreamingResponse(
        event_stream(record, repository=getattr(_store(request), "repository", None)),
        media_type="text/event-stream",
    )


@router.get("/isolation-runs/{run_id}/viewer")
def run_viewer(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    path = record.run_dir / "viewer.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"kind": "viewer_not_available", "message": "Viewer is not available."})
    return FileResponse(path, media_type="text/html")


@router.get("/isolation-runs/{run_id}/pid-image")
def run_pid_image(request: Request, run_id: str, authorization: str = Header(default="")):
    _require_run_read_auth(authorization)
    record = _store(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"kind": "unknown_run", "message": "Unknown run id."})
    matches = sorted(Path(record.run_dir).glob("*_pid.*"))
    if not matches:
        raise HTTPException(status_code=404, detail={"kind": "pid_image_not_available", "message": "P&ID image is not available."})
    return FileResponse(matches[0])
