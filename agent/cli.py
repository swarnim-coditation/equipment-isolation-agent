"""CLI entrypoint for the agentic equipment isolation runner.

Usage:
    uv run python -m agent --equipment BT-11 [--job-name pnid_2_bio_final]
                                            [--model gemini-2.5-flash]
                                            [--max-steps 12] [--output-dir DIR]

The Gemini orchestrator drives the deterministic pipeline as tools. Outputs
mirror the deterministic runner plus an audit trace of every tool call:
    <TAG>_output.json   final UI payload (same shape as run.py)
    <TAG>_viewer.html   bbox overlay viewer
    <TAG>_trace.json    agent transcript + per-tool audit trace
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from config import ApiConfig, GraphConfig, IsolationPolicy, RunConfig, WorkScope
from image import resolve_pid_image
from output import write_json, write_viewer

from agent.loop import DEFAULT_MODEL, run_agent
from agent.session import AgentSession, jsonable

logger = logging.getLogger("agent_isolation")


def parse_args():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the agentic (Gemini-orchestrated) equipment isolation runner.")
    parser.add_argument("--equipment", required=True, help="Equipment tag, e.g. BT-11")
    parser.add_argument("--job-name", default="", help="P&ID/job name, e.g. pnid_2_bio_final")
    parser.add_argument("--job-id", default="", help="P&ID/job id, e.g. 2100")
    parser.add_argument("--host", default="44.217.77.13")
    parser.add_argument("--port", default="8182")
    parser.add_argument("--project-id", default="274")
    parser.add_argument("--collection-id", default="196")
    parser.add_argument("--collection-name", default="Unit")
    parser.add_argument("--api-base-url", default="https://api.plant360.ai:8080")
    parser.add_argument("--auth-token", default=os.environ.get("PLANT360_AUTH_TOKEN", ""))
    parser.add_argument("--gemini-api-key", default=os.environ.get("GEMINI_API_KEY", ""))
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
        help=f"Gemini model (default: {DEFAULT_MODEL}; override via GEMINI_MODEL env or --model)",
    )
    parser.add_argument("--max-steps", type=int, default=12, help="Cap on agent tool-calling iterations")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--output-dir", default="/tmp/opencode/equipment_isolation_agent")
    parser.add_argument("--image-url", default="", help="Optional P&ID image URL for HTML overlay")
    parser.add_argument("--non-intrusive", action="store_true")
    parser.add_argument("--not-high-risk", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Print only final status / output paths")
    return parser.parse_args()


def load_dotenv():
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def build_config(args) -> RunConfig:
    return RunConfig(
        equipment_tag=args.equipment,
        job_name=args.job_name,
        job_id=args.job_id,
        collection_id=args.collection_id,
        collection_name=args.collection_name,
        graph=GraphConfig(host=args.host, port=args.port, project_id=args.project_id),
        api=ApiConfig(
            base_url=args.api_base_url,
            auth_token=args.auth_token,
            verify_ssl=True,
        ),
        policy=IsolationPolicy(max_traversal_depth=args.max_depth),
        work_scope=WorkScope(
            intrusive_work=not args.non_intrusive,
            high_risk_service=not args.not_high_risk,
        ),
        output_dir=Path(args.output_dir),
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


def main():
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, force=True)
    logger.setLevel(logging.WARNING if args.quiet else logging.INFO)

    if not args.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is required (set in .env or pass --gemini-api-key).", file=sys.stderr)
        sys.exit(2)

    config = build_config(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = AgentSession(config)

    agent_result = run_agent(
        session,
        model=args.model,
        api_key=args.gemini_api_key,
        max_steps=args.max_steps,
        on_event=_make_event_printer(args.quiet),
    )

    # Always write the trace first so failed/incomplete runs are still debuggable.
    config = session.config
    stem = config.equipment_tag.replace("/", "_").replace(" ", "_")
    trace_path = output_dir / f"{stem}_trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "equipment": config.equipment_tag,
                "model": args.model,
                "agent_result": jsonable(agent_result),
                "trace": session.trace,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )

    final_payload = session.final_payload
    if not final_payload:
        print(f"ERROR: no final payload produced (forced stages: {agent_result.get('forced')}).", file=sys.stderr)
        print(f"trace_json={trace_path}", file=sys.stderr)
        sys.exit(1)

    if session.loto_procedure:
        final_payload.setdefault("data", [{}])[0].setdefault("loto_procedure", session.loto_procedure)

    image_url = args.image_url
    if not image_url:
        image_url, image_debug = resolve_pid_image(config, output_dir, stem)
        final_payload.setdefault("debug", {}).update(image_debug)

    output_json = output_dir / f"{stem}_output.json"
    viewer_html = output_dir / f"{stem}_viewer.html"
    write_json(output_json, final_payload)
    write_viewer(viewer_html, final_payload, image_url=image_url)

    data = final_payload.get("data", [{}])[0]
    print(f"assurance_status={data.get('assurance_status')}")
    print(f"isolation_points={len(data.get('isolation_points') or [])}")
    print(f"agent_steps={agent_result['steps_used']}")
    print(f"output_json={output_json}")
    print(f"viewer_html={viewer_html}")
    print(f"trace_json={trace_path}")
