HILT_PROCESS_LINE_CLASSES = {"primary_process_line", "secondary_process_line", "main_process_line", "process_line"}
HILT_CONTEXT_LINE_CLASSES = {
    "piping_to_instrument_line",
    "companion_line",
    "instrument_signal_line",
    "signal_line",
    "electrical_signal_line",
}


def analyze_isolation_obligations(candidate_data, config):
    """Build deterministic isolation obligations for each process boundary source.

    The selected isolation candidates remain the only devices counted as barriers.
    This layer makes the boundary accounting explicit and surfaces additional
    same-source valve candidates as manual bypass/parallel-route checks.
    """
    debug = candidate_data.get("debug") or {}
    candidates = candidate_data.get("candidates") or []
    candidate_pool = candidate_data.get("_candidate_pool") or candidates
    selected_ids = {_norm(candidate.get("candidate_id")) for candidate in candidates if candidate.get("candidate_id")}
    pool_by_source = _pool_by_source(candidate_pool)
    items = []
    seen_sources = set()

    for sample in debug.get("bbox_source_visual_selection_samples") or []:
        source_key = _source_key_from_item(sample)
        if not source_key or source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        selected_for_source = [str(value) for value in sample.get("selected_candidate_ids") or [] if value]
        manual_candidates = _manual_candidates_for_source(pool_by_source.get(source_key) or [], selected_ids)
        items.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": _source_label(sample, pool_by_source.get(source_key) or []),
                "source_type": "process",
                "status": "isolated",
                "selected_candidate_ids": selected_for_source,
                "manual_candidates": manual_candidates,
                "manual_candidate_count": len(manual_candidates),
                "basis": "selected drawable isolation candidate for boundary source",
            }
        )

    for source in debug.get("bbox_unselected_source_components") or []:
        source_key = _source_key_from_item(source)
        if not source_key or source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        line_kind = _line_kind(source.get("source_hilt_lines") or [])
        source_pool = pool_by_source.get(source_key) or []
        shared_selected_ids = _selected_candidate_ids_for_source(source_pool, selected_ids)
        manual_candidates = _manual_candidates_for_source(source_pool, selected_ids)
        source_type = "process" if line_kind != "context" else "instrument_context"
        if source_type == "process" and shared_selected_ids:
            status = "isolated"
        else:
            status = "unresolved" if source_type == "process" else "context"
        items.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": source.get("source_component_tag") or source.get("source_component_tag_raw"),
                "source_type": source_type,
                "status": status,
                "selected_candidate_ids": shared_selected_ids,
                "manual_candidates": manual_candidates,
                "manual_candidate_count": len(manual_candidates),
                "source_hilt_line_kind": line_kind,
                "source_hilt_lines": source.get("source_hilt_lines") or [],
                "min_candidate_depth": source.get("min_candidate_depth"),
                "candidate_count": source.get("candidate_count"),
                "basis": _unselected_basis(status, line_kind),
            }
        )

    for context in candidate_data.get("boundary_context_sources") or []:
        source_key = _source_key_from_item(context)
        if not source_key or source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        items.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": context.get("source_component_tag"),
                "source_type": "instrument_context",
                "status": "context",
                "selected_candidate_ids": [],
                "manual_candidates": [],
                "manual_candidate_count": 0,
                "basis": context.get("reason") or "non-process boundary context",
            }
        )

    process_items = [item for item in items if item.get("source_type") == "process"]
    unresolved_items = [item for item in process_items if item.get("status") == "unresolved"]
    manual_candidate_count = sum(len(item.get("manual_candidates") or []) for item in items)
    result = {
        "status": "completed",
        "items": items,
        "summary": {
            "obligation_count": len(items),
            "process_obligation_count": len(process_items),
            "isolated_count": sum(1 for item in process_items if item.get("status") == "isolated"),
            "unresolved_count": len(unresolved_items),
            "context_count": sum(1 for item in items if item.get("status") == "context"),
            "manual_candidate_count": manual_candidate_count,
        },
        "debug": {
            "source_count": len(items),
            "manual_candidate_count": manual_candidate_count,
            "unresolved_source_ids": [item.get("source_component") for item in unresolved_items],
        },
    }
    return {**candidate_data, "isolation_obligations": result}


def _pool_by_source(candidate_pool):
    grouped = {}
    for candidate in candidate_pool or []:
        if candidate.get("source_context_type"):
            continue
        source_key = _source_key_from_item(candidate)
        if source_key:
            grouped.setdefault(source_key, []).append(candidate)
    return grouped


def _manual_candidates_for_source(items, selected_ids, include_selected=False, limit=6):
    rows = []
    seen = set()
    for candidate in sorted(items or [], key=_candidate_sort_key):
        candidate_id = _norm(candidate.get("candidate_id") or candidate.get("visual_id"))
        if not candidate_id or candidate_id in seen:
            continue
        if not include_selected and candidate_id in selected_ids:
            continue
        bbox = _valid_bbox(candidate.get("bbox"))
        if not bbox:
            continue
        seen.add(candidate_id)
        properties = candidate.get("properties") or {}
        rows.append(
            {
                "uuid": str(candidate.get("candidate_id") or candidate.get("visual_id") or ""),
                "bbox": bbox,
                "tag_number": candidate.get("tag_number"),
                "entity_class": properties.get("entity_class") or candidate.get("candidate_label"),
                "traversal_depth": candidate.get("traversal_depth"),
                "source_visual_distance": candidate.get("source_visual_distance"),
                "reason": "Additional same-source candidate; field-confirm whether this is a bypass or parallel isolation requirement.",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _selected_candidate_ids_for_source(items, selected_ids):
    result = []
    seen = set()
    for candidate in sorted(items or [], key=_candidate_sort_key):
        candidate_id = _norm(candidate.get("candidate_id") or candidate.get("visual_id"))
        if not candidate_id or candidate_id not in selected_ids or candidate_id in seen:
            continue
        seen.add(candidate_id)
        result.append(str(candidate.get("candidate_id") or candidate.get("visual_id") or ""))
    return result


def _line_kind(lines):
    if not lines:
        return "unknown"
    classes = {_norm(line.get("entity_class")) for line in lines}
    types = {_norm(line.get("entity_type")) for line in lines}
    if classes & HILT_PROCESS_LINE_CLASSES or types & HILT_PROCESS_LINE_CLASSES or "process_line" in types:
        return "process"
    if classes and classes <= HILT_CONTEXT_LINE_CLASSES:
        return "context"
    return "unknown"


def _unselected_basis(status, line_kind):
    if status == "context":
        return "source lines are non-process instrument/context lines"
    if status == "isolated":
        return "source is covered by an isolation candidate selected for another boundary source"
    if line_kind == "unknown":
        return "source has no selected drawable isolation candidate; HILT line class is unknown"
    return "process boundary source has no selected drawable isolation candidate"


def _source_key_from_item(item):
    equipment = str(item.get("equipment_tag") or "").strip()
    source = str(
        item.get("source_component")
        or item.get("source_component_id")
        or item.get("source_component_tag")
        or ""
    ).strip()
    if not equipment or not source:
        return None
    return (equipment, source)


def _source_label(sample, pool):
    for key in ("source_component_tag", "source_component_tag_raw"):
        value = str(sample.get(key) or "").strip()
        if value:
            return value
    for candidate in pool:
        for key in ("source_display_label", "source_component_tag"):
            value = str(candidate.get(key) or "").strip()
            if value:
                return value
    return ""


def _candidate_sort_key(candidate):
    return (
        0 if _valid_bbox(candidate.get("bbox")) else 1,
        float(candidate.get("source_visual_distance") or 999999.0),
        int(candidate.get("traversal_depth") or 99),
        str(candidate.get("tag_number") or ""),
        str(candidate.get("candidate_id") or ""),
    )


def _valid_bbox(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return []
    try:
        values = [int(value) for value in bbox]
    except Exception:
        return []
    if values[2] <= 0 or values[3] <= 0:
        return []
    return values


def _norm(value):
    return str(value or "").strip().lower()
