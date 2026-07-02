"""Eval harness: compare the agentic (Gemini) path against the deterministic
baseline across multiple equipment tags.

Since the agent uses the SAME deterministic stages as tools, its
assurance_status + isolation points must match the baseline exactly -- any
divergence means the agent failed to drive the full pipeline. This script makes
that invariant cheap to check.

Usage:
    uv run python eval_compare.py BT-11 BT-12
    uv run python eval_compare.py --limit 5        # first 5 equipment tags
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from run import load_dotenv
from config import ApiConfig, GraphConfig, RunConfig
from graph_client import GraphClient, normalize_vertex, props_only, vertex_id

from agent.session import AgentSession
from agent.tools import call_tool
from agent.loop import DEFAULT_MODEL, run_agent

load_dotenv()


def _first_value(props, keys):
    for key in keys:
        value = props.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return None


def list_equipment_tags(limit: int = 0) -> list[str]:
    config = GraphConfig()
    with GraphClient(config) as client:
        rows = [normalize_vertex(row) for row in client.g.V().hasLabel("Equipment").valueMap(True).toList()]
    items = []
    for row in rows:
        props = props_only(row)
        tag = _first_value(props, ("tag", "tag_number", "Equipment Name", "name", "equipment_number"))
        if tag:
            items.append(tag)
    items = sorted(set(items))
    return items[:limit] if limit and limit > 0 else items


def build_config(equipment_tag: str) -> RunConfig:
    return RunConfig(
        equipment_tag=equipment_tag,
        graph=GraphConfig(),
        api=ApiConfig(auth_token=os.environ.get("PLANT360_AUTH_TOKEN", "")),
    )


def run_deterministic(config: RunConfig) -> dict:
    """Drive the pipeline in fixed order via the same tools the agent uses."""
    session = AgentSession(config)
    for name in [
        "fetch_boundary",
        "find_candidates",
        "resolve_bboxes",
        "build_evidence",
        "validate",
        "finalize_plan",
    ]:
        call_tool(session, name, {})
    return _signature(session.final_payload)


def run_agentic(config: RunConfig, model: str, max_steps: int) -> dict:
    session = AgentSession(config)
    result = run_agent(session, model=model, max_steps=max_steps)
    return {
        **_signature(session.final_payload),
        "steps_used": result["steps_used"],
        "validate_terminal": result["validate_terminal"],
        "trace_tools": [e["tool"] for e in session.trace],
    }


def _signature(payload: dict | None) -> dict:
    if not payload:
        return {"assurance_status": None, "points": 0, "uuids": []}
    data = payload.get("data", [{}])[0]
    points = data.get("isolation_points") or []
    return {
        "assurance_status": data.get("assurance_status"),
        "points": len(points),
        "uuids": sorted(p.get("uuid") for p in points),
    }


def main():
    parser = argparse.ArgumentParser(description="Compare agentic vs deterministic isolation across equipment.")
    parser.add_argument("equipment", nargs="*", help="Equipment tags to evaluate")
    parser.add_argument("--limit", type=int, default=0, help="If >0, take first N equipment tags from the graph")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()

    tags = args.equipment or list_equipment_tags(args.limit)
    if not tags:
        print("no equipment tags to evaluate", file=sys.stderr)
        sys.exit(1)

    print(f"{'equipment':<16}{'det_status':<28}{'agent_status':<28}{'match':<8}{'steps':<7}{'terminal'}")
    print("-" * 100)
    all_match = True
    for tag in tags:
        config = build_config(tag)
        det = run_deterministic(config)
        try:
            agent = run_agentic(config, args.model, args.max_steps)
        except Exception as exc:
            print(f"{tag:<16}{det['assurance_status']:<28}ERROR: {str(exc)[:50]:<28}")
            all_match = False
            continue
        uuids_match = det["uuids"] == agent["uuids"]
        status_match = det["assurance_status"] == agent["assurance_status"]
        match = status_match and uuids_match and det["points"] == agent["points"]
        all_match = all_match and match
        flag = "OK" if match else "DIFF"
        print(
            f"{tag:<16}{str(det['assurance_status']):<28}{str(agent['assurance_status']):<28}"
            f"{flag:<8}{agent['steps_used']:<7}{agent['validate_terminal']}"
        )
        if not match:
            print(f"    det : status={det['assurance_status']} points={det['points']} uuids={det['uuids']}")
            print(f"    agent: status={agent['assurance_status']} points={agent['points']} uuids={agent['uuids']}")
            print(f"    tools: {agent['trace_tools']}")
    print("-" * 100)
    print("ALL MATCH" if all_match else "DIFFERENCES FOUND")
    sys.exit(0 if all_match else 1)


if __name__ == "__main__":
    main()
