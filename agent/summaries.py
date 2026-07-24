"""Pure summarizers for the agent tool layer.

Every function here is dict-in / dict-out with no I/O and no ``AgentSession``
coupling. They compact heavy pipeline stage output into the small payloads the
model actually sees, so the model's context stays small.

Extracted from agent/tools.py. Covered by tests/test_agent_summaries.py.
"""
from __future__ import annotations

from typing import Any

from domain.enums import ImpactSeverity, ObligationStatus, SourceType


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
    debug = data.get("debug") or {}
    return {
        "matched_equipment_count": data.get("matched_equipment_count"),
        "traversal_limit_hit": data.get("traversal_limit_hit"),
        "job_resolution": debug.get("job_resolution"),
        "job_name": debug.get("job_name"),
        "job_id": debug.get("job_id"),
        "job_resolution_error": debug.get("job_resolution_error"),
        "fatal": bool(debug.get("fatal")),
        "message": debug.get("message"),
        "pnid_names": debug.get("pnid_names") or [],
        "cnvrt_project_id": debug.get("cnvrt_project_id"),
        "collection_id": debug.get("collection_id"),
        "component_count": len(components),
        "boundary_source_count": len(sources),
        "components": components[:25],
        "boundary_sources": sources[:25],
    }


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


def _summarize_isolation_obligations(result: dict) -> dict:
    summary = result.get("summary") or {}
    items = result.get("items") or []
    unresolved = [
        item
        for item in items
        if item.get("source_type") == SourceType.PROCESS.value and item.get("status") == ObligationStatus.UNRESOLVED.value
    ]
    return {
        "status": result.get("status"),
        "process_obligation_count": summary.get("process_obligation_count"),
        "isolated_count": summary.get("isolated_count"),
        "unresolved_count": summary.get("unresolved_count"),
        "context_count": summary.get("context_count"),
        "manual_candidate_count": summary.get("manual_candidate_count"),
        "unresolved_sources": [
            {
                "source_component": item.get("source_component"),
                "source_tag": item.get("source_component_tag"),
                "manual_candidate_count": len(item.get("manual_candidates") or []),
                "basis": item.get("basis"),
            }
            for item in unresolved[:12]
        ],
    }


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


def _summarize_instrument_context(result: dict) -> dict:
    instruments = result.get("instruments") or []
    checks = result.get("checks") or {}
    return {
        "status": result.get("status"),
        "error": result.get("error"),
        "policy": result.get("policy"),
        "instrument_count": len(instruments),
        "check_counts": {key: len(value or []) for key, value in checks.items()},
        "instruments": [
            {
                "tag": item.get("tag"),
                "prefix": item.get("prefix"),
                "name": item.get("name"),
                "measured_variable": item.get("measured_variable"),
                "instrument_type": item.get("instrument_type"),
                "path_hops": item.get("path_hops"),
            }
            for item in instruments[:12]
        ],
        "top_checks": [
            {"group": group, "tag": check.get("tag"), "action": _short(check.get("action"), 180)}
            for group, rows in checks.items()
            for check in (rows or [])[:3]
        ][:10],
        "note": "Advisory SOP context only. Do not use instruments to upgrade assurance_status.",
    }


def _summarize_downstream_impact(result: dict) -> dict:
    debug = result.get("debug") or {}
    warnings = result.get("warnings") or []
    return {
        "status": result.get("status"),
        "error": result.get("error"),
        "warning_count": len(warnings),
        "likely_count": sum(1 for item in warnings if item.get("severity") == ImpactSeverity.LIKELY.value),
        "possible_count": sum(1 for item in warnings if item.get("severity") == ImpactSeverity.POSSIBLE.value),
        "unknown_flow_path_count": debug.get("unknown_flow_path_count"),
        "top_warnings": [
            {
                "severity": item.get("severity"),
                "source_tag": item.get("source_tag"),
                "affected_tag": item.get("affected_tag"),
                "affected_class": item.get("affected_class"),
                "affected_type": item.get("affected_type"),
                "impact_type": item.get("impact_type"),
                "path_hops": item.get("path_hops"),
            }
            for item in warnings[:8]
        ],
        "note": "Deterministic HILT reachability only. Say 'may affect' for possible impacts.",
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


def _summarize_relief_analysis(result: dict) -> dict:
    schemes = (result.get("detected_isolation_schemes") or {}).get("items") or []
    relief = (result.get("relief_candidates") or {}).get("items") or []
    envelope = result.get("isolated_envelope") or {}
    return {
        "envelope_node_count": envelope.get("node_count"),
        "scheme_count": len(schemes),
        "scheme_counts": ((result.get("detected_isolation_schemes") or {}).get("summary") or {}).get("counts_by_type"),
        "relief_candidate_count": len(relief),
        "relief_counts": ((result.get("relief_candidates") or {}).get("summary") or {}).get("counts_by_type"),
        "schemes": [
            {
                "source": item.get("source_component_tag") or item.get("source_component"),
                "scheme_type": item.get("scheme_type"),
                "barrier_ids": item.get("barrier_ids") or [],
                "relief_candidate_ids": item.get("relief_candidate_ids") or [],
            }
            for item in schemes[:10]
        ],
        "relief_candidates": [
            {
                "id": item.get("id"),
                "tag": item.get("tag"),
                "relief_type": item.get("relief_type"),
                "classified_by": item.get("classified_by"),
                "confidence": item.get("classification_confidence"),
            }
            for item in relief[:10]
        ],
        "note": "Detected from existing topology/data. Does not recommend or invent stronger isolation schemes.",
    }
