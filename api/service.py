"""Service helpers that bridge API requests to the shared agent runner."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from agent.loop import DEFAULT_MODEL
from agent.runner import AgentRunResult, run_agent_pipeline
from image import resolve_pid_image
from output import write_json, write_viewer
from config import JOB_IDS_BY_NAME
from pipeline.config_builder import build_run_config
from pipeline.equipment import add_equipment_jobs, add_equipment_jobs_from_metadata, list_equipment
from pipeline.stages import resolve_project_metadata


def config_from_run_request(request, auth_token: str, output_dir: Path):
    scope = request.work_scope
    return build_run_config(
        equipment_tag=request.equipment_tag,
        job_name=request.job_name,
        job_id=request.job_id,
        project_config="",
        project_profile="__api_no_profile__",
        auth_token=auth_token,
        api_base_url=request.api_base_url,
        verify_ssl=True,
        unigraph_api_base_url=request.unigraph_api_base_url,
        cnvrt_project_id=request.cnvrt_project_id,
        collection_id=request.collection_id,
        collection_name=request.collection_name,
        host=request.host,
        port=request.port,
        project_id=request.unigraph_project_id,
        traversal_source=request.traversal_source,
        max_depth=request.max_depth,
        intrusive_work=scope.intrusive_work,
        high_risk_service=scope.high_risk_service,
        confined_space_entry=scope.confined_space_entry,
        hot_work=scope.hot_work,
        output_dir=output_dir,
    )


def config_from_equipment_request(request, auth_token: str):
    return build_run_config(
        equipment_tag="",
        project_config="",
        project_profile="__api_no_profile__",
        auth_token=auth_token,
        api_base_url=request.api_base_url,
        verify_ssl=True,
        unigraph_api_base_url=request.unigraph_api_base_url,
        cnvrt_project_id=request.cnvrt_project_id,
        collection_id=request.collection_id,
        collection_name=request.collection_name,
        host=request.host,
        port=request.port,
        project_id=request.unigraph_project_id,
        traversal_source=request.traversal_source,
    )


def list_project_equipment(request, auth_token: str):
    config = config_from_equipment_request(request, auth_token)
    config, _metadata_debug = resolve_project_metadata(config)
    items = list_equipment(config.graph, request.limit)
    add_equipment_jobs_from_metadata(items, config.job_ids_by_name)
    add_equipment_jobs(items, config.api, config.job_ids_by_name or JOB_IDS_BY_NAME)
    return items


def execute_agent_request(
    *,
    run_id: str,
    request,
    auth_token: str,
    run_dir: Path,
    on_event: Callable | None = None,
) -> dict:
    config = config_from_run_request(request, auth_token, run_dir)
    model = request.model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    result: AgentRunResult = run_agent_pipeline(
        config,
        model=model,
        api_key=os.environ.get("GEMINI_API_KEY", ""),
        max_steps=request.max_steps,
        on_event=on_event,
    )

    trace_payload = {
        "equipment": result.config.equipment_tag,
        "model": model,
        "agent_result": result.agent_result,
        "trace": result.trace,
    }
    write_json(run_dir / "trace.json", trace_payload)

    if not result.final_payload:
        return {
            "ok": False,
            "error": {
                "kind": "no_payload",
                "message": "No final payload produced.",
                "forced": result.agent_result.get("forced") or [],
            },
            "trace": trace_payload,
        }

    final_payload = result.final_payload
    stem = result.config.equipment_tag.replace("/", "_").replace(" ", "_")
    pid_image_url = ""
    pid_image_path = ""
    if request.image_url:
        pid_image_url = request.image_url
    else:
        _file_uri, image_debug = resolve_pid_image(result.config, run_dir, stem)
        final_payload.setdefault("debug", {}).update(image_debug)
        pid_image_path = image_debug.get("pid_image_path") or ""
        if pid_image_path:
            pid_image_url = f"/isolation-runs/{run_id}/pid-image"

    write_json(run_dir / "result.json", final_payload)
    artifacts = {"trace_url": f"/isolation-runs/{run_id}/trace"}
    if pid_image_path:
        artifacts["pid_image_url"] = f"/isolation-runs/{run_id}/pid-image"
    if request.include_viewer:
        write_viewer(run_dir / "viewer.html", final_payload, image_url=pid_image_url)
        artifacts["viewer_url"] = f"/isolation-runs/{run_id}/viewer"

    return {
        "ok": True,
        "config": result.config,
        "payload": final_payload,
        "trace": trace_payload,
        "agent": {
            "model": model,
            "steps_used": result.agent_result.get("steps_used"),
            "forced": result.agent_result.get("forced") or [],
            "assurance_status": result.agent_result.get("assurance_status"),
            "validate_terminal": result.agent_result.get("validate_terminal"),
        },
        "artifacts": artifacts,
    }
