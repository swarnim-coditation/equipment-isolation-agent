from __future__ import annotations

from domain.display import device_display_label
from domain.isolation_actions import manual_candidate_label, operation_kind


def build_secondary_energy_context(validation_data: dict) -> dict:
    validation = validation_data.get("isolation_validation") or {}
    context_items = validation.get("boundary_context_sources") or validation_data.get("boundary_context_sources") or []
    candidates_by_id = {
        str(candidate.get("candidate_id") or candidate.get("uuid") or ""): candidate
        for candidate in validation_data.get("candidates") or []
        if candidate.get("candidate_id") or candidate.get("uuid")
    }
    items = [_context_hold(item, candidates_by_id) for item in context_items]
    return {
        "status": "completed",
        "policy": "separate_field_holds",
        "items": items,
        "summary": {
            "hold_count": len(items),
            "companion_line_count": sum(1 for item in items if item.get("line_class") == "companion_line"),
        },
    }


def context_source_label(item: dict) -> str:
    label = str(item.get("source_component_tag") or "").strip()
    raw = str(item.get("source_component_tag_raw") or "").strip()
    if label and label != "unlabeled graph-only source":
        return label
    if raw:
        return raw
    return str(item.get("source_component") or "context source")


def context_display_label(item: dict) -> str:
    return f"Context {context_source_label(item)}"


def _context_hold(item: dict, candidates_by_id: dict[str, dict]) -> dict:
    line_class = _first_line_class(item)
    source_label = context_source_label(item)
    nearby_ids = [str(value) for value in item.get("nearby_candidate_ids") or [] if value]
    nearby = [_nearby_candidate(candidates_by_id[candidate_id], item) for candidate_id in nearby_ids if candidate_id in candidates_by_id]
    return {
        "source_component": item.get("source_component"),
        "source_component_tag": source_label,
        "source_component_tag_raw": item.get("source_component_tag_raw"),
        "source_bbox": item.get("source_bbox") or [],
        "classification": item.get("classification"),
        "line_class": line_class,
        "nearby_candidate_ids": nearby_ids,
        "nearby_candidates": nearby,
        "action": (
            f"Review secondary/context line {source_label}. Confirm whether it is tracing, utility, purge, "
            "drain/vent, signal, overflow, or another non-process energy/context line."
        ),
        "purpose": "Identify secondary energy sources that are not part of the process isolation boundary.",
        "interpretation": _interpretation(line_class),
        "acceptance_criteria": (
            "If this line carries hazardous, thermal, electrical, pressure, or stored energy, isolate, "
            "de-energize, drain, or otherwise make it safe under the site procedure before work."
        ),
        "limitation": (
            "This context hold does not prove or disprove process isolation. The current HILT class does "
            "not by itself identify the service fluid or energy source."
        ),
        "basis": item.get("reason") or "non-process context source in HILT graph",
    }


def _nearby_candidate(candidate: dict, context_item: dict) -> dict:
    entity_class = candidate.get("entity_class") or candidate.get("candidate_label")
    return {
        "uuid": str(candidate.get("uuid") or candidate.get("candidate_id") or ""),
        "tag": candidate.get("tag") or candidate.get("tag_number"),
        "entity_class": entity_class,
        "operation_kind": operation_kind(entity_class),
        "label": manual_candidate_label(entity_class, "instrument_context"),
        "bbox": candidate.get("bbox") or [],
        "display_label": device_display_label(candidate, fallback="candidate"),
        "source_component": context_item.get("source_component"),
    }


def _first_line_class(item: dict) -> str:
    for line in item.get("source_hilt_lines") or []:
        value = str(line.get("entity_class") or "").strip()
        if value:
            return value
    return ""


def _interpretation(line_class: str) -> str:
    if line_class == "companion_line":
        return (
            "HILT classified the connection as a companion/context line. This may be a secondary service or "
            "graphical context path; it is not automatically a process nozzle or a confirmed tracer."
        )
    if line_class:
        return f"HILT classified this connection as {line_class}; review the service before work."
    return "The graph marked this as non-process context; review the service before work."
