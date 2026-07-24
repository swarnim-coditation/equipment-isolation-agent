"""CLI entrypoint for the agentic equipment isolation runner.

Usage:
    uv run python -m agent --equipment BT-11 [--job-name pnid_2_bio_final]
                                            [--model gemini-2.5-flash]
                                            [--max-steps 16] [--output-dir DIR]

The Gemini orchestrator drives the deterministic pipeline as tools. Outputs
mirror the deterministic runner plus an audit trace of every tool call:
    <TAG>.json          final UI payload (same shape as run.py)
    <TAG>.html          bbox overlay viewer
    <TAG>_trace.json    agent transcript + per-tool audit trace
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from image import resolve_pid_image
from output import write_json, write_viewer

from pipeline.config_builder import build_run_config
from pipeline.env import load_dotenv

from agent.loop import DEFAULT_MODEL
from agent.runner import run_agent_pipeline
from agent.session import jsonable

logger = logging.getLogger("agent_isolation")


def parse_args():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the agentic (Gemini-orchestrated) equipment isolation runner.")
    parser.add_argument("--equipment", required=True, help="Equipment tag, e.g. BT-11")
    parser.add_argument("--project-config", default="project_config.json", help="Project profile JSON path")
    parser.add_argument("--project-profile", default="", help="Project profile name from --project-config")
    parser.add_argument("--job-name", default="", help="P&ID/job name, e.g. pnid_2_bio_final")
    parser.add_argument("--job-id", default="", help="P&ID/job id, e.g. 2100")
    parser.add_argument("--host", default="", help="Override Gremlin host")
    parser.add_argument("--port", default="", help="Override Gremlin port")
    parser.add_argument("--project-id", default="", help="Override Unigraph project id")
    parser.add_argument("--cnvrt-project-id", default="", help="Override CNVRT project id")
    parser.add_argument("--traversal-source", default="", help="Override Gremlin traversal source alias")
    parser.add_argument("--collection-id", default="", help="Override CNVRT collection id")
    parser.add_argument("--collection-name", default="", help="Override CNVRT collection name")
    parser.add_argument("--api-base-url", default="https://api.plant360.ai:8080")
    parser.add_argument("--auth-token", default=os.environ.get("PLANT360_AUTH_TOKEN", ""))
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY", ""))
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
        help=f"Gemini model (default: {DEFAULT_MODEL}; override via GEMINI_MODEL env or --model)",
    )
    parser.add_argument("--max-steps", type=int, default=16, help="Cap on agent tool-calling iterations")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--output-dir", default="output_agent")
    parser.add_argument("--image-url", default="", help="Optional P&ID image URL for HTML overlay")
    parser.add_argument("--non-intrusive", action="store_true")
    parser.add_argument("--not-high-risk", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Print only final status / output paths")
    return parser.parse_args()


def build_config(args):
    return build_run_config(
        equipment_tag=args.equipment,
        job_name=args.job_name,
        job_id=args.job_id,
        project_config=args.project_config,
        project_profile=args.project_profile,
        auth_token=args.auth_token,
        api_base_url=args.api_base_url,
        verify_ssl=True,
        cnvrt_project_id=args.cnvrt_project_id,
        collection_id=args.collection_id,
        collection_name=args.collection_name,
        host=args.host,
        port=args.port,
        project_id=args.project_id,
        traversal_source=args.traversal_source,
        max_depth=args.max_depth,
        intrusive_work=not args.non_intrusive,
        high_risk_service=not args.not_high_risk,
        output_dir=args.output_dir,
    )


_SUMMARY_KEYS = (
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
)


def _make_event_printer(quiet: bool):
    if quiet:
        return None

    def on_event(kind, payload):
        if kind == "start":
            print(f"[agent] equipment={payload['equipment']} model={payload['model']} max_steps={payload['max_steps']}")
        elif kind == "tool_call":
            args_str = ", ".join(f"{k}={v}" for k, v in payload["args"].items()) or "-"
            print(f"[agent] -> {payload['name']}({args_str})")
        elif kind == "tool_result":
            r = payload["result"]
            summary = {k: r[k] for k in _SUMMARY_KEYS if k in r}
            print(f"[agent] <- {payload['name']}: {summary}")
        elif kind == "model_text":
            text = str(payload).strip().replace("\n", " ")
            if text:
                print(f"[agent] reasoning: {text[:180]}")
        elif kind == "guardrail":
            if isinstance(payload, dict) and "forced" in payload:
                print(f"[agent] !! guardrail forced {payload['forced']}")
            else:
                print(f"[agent] !! guardrail: {payload}")

    return on_event


def _safe_stem(equipment_tag: str) -> str:
    return str(equipment_tag or "unknown_equipment").replace("/", "_").replace(" ", "_")


def _write_trace(output_dir: Path, equipment_tag: str, model: str, agent_result: dict, trace: list[dict]) -> Path:
    trace_path = output_dir / f"{_safe_stem(equipment_tag)}_trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "equipment": equipment_tag,
                "model": model,
                "agent_result": jsonable(agent_result),
                "trace": jsonable(trace),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    return trace_path


def main():
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, force=True)
    logger.setLevel(logging.WARNING if args.quiet else logging.INFO)

    if not args.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is required (set in .env or pass --gemini-api-key).", file=sys.stderr)
        sys.exit(2)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = None
    try:
        config = build_config(args)
        result = run_agent_pipeline(
            config,
            model=args.model,
            api_key=args.gemini_api_key,
            max_steps=args.max_steps,
            on_event=_make_event_printer(args.quiet),
        )
    except Exception as exc:
        equipment_tag = getattr(config, "equipment_tag", None) or args.equipment
        agent_result = {
            "error": True,
            "kind": "pipeline_error",
            "message": str(exc),
            "steps_used": 0,
            "forced": [],
        }
        trace_path = _write_trace(
            output_dir,
            equipment_tag,
            args.model,
            agent_result,
            [{"kind": "fatal_error", "error": agent_result}],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"trace_json={trace_path}", file=sys.stderr)
        sys.exit(1)

    # Always write the trace first so failed/incomplete runs are still debuggable.
    config = result.config
    stem = _safe_stem(config.equipment_tag)
    trace_path = _write_trace(output_dir, config.equipment_tag, args.model, result.agent_result, result.trace)

    final_payload = result.final_payload
    if not final_payload:
        print(f"ERROR: no final payload produced (forced stages: {result.agent_result.get('forced')}).", file=sys.stderr)
        print(f"trace_json={trace_path}", file=sys.stderr)
        sys.exit(1)

    image_url = args.image_url
    if not image_url:
        image_url, image_debug = resolve_pid_image(config, output_dir, stem)
        final_payload.setdefault("debug", {}).update(image_debug)

    output_json = output_dir / f"{stem}.json"
    viewer_html = output_dir / f"{stem}.html"
    write_json(output_json, final_payload)
    write_viewer(viewer_html, final_payload, image_url=image_url)

    data = final_payload.get("data", [{}])[0]
    print(f"assurance_status={data.get('assurance_status')}")
    print(f"isolation_points={len(data.get('isolation_points') or [])}")
    print(f"agent_steps={result.agent_result['steps_used']}")
    print(f"output_json={output_json}")
    print(f"viewer_html={viewer_html}")
    print(f"trace_json={trace_path}")
