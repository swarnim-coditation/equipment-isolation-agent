from api_client import Plant360Client
from bbox_util import (
    _dedupe_source_candidates,
    _selectable_candidate_pool,
    _source_key,
)
from hilt_merge import _hilt_source_entries, _merge_hilt_source_branches, _merge_hilt_topology
from domain.enums import FlowRole
from flow import classify_nozzle_flow, role_for_source
from hilt_topology import resolve_nozzle_isolation, resolve_source_branch_isolation
from domain.hilt_geometry import calibrate_yflip as _calibrate_hilt_yflip
from hilt_index import (
    _dedupe_hilt_lines,
    _extract_hilt_source_context,
    _find_hilt_node,
    _find_hilt_node_by_tag,
    _hilt_nodes_by_id,
)
from visual_selection import (
    _detect_unclassified_parallel_branch_checks,
    _select_visually_nearest_per_source,
    _sources_owning_isolation_valve,
)
from bbox_geometry import (
    _bbox_distance,
    _bbox_near,
    _looks_like_nozzle_label,
)
from stlm_payload import (
    _extract_text_items,
    _fallback_hilt_yflip,
)
from domain.hilt_geometry import extract_symbols as _extract_symbols
from domain.hilt_geometry import symbol_attr as _symbol_attr
from domain.hilt_geometry import symbol_bbox as _symbol_bbox
from domain.topology import CONTEXT_LINE_CLASSES, PROCESS_LINE_CLASSES, SIGNAL_LINE_CLASSES, normalize_tag
from domain.topology import tag_prefix as _tag_prefix


INSTRUMENT_CONTEXT_MAX_DISTANCE = 190.0
INSTRUMENT_ENTITY_CLASSES = {
    "general_instrument_control_panel",
    "locally_mounted_instrument",
    "instrument",
    "pressure_gauge",
    "pressure_indicator",
    "temperature_indicator",
    "level_indicator",
}
INSTRUMENT_TAG_PREFIXES = {
    "fi",
    "fic",
    "ft",
    "la",
    "lah",
    "lal",
    "li",
    "lic",
    "lg",
    "lt",
    "pi",
    "pg",
    "pt",
    "ti",
    "tc",
    "tt",
}
HILT_SIGNAL_LINE_CLASSES = SIGNAL_LINE_CLASSES
HILT_CONTEXT_LINE_CLASSES = CONTEXT_LINE_CLASSES
HILT_PROCESS_LINE_CLASSES = PROCESS_LINE_CLASSES


def resolve_bboxes(candidate_data, config):
    job_id = config.resolved_job_id
    debug = dict(candidate_data.get("debug", {}) or {})
    if not job_id:
        debug["bbox_error"] = "missing_job_id"
        return {**candidate_data, "debug": debug}

    client = Plant360Client(config.api)
    try:
        stlm_payload = client.stlm_symbols(job_id)
    except Exception as exc:
        debug["bbox_stlm_error"] = str(exc)
        stlm_payload = None
    try:
        hilt_payload = client.hilt_graph(job_id)
    except Exception as exc:
        debug["hilt_graph_error"] = str(exc)
        hilt_payload = None

    symbols = _extract_symbols(stlm_payload)
    text_items = _extract_text_items(stlm_payload)
    hilt_nodes_raw = ((hilt_payload.get("hilt_graph") or {}) if isinstance(hilt_payload, dict) else {}).get("nodes") or []
    y_flip_h = _calibrate_hilt_yflip(hilt_nodes_raw, symbols)
    if hilt_payload and y_flip_h is None:
        y_flip_h = _fallback_hilt_yflip(config, client, hilt_payload, debug)
    hilt_node_by_id = _hilt_nodes_by_id(hilt_payload, y_flip_h) if hilt_payload and y_flip_h is not None else {}
    hilt_source_context = _extract_hilt_source_context(hilt_payload)
    symbol_by_id = {}
    nozzle_symbol_by_parent_and_id = {}
    for symbol in symbols:
        for key in (symbol.get("uuid"), symbol.get("id"), symbol.get("source_id")):
            if key:
                symbol_by_id[_norm(key)] = symbol
        nozzle_id = _symbol_attr(symbol, "Nozzle Id") or _symbol_attr(symbol, "nozzle_id")
        parent_id = symbol.get("associated_equipment_id") or symbol.get("parent")
        if nozzle_id and parent_id:
            nozzle_symbol_by_parent_and_id[(_norm(parent_id), _norm(nozzle_id))] = symbol

    candidates, resolved = _resolve_candidate_bboxes(
        candidate_data.get("candidates", []) or [],
        symbol_by_id,
        nozzle_symbol_by_parent_and_id,
        hilt_node_by_id,
    )
    candidate_pool, pool_resolved = _resolve_candidate_bboxes(
        candidate_data.get("_candidate_pool", []) or [],
        symbol_by_id,
        nozzle_symbol_by_parent_and_id,
        hilt_node_by_id,
    )
    selected_equipment_overlays = _resolve_selected_equipment_overlays(
        candidate_data.get("selected_equipment_nodes") or [],
        symbols,
        config.equipment_tag,
        hilt_node_by_id,
    )
    candidate_pool = _mark_visible_source_labels(candidate_pool, symbols, text_items)
    candidate_pool = _attach_hilt_source_context(candidate_pool, hilt_source_context)
    flow_roles = classify_nozzle_flow(hilt_payload) if hilt_payload else {}
    candidate_pool = _attach_flow_roles(candidate_pool, flow_roles)
    hilt_context_sources, hilt_context_items = _hilt_context_sources(candidate_pool, symbols)
    visual_context_sources, visual_context_items = _instrument_context_sources(candidate_pool, symbols, hilt_context_sources)
    context_sources = hilt_context_sources | visual_context_sources
    # A process isolation valve physically cannot sit inline on a signal line
    # (electrical/instrument "signal" lines carry signals, not process fluid). So a
    # source excused as instrument context *because of a signal line* that also owns a
    # policy-selectable isolation valve is a definitive line-class mislabel (e.g. a
    # process bridle mis-converted to electrical_signal_line) — override it so the real
    # valve is kept. Companion-line / piping-to-instrument context is intentionally
    # left untouched: a small valve there can legitimately be instrument context rather
    # than a process boundary, which is exactly what this classification is for.
    isolation_valve_sources = _sources_owning_isolation_valve(candidate_pool, config.policy)
    signal_line_context_sources = {
        (str(item.get("equipment_tag") or ""), str(item.get("source_component") or ""))
        for item in hilt_context_items
        if _context_item_has_signal_line(item)
    }
    mislabeled_sources = isolation_valve_sources & signal_line_context_sources
    context_overridden = sorted(str(source) for source in context_sources & mislabeled_sources)
    context_sources = context_sources - mislabeled_sources
    context_instruments = [
        item
        for item in (hilt_context_items + visual_context_items)
        if (str(item.get("equipment_tag") or ""), str(item.get("source_component") or "")) not in mislabeled_sources
    ]
    candidate_pool = _mark_source_context(candidate_pool, context_sources)
    selectable_candidate_pool = _selectable_candidate_pool(candidate_pool, config.policy)
    visual_candidates, visual_debug = _select_visually_nearest_per_source(selectable_candidate_pool, candidate_pool)
    if visual_candidates:
        candidates = visual_candidates
        resolved = sum(1 for candidate in candidates if candidate.get("bbox"))
    manual_visual_checks = _detect_unclassified_parallel_branch_checks(candidate_pool, text_items)
    candidates = _attach_flow_roles(candidates, flow_roles)
    # HILT piping topology is AUTHORITATIVE for nozzle<->valve connectivity
    # (the parsed P&ID piping graph beats JanusGraph depth + bbox distance, which
    # can pick a geographically-near but topologically-wrong valve). HILT uses a
    # CAD y-axis (bottom-left); calibrate the flip to image coords via STLM nozzles.
    hilt_isolation_map = (
        resolve_nozzle_isolation(hilt_payload, config.equipment_tag, y_flip=y_flip_h, policy=config.policy)
        if hilt_payload and y_flip_h is not None
        else {}
    )
    hilt_branch_obligations = (
        resolve_source_branch_isolation(
            hilt_payload,
            _hilt_source_entries(candidate_pool),
            y_flip=y_flip_h,
            policy=config.policy,
        )
        if hilt_payload and y_flip_h is not None
        else []
    )
    candidates = (
        _merge_hilt_source_branches(candidates, hilt_branch_obligations, flow_roles, config.equipment_tag, config.policy)
        if hilt_branch_obligations
        else candidates
    )
    candidates = _merge_hilt_topology(candidates, hilt_isolation_map, flow_roles, config.equipment_tag, config.policy) if hilt_isolation_map else candidates

    debug.update(
        {
            "bbox_resolved_count": resolved,
            "target_equipment_bbox_resolved_count": len(selected_equipment_overlays),
            "bbox_candidate_pool_resolved_count": pool_resolved,
            "bbox_selectable_candidate_pool_count": len(selectable_candidate_pool),
            "bbox_stlm_symbol_count": len(symbols),
            "hilt_graph_node_count": hilt_source_context.get("node_count", 0),
            "hilt_graph_link_count": hilt_source_context.get("link_count", 0),
            "hilt_source_line_context_count": hilt_source_context.get("source_line_context_count", 0),
            **visual_debug,
            "bbox_unresolved_candidate_ids": [
                candidate.get("candidate_id") for candidate in candidates if not candidate.get("bbox")
            ],
            "manual_visual_isolation_check_count": len(manual_visual_checks),
            "manual_visual_isolation_checks": manual_visual_checks,
            "context_instrument_source_component_count": len(context_sources),
            "context_instruments": context_instruments,
            "context_overridden_by_isolation_valve": context_overridden,
            "flow_nozzle_classified_count": len(flow_roles),
            "flow_candidate_role_counts": {
                role: sum(1 for c in candidates if (c.get("source_flow_role") or FlowRole.UNKNOWN.value) == role)
                for role in (FlowRole.INLET.value, FlowRole.OUTLET.value, FlowRole.BIDIRECTIONAL.value, FlowRole.UNKNOWN.value)
            },
            "hilt_topology_nozzle_count": len(hilt_isolation_map),
            "hilt_topology_valve_count": sum(len(v) for v in hilt_isolation_map.values()),
            "hilt_branch_source_count": len(hilt_branch_obligations),
            "hilt_branch_count": sum(len(source.get("branches") or []) for source in hilt_branch_obligations),
            "hilt_branch_isolated_count": sum(
                1
                for source in hilt_branch_obligations
                for branch in source.get("branches") or []
                if branch.get("status") == "isolated"
            ),
            "hilt_topology_authoritative_count": sum(1 for c in candidates if c.get("connectivity_source") == "hilt_topology"),
            "hilt_y_flip_calibrated": y_flip_h,
        }
    )
    return {
        **candidate_data,
        "candidates": candidates,
        "_candidate_pool": candidate_pool,
        "selected_equipment_nodes": candidate_data.get("selected_equipment_nodes") or [],
        "selected_equipment_overlays": selected_equipment_overlays,
        "manual_visual_isolation_checks": manual_visual_checks,
        "boundary_context_sources": context_instruments,
        "context_instruments": context_instruments,
        "hilt_branch_obligations": hilt_branch_obligations,
        "_hilt_payload": hilt_payload,
        "_stlm_payload": stlm_payload,
        "debug": debug,
    }


def _resolve_candidate_bboxes(candidates, symbol_by_id, nozzle_symbol_by_parent_and_id, hilt_node_by_id=None):
    hilt_node_by_id = hilt_node_by_id or {}
    resolved = 0
    result = []
    for candidate in candidates:
        candidate = dict(candidate)
        symbol = symbol_by_id.get(_norm(candidate.get("visual_id")))
        if symbol:
            bbox = _symbol_bbox(symbol)
            if bbox:
                candidate["bbox"] = bbox
                candidate["visual_source"] = "stlm_symbol_json"
                candidate["bbox_match_method"] = "stlm_uuid"
                candidate["visual_node_id"] = symbol.get("uuid") or symbol.get("id")
                if not candidate.get("tag_number") and symbol.get("tag"):
                    candidate["tag_number"] = symbol.get("tag")
                    candidate["tag_number_source"] = "stlm_symbol_json"
                resolved += 1
        if not candidate.get("bbox"):
            hilt_node = _find_hilt_node(
                hilt_node_by_id,
                candidate.get("visual_id"),
                candidate.get("candidate_id"),
                candidate.get("cnvrt_id"),
            )
            if hilt_node and hilt_node.get("bbox"):
                candidate["bbox"] = hilt_node["bbox"]
                candidate["visual_source"] = "hilt_graph"
                candidate["bbox_match_method"] = "hilt_uuid"
                candidate["visual_node_id"] = hilt_node.get("uuid")
                if not candidate.get("tag_number") and hilt_node.get("tag_number"):
                    candidate["tag_number"] = hilt_node.get("tag_number")
                    candidate["tag_number_source"] = "hilt_graph"
                resolved += 1
        source_symbol = symbol_by_id.get(_norm(candidate.get("source_visual_id")))
        if not source_symbol:
            source_symbol = nozzle_symbol_by_parent_and_id.get(
                (_norm(candidate.get("source_parent_id")), _norm(candidate.get("source_nozzle_id")))
            )
        if source_symbol:
            source_bbox = _symbol_bbox(source_symbol)
            if source_bbox:
                candidate["source_bbox"] = source_bbox
                candidate["source_visual_node_id"] = source_symbol.get("uuid") or source_symbol.get("id")
                candidate["source_visual_distance"] = _bbox_distance(source_bbox, candidate.get("bbox"))
        if not candidate.get("source_bbox"):
            source_node = _find_hilt_node(
                hilt_node_by_id,
                candidate.get("source_visual_id"),
                candidate.get("source_visual_node_id"),
                candidate.get("source_component_id"),
            )
            if source_node and source_node.get("bbox"):
                candidate["source_bbox"] = source_node["bbox"]
                candidate["source_visual_node_id"] = source_node.get("uuid")
                candidate["source_visual_source"] = "hilt_graph"
                candidate["source_bbox_match_method"] = "hilt_uuid"
                candidate["source_visual_distance"] = _bbox_distance(candidate.get("source_bbox"), candidate.get("bbox"))
        result.append(candidate)
    return result, resolved


def _context_item_has_signal_line(item):
    """True if a context item was excused (partly) because of a signal line. A signal
    line cannot physically carry a process isolation valve, so a selectable valve on
    such a source indicates a mislabel rather than genuine instrument context."""
    for line in item.get("source_hilt_lines") or []:
        values = {_norm(line.get("entity_class")), _norm(line.get("entity_type"))}
        if values & HILT_SIGNAL_LINE_CLASSES:
            return True
    return False


def _resolve_selected_equipment_overlays(equipment_nodes, symbols, equipment_tag, hilt_node_by_id=None):
    hilt_node_by_id = hilt_node_by_id or {}
    symbol_by_id = {}
    equipment_symbols_by_tag = {}
    symbols_by_tag = {}
    for symbol in symbols:
        bbox = _symbol_bbox(symbol)
        if not bbox:
            continue
        for key in (symbol.get("uuid"), symbol.get("id"), symbol.get("source_id")):
            if key:
                symbol_by_id[_norm(key)] = symbol
        tag = symbol.get("tag") or symbol.get("name") or symbol.get("label")
        if tag:
            symbols_by_tag.setdefault(_norm(tag), symbol)
            if _norm(symbol.get("entity_type")) == "equipment":
                equipment_symbols_by_tag.setdefault(_norm(tag), symbol)

    overlays = []
    seen = set()
    for node in equipment_nodes:
        properties = node.get("properties") or {}
        symbol = None
        for value in (
            properties.get("node_id"),
            properties.get("cnvrt_id"),
            properties.get("source_id"),
            properties.get("uuid"),
            node.get("id"),
        ):
            symbol = symbol_by_id.get(_norm(value))
            if symbol:
                break
        if not symbol:
            tag = _equipment_tag(properties) or equipment_tag
            symbol = equipment_symbols_by_tag.get(_norm(tag)) or symbols_by_tag.get(_norm(tag))
        hilt_node = None
        if not symbol:
            hilt_node = _find_hilt_node(
                hilt_node_by_id,
                properties.get("node_id"),
                properties.get("cnvrt_id"),
                properties.get("source_id"),
                properties.get("uuid"),
                node.get("id"),
            )
            if not hilt_node:
                tag = _equipment_tag(properties) or equipment_tag
                hilt_node = _find_hilt_node_by_tag(hilt_node_by_id, tag)
        bbox = _symbol_bbox(symbol) if symbol else (hilt_node.get("bbox") if hilt_node else [])
        if not bbox:
            continue
        key = tuple(bbox)
        if key in seen:
            continue
        seen.add(key)
        overlays.append(
            {
                "equipment_id": node.get("id"),
                "uuid": str(
                    (symbol or {}).get("uuid")
                    or (symbol or {}).get("id")
                    or (hilt_node or {}).get("uuid")
                    or properties.get("node_id")
                    or node.get("id")
                    or ""
                ),
                "tag": _equipment_tag(properties) or (symbol or {}).get("tag") or (hilt_node or {}).get("tag_number") or equipment_tag,
                "entity_class": properties.get("entity_class") or (symbol or {}).get("entity_class") or (hilt_node or {}).get("entity_class") or "equipment",
                "bbox": bbox,
                "reason": "Selected equipment of interest resolved from STLM/HILT symbol.",
            }
        )
    return overlays


def _equipment_tag(properties):
    for key in ("tag", "tag_number", "name", "Equipment Name", "equipment_number", "System Number"):
        value = properties.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return ""


def _attach_hilt_source_context(candidates, hilt_context):
    lines_by_endpoint = hilt_context.get("lines_by_endpoint") or {}
    result = []
    for candidate in candidates:
        candidate = dict(candidate)
        lines = []
        for key in (candidate.get("source_visual_id"), candidate.get("source_visual_node_id")):
            if key:
                lines.extend(lines_by_endpoint.get(_norm(key)) or [])
        candidate["source_hilt_lines"] = _dedupe_hilt_lines(lines)
        result.append(candidate)
    return result


def _attach_flow_roles(candidates, flow_roles):
    if not flow_roles:
        return candidates
    result = []
    for candidate in candidates:
        candidate = dict(candidate)
        candidate["source_flow_role"] = role_for_source(
            flow_roles,
            candidate.get("source_component_tag"),
            candidate.get("source_visual_id") or candidate.get("source_visual_node_id"),
        )
        result.append(candidate)
    return result


def _hilt_context_sources(candidate_pool, symbols=None):
    by_source = {}
    for candidate in candidate_pool:
        if candidate.get("source_hilt_lines"):
            by_source.setdefault(_source_key(candidate), []).append(candidate)

    instrument_symbols = _instrument_symbols(symbols or [])
    context_sources = set()
    context_items = []
    for source_key, items in by_source.items():
        sample = items[0]
        lines = _dedupe_hilt_lines(line for item in items for line in item.get("source_hilt_lines") or [])
        if not lines:
            continue
        classification = _classify_hilt_source_context(sample, lines, instrument_symbols)
        if not classification:
            continue
        context_sources.add(source_key)
        context_items.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": _source_context_label(sample),
                "source_component_tag_raw": sample.get("source_component_tag"),
                "source_bbox": sample.get("source_bbox") or [],
                "classification": classification,
                "source": "hilt_graph",
                "reason": _hilt_context_reason(classification),
                "source_hilt_lines": lines,
                "nearby_instruments": _nearby_instruments(sample.get("source_bbox") or [], instrument_symbols)[:5],
                "nearby_candidate_ids": [item.get("candidate_id") for item in _dedupe_source_candidates(items)[:5]],
            }
        )
    return context_sources, context_items


def _classify_hilt_source_context(sample, lines, instrument_symbols):
    line_classes = {_norm(line.get("entity_class")) for line in lines}
    line_types = {_norm(line.get("entity_type")) for line in lines}
    line_values = line_classes | line_types
    has_process_line = bool(line_values & HILT_PROCESS_LINE_CLASSES or "process_line" in line_types)
    has_context_line = bool(line_values & HILT_CONTEXT_LINE_CLASSES)
    has_signal_line = bool(line_values & HILT_SIGNAL_LINE_CLASSES)
    graph_only_unlabeled = sample.get("source_label_confidence") == "graph_only_unlabeled_component"

    if not has_process_line and has_context_line:
        if has_signal_line:
            return "instrument_signal_context"
        return "instrument_only_context" if "piping_to_instrument_line" in line_values else "companion_line_context"

    # An unlabeled graph-only source near an instrument is only excused as
    # instrument context when its process line does NOT lead onward to an equipment
    # nozzle. That distinguishes a genuine instrument tie-in stub (excuse it) from a
    # real, merely-unlabeled process connection that a transmitter happens to tap
    # (keep it a process path so its isolation obligation is not silently dropped).
    if (
        graph_only_unlabeled
        and not _process_line_reaches_equipment(lines)
        and _has_instrument_context_evidence(sample, lines, instrument_symbols)
    ):
        return "instrument_signal_context" if has_signal_line else "instrument_adjacent_context"

    return ""


def _process_line_reaches_equipment(lines):
    """True if any process line among `lines` connects onward to an equipment
    nozzle (or equipment). Such a source is a real process path, not an instrument
    tie-in artifact, so it must not be excused as instrument context."""
    onward = {"equipment_nozzle", "equipment"}
    for line in lines or []:
        class_values = {_norm(line.get("entity_class")), _norm(line.get("entity_type"))}
        if not (class_values & HILT_PROCESS_LINE_CLASSES or "process_line" in class_values):
            continue
        for node in line.get("connected_nodes") or []:
            node_values = {_norm((node or {}).get("entity_class")), _norm((node or {}).get("entity_type"))}
            if node_values & onward:
                return True
    return False


def _has_instrument_context_evidence(sample, lines, instrument_symbols):
    if _lines_touch_instrument_context(lines):
        return True
    return bool(_nearby_instruments(sample.get("source_bbox") or [], instrument_symbols))


def _lines_touch_instrument_context(lines):
    for line in lines or []:
        line_values = {_norm(line.get("entity_class")), _norm(line.get("entity_type"))}
        if line_values & HILT_SIGNAL_LINE_CLASSES:
            return True
        for node in line.get("connected_nodes") or []:
            node_class = _norm((node or {}).get("entity_class"))
            node_type = _norm((node or {}).get("entity_type"))
            node_tag = str((node or {}).get("tag_number") or "")
            if node_class in INSTRUMENT_ENTITY_CLASSES or node_type in {"instrument", "control_unit"}:
                return True
            if _tag_prefix(node_tag) in INSTRUMENT_TAG_PREFIXES:
                return True
    return False


def _nearby_instruments(source_bbox, instrument_symbols):
    if not source_bbox:
        return []
    nearby = []
    for instrument in instrument_symbols or []:
        distance = _bbox_distance(source_bbox, instrument.get("bbox"))
        if distance is None or distance > INSTRUMENT_CONTEXT_MAX_DISTANCE:
            continue
        nearby.append({**instrument, "distance_from_source": round(distance, 4)})
    nearby.sort(key=lambda item: item.get("distance_from_source") or 999999.0)
    return nearby


def _source_context_label(candidate):
    label = str(candidate.get("source_display_label") or "").strip()
    if label:
        return label
    if candidate.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return candidate.get("source_component_tag") or "unknown source"


def _hilt_context_reason(classification):
    if classification == "instrument_signal_context":
        return "HILT graph connects this source to an instrument/control signal path; it is treated as instrument context rather than a required process isolation boundary."
    if classification == "instrument_adjacent_context":
        return "Graph-only source is visually tied to instrument symbols; it is treated as instrument context rather than a required process isolation boundary."
    if classification == "instrument_only_context":
        return "HILT graph connects this source through a piping-to-instrument line; it is treated as instrument context rather than a required process isolation boundary."
    return "HILT graph connects this source through a companion line; it is treated as companion context rather than a required process isolation boundary."


def _mark_visible_source_labels(candidates, symbols, text_items):
    if not candidates:
        return candidates
    equipment_bbox_by_tag = {}
    for symbol in symbols:
        tag = str(symbol.get("tag") or _symbol_attr(symbol, "tag") or "").strip()
        bbox = _symbol_bbox(symbol)
        if tag and bbox and _norm(symbol.get("entity_type")) == "equipment":
            equipment_bbox_by_tag[_norm(tag)] = bbox

    visible_nozzle_labels_by_equipment = {}
    for equipment_tag, equipment_bbox in equipment_bbox_by_tag.items():
        labels = set()
        for text in text_items:
            label = str(text.get("text") or "").strip().upper()
            if not _looks_like_nozzle_label(label):
                continue
            if _bbox_near(text.get("bbox"), equipment_bbox, padding=180):
                labels.add(label)
        visible_nozzle_labels_by_equipment[equipment_tag] = labels

    marked = []
    for candidate in candidates:
        candidate = dict(candidate)
        equipment_tag = str(candidate.get("equipment_tag") or "")
        nozzle_id = str(candidate.get("source_nozzle_id") or "").strip().upper()
        graph_tag = str(candidate.get("source_component_tag") or "").strip()
        graph_nozzle = graph_tag.split("_", 1)[0].upper() if graph_tag else ""
        visible_labels = visible_nozzle_labels_by_equipment.get(_norm(equipment_tag), set())
        if nozzle_id:
            display_nozzle = nozzle_id
            confidence = "graph_nozzle_id"
        elif graph_nozzle in visible_labels:
            display_nozzle = graph_nozzle
            confidence = "visible_nozzle_text"
        else:
            display_nozzle = ""
            confidence = "graph_only_unlabeled_component"
        if display_nozzle and graph_tag.upper().startswith(f"{display_nozzle}_"):
            candidate["source_display_label"] = graph_tag
        else:
            candidate["source_display_label"] = f"{display_nozzle}_{equipment_tag}" if display_nozzle and equipment_tag else display_nozzle
        candidate["source_label_confidence"] = confidence
        marked.append(candidate)
    return marked


def _instrument_context_sources(candidate_pool, symbols, known_context_sources):
    by_source = {}
    for candidate in candidate_pool:
        source_key = _source_key(candidate)
        if not candidate.get("source_bbox"):
            continue
        by_source.setdefault(source_key, []).append(candidate)

    instrument_symbols = _instrument_symbols(symbols)

    context_sources = set()
    context_instruments = []
    for source_key, items in by_source.items():
        if source_key in known_context_sources:
            continue
        sample = items[0]
        if sample.get("source_hilt_lines"):
            continue
        if sample.get("source_label_confidence") != "graph_only_unlabeled_component":
            continue
        source_bbox = sample.get("source_bbox") or []
        nearby = _nearby_instruments(source_bbox, instrument_symbols)
        if not nearby:
            continue
        context_sources.add(source_key)
        context_instruments.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": sample.get("source_component_tag"),
                "source_bbox": source_bbox,
                "classification": "instrument_only_context",
                "reason": "Nozzle is visually co-located with instrument symbols; treated as instrument context rather than a required process isolation boundary.",
                "nearby_instruments": nearby[:5],
                "nearby_candidate_ids": [item.get("candidate_id") for item in _dedupe_source_candidates(items)[:5]],
            }
        )
    return context_sources, context_instruments


def _instrument_symbols(symbols):
    result = []
    for symbol in symbols or []:
        if _norm(symbol.get("entity_class")) not in INSTRUMENT_ENTITY_CLASSES:
            continue
        bbox = _symbol_bbox(symbol)
        if not bbox:
            continue
        tag = str(symbol.get("tag") or _symbol_attr(symbol, "tag") or "").strip()
        function_name = str(_symbol_attr(symbol, "FunctionName") or "").strip()
        function_number = str(_symbol_attr(symbol, "FunctionNumber") or "").strip()
        display_tag = tag or (f"{function_name}-{function_number}" if function_name and function_number else function_name)
        prefix = _tag_prefix(display_tag or function_name)
        if prefix and prefix not in INSTRUMENT_TAG_PREFIXES:
            continue
        result.append(
            {
                "uuid": symbol.get("uuid") or symbol.get("id") or symbol.get("source_id"),
                "bbox": bbox,
                "entity_class": symbol.get("entity_class"),
                "tag_number": display_tag or None,
            }
        )
    return result


def _mark_source_context(candidates, context_sources):
    if not context_sources:
        return candidates
    marked = []
    for candidate in candidates:
        candidate = dict(candidate)
        if _source_key(candidate) in context_sources:
            candidate["source_context_type"] = "non_process_context"
            candidate["source_context_reason"] = "source_nozzle_classified_as_non_process_context"
        marked.append(candidate)
    return marked


_norm = normalize_tag


