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
from typing import Callable

from bbox import resolve_bboxes
from boundary import fetch_boundaries
from candidates import find_candidates
from domain.topology import normalize_tag
from evidence import build_evidence
from impact import analyze_downstream_impact as _analyze_downstream_impact
from instrument_context import analyze_instrument_context as _analyze_instrument_context
from job_resolver import resolve_job_from_boundary
from loto import build_loto_procedure as _build_loto_procedure
from obligations import analyze_isolation_obligations as _analyze_isolation_obligations
from payload import build_final_payload
from planner import plan_requests
from relief import analyze_isolation_schemes_and_relief as _analyze_isolation_schemes_and_relief
from validator import validate

from pipeline.errors import fatal_job_resolution_detail

from agent import osha
from agent.session import AgentSession
from agent.summaries import (
    _short,
    _summarize_bbox,
    _summarize_boundary,
    _summarize_candidates,
    _summarize_downstream_impact,
    _summarize_evidence,
    _summarize_instrument_context,
    _summarize_isolation_obligations,
    _summarize_loto,
    _summarize_payload,
    _summarize_relief_analysis,
    _summarize_validation,
    _tag,
)


def t_fetch_boundary(session: AgentSession, equipment_tag: str = "") -> dict:
    """Fetch the boundary for the equipment THIS RUN is about.

    The CLI-supplied tag is authoritative, exactly as in run.py. A model-supplied
    ``equipment_tag`` is accepted only when it is a formatting variant of the same
    tag; anything else is ignored and reported back, because the alternative is
    silently analysing the wrong equipment. Observed failures this guards against:
    the model passing "BT 11" or "BT11" for a run configured as "BT-11", which
    returned an empty boundary and looked like missing data rather than a typo.
    """
    tag = session.config.equipment_tag.strip()
    if not tag:
        return {"error": "equipment_tag is required"}
    requested = str(equipment_tag or "").strip()
    rejected = requested if requested and normalize_tag(requested) != normalize_tag(tag) else ""
    config = replace(session.config, equipment_tag=tag)
    data = fetch_boundaries(config)
    config, job_debug = resolve_job_from_boundary(config, data)
    data["context"] = config.context
    data.setdefault("debug", {}).update(job_debug)
    session.config = config
    session.boundary_data = data
    summary = _summarize_boundary(data)
    if rejected:
        summary["ignored_equipment_tag"] = rejected
        summary["note"] = (
            f"Ignored equipment_tag={rejected!r}: this run is scoped to {tag!r}. "
            "Do not pass equipment_tag; it is fixed for the run."
        )
    return summary


def _fatal_job_resolution(data: dict | None, config=None) -> dict:
    """Return-not-raise: a tool must never raise, or call_tool flattens it to a
    string and the audit trace loses structure. Detail comes from the shared
    builder so it stays identical to what run.py reports."""
    detail = fatal_job_resolution_detail(config, data)
    if not detail:
        return {}
    return {"error": "fatal_job_resolution", **detail}


def t_find_candidates(session: AgentSession, **_) -> dict:
    if not session.boundary_data:
        return {"error": "call fetch_boundary first"}
    fatal = _fatal_job_resolution(session.boundary_data, session.config)
    if fatal:
        return fatal
    data = find_candidates(session.boundary_data, session.config.policy)
    session.candidate_data = data
    return _summarize_candidates(data)


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


def t_analyze_isolation_obligations(session: AgentSession, **_) -> dict:
    if not session.bbox_data:
        return {"error": "call resolve_bboxes first"}
    data = _analyze_isolation_obligations(session.bbox_data, session.config)
    session.bbox_data = data
    session.isolation_obligations = data.get("isolation_obligations") or {}
    if session.evidence_data:
        session.evidence_data["isolation_obligations"] = session.isolation_obligations
    if session.validation_data:
        session.validation_data["isolation_obligations"] = session.isolation_obligations
        session.validation_data.setdefault("isolation_validation", {})["isolation_obligations"] = session.isolation_obligations
    if session.final_payload:
        session.final_payload.setdefault("data", [{}])[0]["isolation_obligations"] = session.isolation_obligations
    return _summarize_isolation_obligations(session.isolation_obligations)


def t_analyze_isolation_schemes_and_relief(session: AgentSession, **_) -> dict:
    if not session.bbox_data:
        return {"error": "call resolve_bboxes first"}
    data = _analyze_isolation_schemes_and_relief(session.bbox_data, session.config)
    session.bbox_data = data
    session.relief_analysis = {
        "isolated_envelope": data.get("isolated_envelope") or {},
        "detected_isolation_schemes": data.get("detected_isolation_schemes") or {},
        "relief_candidates": data.get("relief_candidates") or {},
    }
    for target in (session.evidence_data, session.planner_data, session.validation_data):
        if target is not None:
            target.update(session.relief_analysis)
    if session.final_payload:
        payload_data = session.final_payload.setdefault("data", [{}])[0]
        payload_data.update(session.relief_analysis)
    return _summarize_relief_analysis(session.relief_analysis)


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
    if session.bbox_data and session.isolation_obligations is None:
        source = _analyze_isolation_obligations(session.bbox_data, session.config)
        session.bbox_data = source
        session.isolation_obligations = source.get("isolation_obligations") or {}
    if session.bbox_data and session.relief_analysis is None:
        source = _analyze_isolation_schemes_and_relief(session.bbox_data, session.config)
        session.bbox_data = source
        session.relief_analysis = {
            "isolated_envelope": source.get("isolated_envelope") or {},
            "detected_isolation_schemes": source.get("detected_isolation_schemes") or {},
            "relief_candidates": source.get("relief_candidates") or {},
        }
    if session.instrument_context:
        source["instrument_context"] = session.instrument_context
    if session.relief_analysis:
        source.update(session.relief_analysis)
    data = build_evidence(source, session.config)
    session.evidence_data = data
    return _summarize_evidence(data)


def t_validate(session: AgentSession, **_) -> dict:
    source = session.evidence_data or session.bbox_data or session.candidate_data
    if not source:
        return {"error": "call build_evidence first"}
    planner_data = plan_requests(source, session.config)
    session.planner_data = planner_data
    data = validate(planner_data)
    session.validation_data = data
    return _summarize_validation(data)


def t_finalize_plan(session: AgentSession, **_) -> dict:
    if not session.validation_data:
        return {"error": "call validate first"}
    if session.relief_analysis:
        session.validation_data.update(session.relief_analysis)
    payload = build_final_payload(session.validation_data, session.config, downstream_impact=session.downstream_impact)
    if session.instrument_context:
        payload.setdefault("data", [{}])[0]["instrument_context"] = session.instrument_context
    session.final_payload = payload
    return _summarize_payload(payload)


def t_get_osha_guidance(session: AgentSession, topic: str = "") -> dict:
    """RAG over the bundled OSHA 1910.147 reference. Retrieve relevant regulatory
    text to ground your LOTO reasoning in real citations. Call freely for any
    phase -- e.g. topic='stored energy', 'verification', 'isolation sequence'.
    """
    return osha.get_osha_guidance(topic)


def t_analyze_downstream_impact(session: AgentSession, **_) -> dict:
    """Analyze process-side assets reachable from selected isolation barriers.
    Requires validate. Reachability is deterministic HILT graph data; the agent
    may summarize it but must not promote possible impacts to certainties."""
    if not session.validation_data:
        return {"error": "call validate first"}
    result = _analyze_downstream_impact(session.validation_data, session.config)
    session.downstream_impact = result
    if session.final_payload:
        session.final_payload.setdefault("data", [{}])[0]["downstream_impact"] = result
    return _summarize_downstream_impact(result)


def t_analyze_instrument_context(session: AgentSession, **_) -> dict:
    """Analyze HILT/STLM instruments relevant to the selected equipment.
    Requires resolve_bboxes. The result is advisory SOP context only; it must not
    change assurance status or be treated as proof of isolation."""
    source = session.bbox_data or session.validation_data
    if not source:
        return {"error": "call resolve_bboxes first"}
    result = _analyze_instrument_context(source, session.config)
    session.instrument_context = result
    for target in (session.bbox_data, session.evidence_data, session.planner_data, session.validation_data):
        if target is not None:
            target["instrument_context"] = result
    if session.final_payload:
        session.final_payload.setdefault("data", [{}])[0]["instrument_context"] = result
    return _summarize_instrument_context(result)


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
    if session.instrument_context:
        source["instrument_context"] = session.instrument_context
    if session.relief_analysis:
        source.update(session.relief_analysis)
    procedure = _build_loto_procedure(source, session.config, isolation_order=session.isolation_order)
    session.loto_procedure = procedure
    if session.final_payload:
        session.final_payload.setdefault("data", [{}])[0]["loto_procedure"] = procedure
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
        if session.instrument_context:
            source["instrument_context"] = session.instrument_context
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


from agent.tool_specs import TOOL_SPECS

# Bind schema names to implementations by convention: tool "foo" -> t_foo.
# TOOL_SPECS no longer carries callables, so this is the single dispatch source.
DISPATCH: dict[str, Callable] = {spec["name"]: globals()[f"t_{spec['name']}"] for spec in TOOL_SPECS}

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
