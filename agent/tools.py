"""Agent tools: thin wrappers exposing the deterministic pipeline stages to the
Gemini orchestrator, plus a dispatch registry.

Design rules:
- Tools operate on a server-side ``AgentSession`` (heavy data stays out of the
  LLM context) and return COMPACT summaries only -- counts, tags, gaps.
- The pipeline sequence is preserved (boundary -> candidates -> bbox -> evidence
  -> planner -> validate), but the AGENT chooses when to call each and may
  repeat/investigate. Stages gracefully accept whichever upstream data exists.
- ``validate`` runs ``plan_requests`` internally so the agent gets the
  AUTHORITATIVE ``assurance_status`` in one call. It cannot be overridden.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from bbox import resolve_bboxes
from boundary import fetch_boundaries
from candidates import find_candidates
from config import RunConfig
from evidence import build_evidence
from loto import build_loto_procedure as _build_loto_procedure
from output import build_final_payload
from planner import plan_requests
from validator import validate

from agent import osha
from agent.session import AgentSession


def _tag(properties: dict) -> str:
    for key in ("tag_number", "tag", "name", "label", "equipment_number", "Equipment Name"):
        value = properties.get(key)
        if value and not _looks_like_uuid(value):
            return str(value)
    return ""


def _looks_like_uuid(value) -> bool:
    parts = str(value or "").strip().split("-")
    return len(parts) == 5 and [len(part) for part in parts] == [8, 4, 4, 4, 12]


def _short(text: Any, limit: int = 240) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def t_fetch_boundary(session: AgentSession, equipment_tag: str = "") -> dict:
    tag = (equipment_tag or session.config.equipment_tag).strip()
    if not tag:
        return {"error": "equipment_tag is required"}
    config = replace(session.config, equipment_tag=tag)
    data = fetch_boundaries(config)
    session.config = config
    session.boundary_data = data
    return _summarize_boundary(data)


def _summarize_boundary(data: dict) -> dict:
    components = []
    sources = []
    for boundary in data.get("equipment_boundaries", []) or []:
        for comp in boundary.get("components", []) or []:
            props = comp.get("properties") or {}
            components.append({"id": comp.get("id"), "label": comp.get("label"), "tag": _tag(props)})
        for cb in boundary.get("component_boundaries", []) or []:
            comp = cb.get("component") or {}
            props = comp.get("properties") or {}
            nozzle = props.get("Nozzle Id") or props.get("nozzle_id") or ""
            sources.append(
                {
                    "id": comp.get("id"),
                    "label": comp.get("label"),
                    "tag": _tag(props),
                    "nozzle": nozzle,
                }
            )
    return {
        "matched_equipment_count": data.get("matched_equipment_count"),
        "traversal_limit_hit": data.get("traversal_limit_hit"),
        "component_count": len(components),
        "boundary_source_count": len(sources),
        "components": components[:25],
        "boundary_sources": sources[:25],
    }


def t_find_candidates(session: AgentSession, **_) -> dict:
    if not session.boundary_data:
        return {"error": "call fetch_boundary first"}
    data = find_candidates(session.boundary_data, session.config.policy)
    session.candidate_data = data
    return _summarize_candidates(data)


def _summarize_candidates(data: dict) -> dict:
    candidates = data.get("candidates", []) or []
    preview = []
    for cand in candidates:
        props = cand.get("properties") or {}
        preview.append(
            {
                "tag": cand.get("tag_number") or _tag(props),
                "class": props.get("entity_class") or cand.get("candidate_label"),
                "method": cand.get("isolation_method"),
                "depth": cand.get("traversal_depth"),
                "source": cand.get("source_component_tag"),
                "bbox_resolved": bool(cand.get("bbox")),
            }
        )
    debug = data.get("debug") or {}
    return {
        "total_candidates": data.get("total_candidates"),
        "all_before_ranking": data.get("all_candidates_before_ranking"),
        "candidates": preview,
        "raw_before_dedupe": debug.get("raw_candidate_count_before_dedupe"),
        "skipped": debug.get("skipped_count"),
    }


def t_resolve_bboxes(session: AgentSession, **_) -> dict:
    if not session.candidate_data:
        return {"error": "call find_candidates first"}
    inferred = session.infer_job_from_candidates()
    data = resolve_bboxes(session.candidate_data, session.config)
    session.bbox_data = data
    summary = _summarize_bbox(data)
    summary["job_id_used"] = session.config.resolved_job_id or ""
    summary["job_name_used"] = session.config.job_name or ""
    summary["job_inferred_by_agent"] = inferred
    return summary


def _summarize_bbox(data: dict) -> dict:
    debug = data.get("debug") or {}
    candidates = data.get("candidates", []) or []
    return {
        "bbox_resolved_count": debug.get("bbox_resolved_count"),
        "unresolved_count": len(debug.get("bbox_unresolved_candidate_ids") or []),
        "stlm_symbols": debug.get("bbox_stlm_symbol_count"),
        "manual_visual_checks": debug.get("manual_visual_isolation_check_count"),
        "context_instruments": len(data.get("context_instruments") or []),
        "unselected_sources": len(debug.get("bbox_unselected_source_components") or []),
        "candidates": [
            {"tag": c.get("tag_number"), "bbox_present": bool(c.get("bbox"))} for c in candidates[:20]
        ],
    }


def t_list_unselected_sources(session: AgentSession, **_) -> dict:
    """Surface boundary source nozzles that have NO selected isolation candidate --
    i.e. the coverage gaps. Call after resolve_bboxes. Use this when build_evidence
    or validate reports missing_boundary_count > 0, then investigate_source on each.
    """
    data = session.bbox_data or session.candidate_data
    if not data:
        return {"error": "call resolve_bboxes first"}
    debug = data.get("debug") or {}
    unselected = debug.get("bbox_unselected_source_components") or []
    rows = []
    for item in unselected[:25]:
        rows.append(
            {
                "source_component_id": item.get("source_component"),
                "source_component_tag": item.get("source_component_tag"),
                "source_component_tag_raw": item.get("source_component_tag_raw"),
                "nozzle": item.get("source_nozzle_id"),
                "label_confidence": item.get("source_label_confidence"),
                "min_candidate_depth": item.get("min_candidate_depth"),
                "candidate_count": item.get("candidate_count"),
                "reason": _short(item.get("reason")),
            }
        )
    return {
        "unselected_source_count": len(unselected),
        "unselected_sources": rows,
    }


def t_investigate_source(session: AgentSession, source_component_id: str = "") -> dict:
    """Pull focused detail for ONE boundary source so you can reason about why it
    is/isn't covered: every candidate (selected and not) for that source, the
    connected HILT lines, and label confidence. ``source_component_id`` can be the
    id or tag of a source returned by fetch_boundary or list_unselected_sources.
    """
    if not source_component_id:
        return {"error": "source_component_id is required"}
    data = session.bbox_data or session.candidate_data
    if not data:
        return {"error": "call resolve_bboxes first"}
    pool = data.get("_candidate_pool") or data.get("candidates") or []
    target = str(source_component_id)
    matches = [
        candidate
        for candidate in pool
        if target in (str(candidate.get("source_component_id")), str(candidate.get("source_component_tag")))
    ]
    if not matches:
        return {
            "error": f"no candidates found for source '{source_component_id}'",
            "hint": "call list_unselected_sources or fetch_boundary for valid source ids",
        }
    sample = matches[0]
    hilt_lines = sample.get("source_hilt_lines") or []
    return {
        "source_component_id": sample.get("source_component_id"),
        "source_component_tag": sample.get("source_component_tag"),
        "source_display_label": sample.get("source_display_label"),
        "source_label_confidence": sample.get("source_label_confidence"),
        "source_context_type": sample.get("source_context_type"),
        "source_bbox_present": bool(sample.get("source_bbox")),
        "candidate_count": len(matches),
        "candidates": [
            {
                "tag": candidate.get("tag_number") or _tag(candidate.get("properties") or {}),
                "class": (candidate.get("properties") or {}).get("entity_class") or candidate.get("candidate_label"),
                "depth": candidate.get("traversal_depth"),
                "distance": candidate.get("source_visual_distance"),
                "bbox_present": bool(candidate.get("bbox")),
                "non_process_context": bool(candidate.get("source_context_type")),
            }
            for candidate in matches[:15]
        ],
        "hilt_lines": [
            {"class": line.get("entity_class"), "type": line.get("entity_type"), "tag": line.get("tag_number")}
            for line in hilt_lines[:10]
        ],
    }


def t_build_evidence(session: AgentSession, **_) -> dict:
    source = session.bbox_data or session.candidate_data
    if not source:
        return {"error": "call find_candidates (and optionally resolve_bboxes) first"}
    data = build_evidence(source, session.config)
    session.evidence_data = data
    return _summarize_evidence(data)


def _summarize_evidence(data: dict) -> dict:
    evidence = data.get("evidence_state") or {}
    return {
        "candidate_count": evidence.get("candidate_count"),
        "barrier_count": len(evidence.get("barrier_candidate_ids") or []),
        "positive_count": len(evidence.get("positive_candidate_ids") or []),
        "verification_count": len(evidence.get("verification_candidate_ids") or []),
        "expected_boundary_count": evidence.get("expected_boundary_count"),
        "covered_boundary_source_count": evidence.get("covered_boundary_source_count"),
        "missing_boundary_count": evidence.get("missing_boundary_count"),
        "missing_evidence": evidence.get("missing_evidence") or data.get("missing_evidence") or [],
    }


def t_validate(session: AgentSession, **_) -> dict:
    source = session.evidence_data or session.bbox_data or session.candidate_data
    if not source:
        return {"error": "call build_evidence first"}
    planner_data = plan_requests(source, session.config)
    session.planner_data = planner_data
    data = validate(planner_data)
    session.validation_data = data
    return _summarize_validation(data)


def _summarize_validation(data: dict) -> dict:
    validation = data.get("isolation_validation") or {}
    return {
        "assurance_status": data.get("assurance_status"),
        "rationale": validation.get("rationale"),
        "terminal": validation.get("terminal"),
        "authoritative": True,
        "candidate_count": validation.get("candidate_count"),
        "expected_boundary_count": validation.get("expected_boundary_count"),
        "covered_boundary_source_count": validation.get("covered_boundary_source_count"),
        "missing_boundary_count": validation.get("missing_boundary_count"),
        "missing_evidence": validation.get("missing_evidence") or [],
        "unresolved_evidence_checks": [
            check.get("check_name") for check in (validation.get("unresolved_evidence_checks") or [])
        ],
    }


def t_finalize_plan(session: AgentSession, **_) -> dict:
    if not session.validation_data:
        return {"error": "call validate first"}
    payload = build_final_payload(session.validation_data, session.config)
    session.final_payload = payload
    return _summarize_payload(payload)


def _summarize_payload(payload: dict) -> dict:
    data = (payload.get("data") or [{}])[0]
    points = data.get("isolation_points") or []
    return {
        "assurance_status": data.get("assurance_status"),
        "isolation_points_count": len(points),
        "isolation_points": [
            {
                "tag": p.get("tag_number"),
                "class": p.get("entity_class"),
                "method": p.get("isolation_method"),
                "uuid": p.get("uuid"),
            }
            for p in points[:20]
        ],
        "job_id": data.get("job_id"),
        "job_name": data.get("job_name"),
    }


def t_get_osha_guidance(session: AgentSession, topic: str = "") -> dict:
    """RAG over the bundled OSHA 1910.147 reference. Retrieve relevant regulatory
    text to ground your LOTO reasoning in real citations. Call freely for any
    phase -- e.g. topic='stored energy', 'verification', 'isolation sequence'.
    """
    return osha.get_osha_guidance(topic)


def t_build_loto_procedure(session: AgentSession, **_) -> dict:
    """Build the OSHA 1910.147(d) procedure skeleton from the validated plan.
    The 6-phase order is FIXED by regulation (authoritative). The WITHIN-phase
    device order (e.g. which valve to close first) is NOT an OSHA rule -- it is
    engineering judgment. If you have committed an order via set_isolation_order,
    it is applied here; otherwise devices stay in engine candidate order and the
    procedure will be marked as not-yet-ordered. Requires validate."""
    source = session.validation_data or session.planner_data or session.evidence_data
    if not source:
        return {"error": "call validate first"}
    procedure = _build_loto_procedure(source, session.config, isolation_order=session.isolation_order)
    session.loto_procedure = procedure
    return _summarize_loto(procedure)


def t_set_isolation_order(session: AgentSession, ordered_uuids: list | None = None) -> dict:
    """Commit the agent's chosen WITHIN-phase isolation order as a list of device
    uuids (the order in which valves/barriers will be closed). This is engineering
    judgment -- OSHA 1910.147 does NOT prescribe within-phase device order. After
    setting, call build_loto_procedure again so the procedure reflects your order.
    The uuids are the candidate ids returned by find_candidates / build_loto_procedure."""
    if not session.candidate_data:
        return {"error": "call find_candidates first"}
    # Validate against the candidates LOTO actually uses (bbox-selected), which may
    # differ from the graph-ranked find_candidates set after visual selection.
    loto_source = session.validation_data or session.bbox_data or session.candidate_data
    valid_uuids = {str(c.get("candidate_id")) for c in (loto_source.get("candidates") or [])}
    ordered = [str(u) for u in (ordered_uuids or []) if str(u) in valid_uuids]
    known = [u for u in ordered if u in valid_uuids]
    session.isolation_order = ordered
    # Rebuild the procedure immediately so the order is reflected.
    if session.loto_procedure is not None and (session.validation_data or session.evidence_data):
        source = session.validation_data or session.evidence_data
        session.loto_procedure = _build_loto_procedure(source, session.config, isolation_order=ordered)
    return {
        "accepted_order": ordered,
        "valid_count": len(known),
        "submitted_count": len(ordered_uuids or []),
        "ignored_unknown_uuids": [str(u) for u in (ordered_uuids or []) if str(u) not in valid_uuids],
        "procedure_updated": session.loto_procedure is not None,
        "note": "Within-phase order is engineering judgment, not an OSHA requirement. "
        "State your rationale (e.g. process-flow direction) in your summary.",
    }


def _summarize_loto(procedure: dict) -> dict:
    phases_summary = []
    for p in procedure.get("phases", []):
        entry = {
            "phase": p.get("phase"),
            "ref": p.get("ref"),
            "title": p.get("title"),
            "device_count": len(p.get("devices") or p.get("relief_devices") or p.get("verify_devices") or p.get("positive_isolation_devices") or []),
            "has_field_action_gap": bool(p.get("field_action_required")),
        }
        if p.get("phase") == 3:
            entry["devices_with_flow_role"] = [
                {"uuid": d.get("uuid"), "source": d.get("source_component"), "flow_role": d.get("source_flow_role")}
                for d in (p.get("devices") or [])
            ]
        phases_summary.append(entry)
    return {
        "standard": procedure.get("standard"),
        "regulatory_sequence_ref": procedure.get("regulatory_sequence_ref"),
        "phase_order_is_regulatory": procedure.get("phase_order_is_regulatory"),
        "within_phase_order_is_regulatory": procedure.get("within_phase_order_is_regulatory"),
        "within_phase_order_source": procedure.get("within_phase_order_source"),
        "assurance_status": procedure.get("assurance_status"),
        "energy_types": procedure.get("energy_types"),
        "phases": phases_summary,
        "open_gaps": procedure.get("open_gaps"),
        "note": "Phase order is OSHA-fixed/authoritative. Within-phase order is NOT an OSHA rule -- "
        "it is engineering judgment. Each Phase 3 device has a flow_role (inlet/outlet) parsed from "
        "the P&ID; isolate INLET (upstream) first. Commit your order via set_isolation_order.",
    }


TOOL_SPECS: list[dict] = [
    {
        "name": "fetch_boundary",
        "description": (
            "Fetch the equipment boundary (components + boundary source nozzles) from JanusGraph "
            "for the given equipment tag. Always call this first. Stores the full boundary server-side; "
            "returns a compact summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "equipment_tag": {
                    "type": "string",
                    "description": "Equipment tag, e.g. 'BT-11'. Defaults to the requested equipment.",
                }
            },
            "required": [],
        },
        "fn": t_fetch_boundary,
    },
    {
        "name": "find_candidates",
        "description": (
            "Run deterministic isolation-candidate selection over the fetched boundary. Returns the "
            "ranked candidate list (valves/blinds/etc.) with tag, class, isolation method, depth, source. "
            "Requires fetch_boundary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_find_candidates,
    },
    {
        "name": "resolve_bboxes",
        "description": (
            "Resolve candidate bounding boxes from Plant360 STLM/HILT (needs the P&ID job, which is "
            "inferred from candidate unit_name if not given). Enables visual overlay and context "
            "classification. Returns bbox resolution stats and unselected-source gaps. Requires find_candidates."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_resolve_bboxes,
    },
    {
        "name": "list_unselected_sources",
        "description": (
            "List boundary source nozzles that have NO selected isolation candidate (the coverage gaps). "
            "Use when validate/build_evidence report missing_boundary_count > 0 to see exactly which nozzles "
            "are uncovered and why (e.g. nearest candidates too deep). Then call investigate_source on each."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_list_unselected_sources,
    },
    {
        "name": "investigate_source",
        "description": (
            "Pull focused detail for ONE boundary source: all candidates (selected and not) for it, the "
            "connected HILT lines, and label confidence. Use to reason about why a source is uncovered or "
            "whether it is non-process instrument context. source_component_id is an id/tag from "
            "fetch_boundary or list_unselected_sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_component_id": {
                    "type": "string",
                    "description": "The id or tag of the boundary source to investigate.",
                }
            },
            "required": ["source_component_id"],
        },
        "fn": t_investigate_source,
    },
    {
        "name": "build_evidence",
        "description": (
            "Classify deterministic evidence (barrier / positive-isolation / verification) and compute "
            "missing-evidence gaps. Call after find_candidates (and usually resolve_bboxes). Returns "
            "counts and the human-readable missing_evidence list."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_build_evidence,
    },
    {
        "name": "validate",
        "description": (
            "Compute the AUTHORITATIVE isolation assurance verdict (deterministic planner + validator). "
            "This is the only source of assurance_status; you cannot declare isolation yourself. "
            "Returns assurance_status, rationale, terminal flag, and remaining gaps. Call before finishing."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_validate,
    },
    {
        "name": "get_osha_guidance",
        "description": (
            "Retrieve relevant regulatory text from the bundled OSHA 29 CFR 1910.147 reference (RAG). "
            "Use to ground LOTO sequencing reasoning in real citations -- e.g. topic='stored energy', "
            "'verification', 'isolation sequence', 'lockout device', 'release'. Call as many times as needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "What you want OSHA guidance on, e.g. 'stored energy relief' or 'verification of isolation'.",
                }
            },
            "required": ["topic"],
        },
        "fn": t_get_osha_guidance,
    },
    {
        "name": "build_loto_procedure",
        "description": (
            "Build the OSHA 1910.147(d) LOTO procedure skeleton from the validated plan. The 6-phase order "
            "is FIXED by regulation (authoritative -- you cannot reorder phases). Then propose a safe "
            "WITHIN-phase device order (especially Phase 3 isolation) using process-flow reasoning, cite OSHA "
            "via get_osha_guidance, and flag every phase with a field-action gap. Requires validate."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_build_loto_procedure,
    },
    {
        "name": "set_isolation_order",
        "description": (
            "Commit your chosen WITHIN-phase isolation order as an ordered list of device uuids "
            "(the closure order of valves/barriers). This is ENGINEERING JUDGMENT -- OSHA 1910.147 does "
            "NOT prescribe which valve to close first; only the phase order is regulated. Use the "
            "candidate ids from find_candidates/build_loto_procedure. Then call build_loto_procedure again "
            "to reflect your order, and state your rationale (e.g. process-flow direction) in the summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ordered_uuids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Device uuids (candidate ids) in the order the agent proposes to isolate them.",
                }
            },
            "required": ["ordered_uuids"],
        },
        "fn": t_set_isolation_order,
    },
    {
        "name": "finalize_plan",
        "description": (
            "Build the final UI payload (isolation points with bbox/tags/methods) from the validated plan. "
            "Requires validate. Returns the isolation points summary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "fn": t_finalize_plan,
    },
]

DISPATCH: dict[str, Callable] = {spec["name"]: spec["fn"] for spec in TOOL_SPECS}

TOOL_NAMES = list(DISPATCH.keys())


def call_tool(session: AgentSession, name: str, args: dict | None = None) -> dict:
    """Execute a named tool against the session, record it in the audit trace,
    and return the compact result. Errors are caught and returned as
    ``{"error": ...}`` rather than raised, so one bad tool call never kills the
    agent loop.
    """
    args = args or {}
    fn = DISPATCH.get(name)
    if fn is None:
        result = {"error": f"unknown tool: {name}"}
        session.record(name, args, result, error="unknown tool")
        return result
    try:
        result = fn(session, **args)
        session.record(name, args, result)
        return result
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
        session.record(name, args, result, error=exc)
        return result
