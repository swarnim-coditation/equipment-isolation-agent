from api_client import Plant360Client
from domain.classification import classify_candidate
from domain.enums import FlowRole, IsolationDecision
from flow import classify_nozzle_flow, role_for_source
from hilt_topology import resolve_nozzle_isolation, resolve_source_branch_isolation


MAX_PARALLEL_COMPANIONS_PER_SOURCE = 2
PARALLEL_COMPANION_DISTANCE_DELTA = 55.0
PARALLEL_COMPANION_MAX_DISTANCE = 170.0
MAX_BRANCH_COMPANIONS_PER_SOURCE = 2
BRANCH_COMPANION_MAX_DISTANCE = 800.0
BRANCH_COMPANION_MIN_SEPARATION = 140.0
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
HILT_SIGNAL_LINE_CLASSES = {"instrument_signal_line", "signal_line", "electrical_signal_line"}
HILT_CONTEXT_LINE_CLASSES = {"piping_to_instrument_line", "companion_line"} | HILT_SIGNAL_LINE_CLASSES
HILT_PROCESS_LINE_CLASSES = {"primary_process_line", "secondary_process_line", "main_process_line", "process_line"}


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
    context_instruments = hilt_context_items + visual_context_items
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


def _selectable_candidate_pool(candidate_pool, policy):
    del policy
    return [
        candidate
        for candidate in candidate_pool
        if candidate.get("policy_decision")
        in {IsolationDecision.AUTOMATIC.value, IsolationDecision.CONDITIONAL_MANUAL_REVIEW.value}
    ]


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


def _select_visually_nearest_per_source(candidate_pool, all_candidate_pool=None):
    all_candidate_pool = all_candidate_pool if all_candidate_pool is not None else candidate_pool
    if not candidate_pool and not all_candidate_pool:
        return [], {}
    by_source = {}
    for candidate in candidate_pool or []:
        if candidate.get("source_context_type"):
            continue
        by_source.setdefault(_source_key(candidate), []).append(candidate)
    all_by_source = {}
    for candidate in all_candidate_pool or []:
        if candidate.get("source_context_type"):
            continue
        all_by_source.setdefault(_source_key(candidate), []).append(candidate)

    selected = []
    companion_samples = []
    samples = []
    source_groups = []
    skipped_sources = []
    for source_key, all_items in all_by_source.items():
        if source_key in by_source:
            continue
        items = _dedupe_source_candidates(all_items)
        if not items:
            continue
        sample = items[0]
        skipped_sources.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "source_component_tag": sample.get("source_display_label"),
                "source_component_tag_raw": sample.get("source_component_tag"),
                "source_nozzle_id": sample.get("source_nozzle_id"),
                "source_label_confidence": sample.get("source_label_confidence"),
                "source_hilt_lines": sample.get("source_hilt_lines") or [],
                "min_candidate_depth": min(int(item.get("traversal_depth") or 99) for item in items),
                "candidate_count": len(items),
                "reason": "no_policy_selectable_candidate_for_source",
            }
        )
    for source_key, items in by_source.items():
        items = _dedupe_source_candidates(items)
        min_depth = min(int(item.get("traversal_depth") or 99) for item in items)
        if min_depth > 2:
            sample = items[0]
            skipped_sources.append(
                {
                    "equipment_tag": source_key[0],
                    "source_component": source_key[1],
                    "source_component_tag": sample.get("source_display_label"),
                    "source_component_tag_raw": sample.get("source_component_tag"),
                    "source_nozzle_id": sample.get("source_nozzle_id"),
                    "source_label_confidence": sample.get("source_label_confidence"),
                    "source_hilt_lines": sample.get("source_hilt_lines") or [],
                    "min_candidate_depth": min_depth,
                    "candidate_count": len(items),
                    "reason": "nearest_candidates_exceed_visual_selection_depth_limit",
                }
            )
            continue
        items.sort(key=_visual_sort_key)
        source_groups.append((min_depth, source_key, items))

    used_candidate_ids = set()
    for min_depth, source_key, items in sorted(source_groups, key=lambda item: (item[0], str(item[1]))):
        winner = items[0]
        if min_depth > 1:
            for item in items:
                candidate_id = _norm(item.get("candidate_id"))
                if candidate_id and candidate_id in used_candidate_ids:
                    continue
                winner = item
                break
        used_candidate_ids.add(_norm(winner.get("candidate_id")))
        selected.append(winner)
        companions = _parallel_companions(winner, items, used_candidate_ids)
        branch_companions = _branch_merge_companions(winner, items, used_candidate_ids)
        companions.extend(branch_companions)
        for companion in companions:
            used_candidate_ids.add(_norm(companion.get("candidate_id")))
            selected.append(companion)
        if companions:
            companion_samples.append(
                {
                    "equipment_tag": source_key[0],
                    "source_component": source_key[1],
                    "primary_candidate_id": winner.get("candidate_id"),
                    "companion_candidate_ids": [item.get("candidate_id") for item in companions],
                    "reason": "same_source_parallel_or_branch_merge_candidates",
                }
            )
        samples.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "selected_candidate_ids": [winner.get("candidate_id")],
                "selected_depths": [winner.get("traversal_depth")],
                "selected_source_visual_distances": [winner.get("source_visual_distance")],
            }
        )

    deduped = _dedupe_candidates(selected)
    deduped.sort(key=_visual_sort_key)
    for rank, candidate in enumerate(deduped, start=1):
        candidate["path_selection"] = {
            "mode": "nearest_exact_bbox_candidate_per_source_component",
            "primary_source_component_tag": candidate.get("source_component_tag"),
            "primary_source_component_id": candidate.get("source_component_id"),
            "selected_depth": candidate.get("traversal_depth"),
            "selected_source_visual_distance": candidate.get("source_visual_distance"),
            "rank": rank,
            "source_path_count": candidate.get("source_path_count", 1),
        }
    return deduped[:20], {
        "bbox_candidate_finder_mode": "nearest_exact_bbox_candidate_per_source_component",
        "bbox_source_visual_selection_samples": samples[:25],
        "bbox_parallel_companion_selection_count": sum(len(item.get("companion_candidate_ids") or []) for item in companion_samples),
        "bbox_parallel_companion_selection_samples": companion_samples[:25],
        "bbox_unselected_source_component_count": len(skipped_sources),
        "bbox_unselected_source_components": skipped_sources[:50],
    }


def _parallel_companions(winner, items, used_candidate_ids):
    winner_distance = winner.get("source_visual_distance")
    winner_depth = int(winner.get("traversal_depth") or 99)
    if winner_distance is None or winner_depth > 2:
        return []

    companions = []
    for item in items:
        candidate_id = _norm(item.get("candidate_id"))
        if not candidate_id or candidate_id in used_candidate_ids or candidate_id == _norm(winner.get("candidate_id")):
            continue
        if int(item.get("traversal_depth") or 99) != winner_depth:
            continue
        item_distance = item.get("source_visual_distance")
        if item_distance is None:
            continue
        if item_distance > PARALLEL_COMPANION_MAX_DISTANCE:
            continue
        if item_distance - winner_distance > PARALLEL_COMPANION_DISTANCE_DELTA:
            continue
        companion = dict(item)
        companion["parallel_companion"] = True
        companion["reason"] = (
            f"{str(companion.get('reason') or '').rstrip('.')}. Selected as close parallel-branch companion "
            f"for source component {winner.get('source_component_tag')}"
        )
        companions.append(companion)
        if len(companions) >= MAX_PARALLEL_COMPANIONS_PER_SOURCE:
            break
    return companions


def _branch_merge_companions(winner, items, used_candidate_ids):
    winner_depth = int(winner.get("traversal_depth") or 99)
    winner_distance = winner.get("source_visual_distance")
    winner_direction = _direction_sector(winner.get("source_bbox"), winner.get("bbox"))
    winner_center = _bbox_center(winner.get("bbox"))
    if winner_depth > 2 or winner_distance is None or not winner_direction or not winner_center:
        return []

    companions = []
    for item in items:
        candidate_id = _norm(item.get("candidate_id"))
        if not candidate_id or candidate_id in used_candidate_ids or candidate_id == _norm(winner.get("candidate_id")):
            continue
        if int(item.get("traversal_depth") or 99) != winner_depth:
            continue
        item_distance = item.get("source_visual_distance")
        if item_distance is None or item_distance > BRANCH_COMPANION_MAX_DISTANCE:
            continue
        item_direction = _direction_sector(item.get("source_bbox"), item.get("bbox"))
        if not item_direction or item_direction == winner_direction:
            continue
        item_center = _bbox_center(item.get("bbox"))
        if not item_center or _point_distance(winner_center, item_center) < BRANCH_COMPANION_MIN_SEPARATION:
            continue
        companion = dict(item)
        companion["branch_merge_companion"] = True
        companion["reason"] = (
            f"{str(companion.get('reason') or '').rstrip('.')}. Selected as same-nozzle branch/merge companion "
            f"for source component {winner.get('source_component_tag')}"
        )
        companions.append(companion)
        if len(companions) >= MAX_BRANCH_COMPANIONS_PER_SOURCE:
            break
    return companions


def _detect_unclassified_parallel_branch_checks(candidate_pool, text_items):
    checks = []
    by_source = {}
    for candidate in candidate_pool:
        if candidate.get("source_context_type"):
            continue
        if candidate.get("bbox") and candidate.get("source_bbox"):
            by_source.setdefault(_source_key(candidate), []).append(candidate)

    seen = set()
    for source_key, items in by_source.items():
        items = [item for item in _dedupe_source_candidates(items) if int(item.get("traversal_depth") or 99) <= 2]
        if len(items) < 2:
            continue
        items.sort(key=_visual_sort_key)
        cluster = items[:3]
        xs = [bbox[0] for bbox in (item.get("bbox") for item in cluster) if bbox]
        ys = [bbox[1] for bbox in (item.get("bbox") for item in cluster) if bbox]
        if not xs or not ys:
            continue
        min_x = min(xs) - 30
        max_x = max(bbox[0] + bbox[2] for bbox in (item.get("bbox") for item in cluster) if bbox) + 30
        min_y = min(ys)
        max_y = max(bbox[1] + bbox[3] for bbox in (item.get("bbox") for item in cluster) if bbox)
        for text in text_items:
            bbox = text.get("bbox") or []
            if len(bbox) != 4:
                continue
            x, y, w, h = bbox
            value = str(text.get("text") or "").strip().lower()
            if not value or "dec" not in value:
                continue
            if not (min_x <= x <= max_x and max_y + 8 <= y <= max_y + 80):
                continue
            key = (source_key, tuple(bbox), value)
            if key in seen:
                continue
            seen.add(key)
            checks.append(
                {
                    "equipment_tag": source_key[0],
                    "source_component": source_key[1],
                    "source_component_tag": cluster[0].get("source_component_tag"),
                    "uuid": text.get("id"),
                    "bbox": bbox,
                    "entity_class": "suspected_unclassified_parallel_branch_valve",
                    "reason": "Possible lower bypass-path valve was detected as OCR/text, not as a valve symbol. Manual confirmation is required before treating this parallel branch as isolated.",
                    "near_candidate_ids": [item.get("candidate_id") for item in cluster],
                }
            )
    return checks


def _extract_hilt_source_context(payload):
    graph = (payload or {}).get("hilt_graph") if isinstance(payload, dict) else None
    if not isinstance(graph, dict):
        return {"lines_by_endpoint": {}, "nodes_by_id": {}, "node_count": 0, "link_count": 0, "source_line_context_count": 0}

    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    nodes_by_id = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id") or (node.get("payload") or {}).get("id") or (node.get("payload") or {}).get("source_id")
        if node_id:
            nodes_by_id[_norm(node_id)] = _hilt_node_summary(node)

    lines_by_endpoint = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        payload = link.get("payload") or {}
        source = link.get("source") or payload.get("from")
        target = link.get("target") or payload.get("to")
        line = _hilt_line_summary(link, nodes_by_id)
        for endpoint in (source, target):
            if endpoint:
                lines_by_endpoint.setdefault(_norm(endpoint), []).append(line)

    return {
        "lines_by_endpoint": lines_by_endpoint,
        "nodes_by_id": nodes_by_id,
        "node_count": len(nodes),
        "link_count": len(links),
        "source_line_context_count": sum(len(items) for items in lines_by_endpoint.values()),
    }


def _hilt_node_summary(node):
    payload = node.get("payload") or {}
    bbox_location = payload.get("bounding_box_location") or {}
    return {
        "uuid": node.get("id") or payload.get("id") or payload.get("source_id"),
        "entity_type": payload.get("entity_type"),
        "entity_class": payload.get("entity_class"),
        "tag_number": _hilt_text_value(payload.get("text")) or _attr_value(payload.get("attributes"), "tag"),
        "bbox": _hilt_bbox(payload),
        "center": [bbox_location.get("x"), bbox_location.get("y")] if bbox_location else [],
    }


def _hilt_line_summary(link, nodes_by_id):
    payload = link.get("payload") or {}
    source = link.get("source") or payload.get("from")
    target = link.get("target") or payload.get("to")
    segment = payload.get("piping_network_segment") or {}
    system = payload.get("piping_network_system") or {}
    return {
        "line_id": payload.get("id") or payload.get("source_id"),
        "source": source,
        "target": target,
        "entity_type": payload.get("entity_type"),
        "entity_class": payload.get("entity_class"),
        "segment_id": segment.get("id"),
        "system_id": system.get("id"),
        "tag_number": _attr_value(segment.get("attributes"), "tag") or _attr_value(payload.get("attributes"), "tag"),
        "text": _hilt_text_value(payload.get("text")),
        "graphical_lines": _hilt_graphical_lines(payload.get("graphical_lines")),
        "connected_nodes": [nodes_by_id.get(_norm(value)) for value in (source, target) if nodes_by_id.get(_norm(value))],
    }


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


def _merge_hilt_topology(candidates, hilt_isolation_map, flow_roles, equipment_tag, policy):
    """Override nozzle->valve assignments with HILT piping-topology results.
    For each nozzle HILT resolves, replace any JanusGraph-derived candidate for
    that nozzle with the topologically-connected HILT valve. Nozzles HILT did not
    resolve keep their existing candidate (if any). If one physical valve covers
    multiple nozzles, keep one candidate and accumulate all source paths."""
    if not hilt_isolation_map:
        return candidates
    covered_nozzles = {tag for tag, valves in hilt_isolation_map.items() if valves}
    merged = []
    for candidate in candidates:
        src = candidate.get("source_component_tag")
        if src and src in covered_nozzles:
            continue  # replaced by the HILT-authoritative valve for this nozzle
        merged.append(candidate)
    hilt_by_valve_id = {}
    for nozzle_tag, valves in hilt_isolation_map.items():
        if not valves:
            continue
        seen_valve_ids: set[str] = set()
        for valve in valves:
            vid = str(valve.get("valve_id") or "")
            if not vid:
                continue
            if vid in seen_valve_ids:
                continue
            seen_valve_ids.add(vid)
            if vid not in hilt_by_valve_id:
                hilt_by_valve_id[vid] = _hilt_valve_candidate(valve, nozzle_tag, equipment_tag, flow_roles, policy)
                continue
            _append_hilt_source_path(hilt_by_valve_id[vid], valve, nozzle_tag, flow_roles)
    merged.extend(hilt_by_valve_id.values())
    return merged


def _merge_hilt_source_branches(candidates, hilt_branch_obligations, flow_roles, equipment_tag, policy):
    covered_sources = {
        (str(source.get("equipment_tag") or equipment_tag), str(source.get("source_component") or source.get("source_component_tag") or ""))
        for source in hilt_branch_obligations or []
        if source.get("branches")
    }
    merged = [
        candidate
        for candidate in candidates
        if (str(candidate.get("equipment_tag") or ""), str(candidate.get("source_component_id") or candidate.get("source_component_tag") or ""))
        not in covered_sources
    ]
    for source in hilt_branch_obligations or []:
        source_component = str(source.get("source_component") or source.get("source_component_tag") or "")
        for branch in source.get("branches") or []:
            if branch.get("status") != "isolated" or not branch.get("valve"):
                continue
            merged.append(
                _hilt_valve_candidate(
                    branch["valve"],
                    source.get("source_component_tag") or source_component,
                    source.get("equipment_tag") or equipment_tag,
                    flow_roles,
                    policy,
                    source_component_id=source_component,
                    source_visual_id=source.get("source_visual_id"),
                    source_bbox=source.get("source_bbox") or [],
                    branch=branch,
                )
            )
    return _dedupe_candidates(merged)


def _hilt_source_entries(candidate_pool):
    entries = {}
    for candidate in candidate_pool or []:
        if candidate.get("source_context_type"):
            continue
        source_visual_id = str(candidate.get("source_visual_node_id") or candidate.get("source_visual_id") or "").strip()
        if not source_visual_id:
            continue
        source_component = str(candidate.get("source_component_id") or candidate.get("source_component_tag") or "").strip()
        equipment_tag = str(candidate.get("equipment_tag") or "").strip()
        key = (equipment_tag, source_component, source_visual_id)
        if key in entries:
            continue
        entries[key] = {
            "equipment_tag": equipment_tag,
            "source_component_id": source_component,
            "source_component_tag": candidate.get("source_display_label") or candidate.get("source_component_tag") or source_component,
            "source_visual_id": source_visual_id,
            "source_visual_node_id": candidate.get("source_visual_node_id"),
            "source_bbox": candidate.get("source_bbox") or [],
            "source_type": "process",
        }
    return list(entries.values())


def _hilt_valve_candidate(
    valve,
    nozzle_tag,
    equipment_tag,
    flow_roles,
    policy,
    source_component_id=None,
    source_visual_id=None,
    source_bbox=None,
    branch=None,
):
    entity_class = valve.get("entity_class") or "valve"
    hops = int(valve.get("hop_distance") or 0)
    method = "close and lock valve" if "valve" in entity_class.lower() else "isolate and lock/tag"
    properties = {"entity_class": entity_class, "entity_type": valve.get("entity_type")}
    classification = classify_candidate(properties, entity_class, policy, method_text=method)
    return {
        "equipment_tag": equipment_tag,
        "source_component_tag": nozzle_tag,
        "source_component_id": source_component_id or nozzle_tag,
        "source_visual_id": source_visual_id,
        "source_bbox": source_bbox or [],
        "candidate_id": valve.get("valve_id"),
        "visual_id": valve.get("valve_id"),
        "tag_number": valve.get("tag") or None,
        "candidate_label": entity_class,
        "tag_type": "line",
        "energy_type": ["process"],
        "isolation_method": method,
        "matched_keywords": list(classification.matched_policy_classes or (entity_class,)),
        "policy_decision": classification.decision.value,
        "requires_manual_review": classification.requires_manual_review,
        "classification": classification.to_dict(),
        "traversal_depth": hops,
        "source_distance": None,
        "confidence": max(130 - hops * 10, 60),
        "reason": f"Connected to {nozzle_tag} via HILT piping topology ({hops} hops). Topology-authoritative.",
        "properties": properties,
        "property_preview": {},
        "bbox": valve.get("bbox") or [],
        "connectivity_source": "hilt_topology",
        "required_branch_isolation": bool(branch),
        "branch_id": (branch or {}).get("branch_id"),
        "branch_status": (branch or {}).get("status"),
        "branch_basis": (branch or {}).get("basis"),
        "branch_path_node_ids": (branch or {}).get("path_node_ids") or valve.get("path_node_ids") or [],
        "branch_path_node_classes": (branch or {}).get("path_node_classes") or [],
        "branch_context_devices": (branch or {}).get("context_devices") or [],
        "source_flow_role": role_for_source(flow_roles, nozzle_tag),
        "source_paths": [_hilt_source_path(nozzle_tag, hops, flow_roles, source_component_id=source_component_id, source_visual_id=source_visual_id, branch=branch)],
        "source_path_count": 1,
    }


def _append_hilt_source_path(candidate, valve, nozzle_tag, flow_roles):
    hops = int(valve.get("hop_distance") or 0)
    candidate.setdefault("source_paths", []).append(_hilt_source_path(nozzle_tag, hops, flow_roles))
    candidate["source_path_count"] = len(candidate["source_paths"])
    roles = {
        path.get("source_flow_role")
        for path in candidate["source_paths"]
        if path.get("source_flow_role") and path.get("source_flow_role") != FlowRole.UNKNOWN.value
    }
    if len(roles) == 1:
        candidate["source_flow_role"] = next(iter(roles))
    elif len(roles) > 1:
        candidate["source_flow_role"] = FlowRole.BIDIRECTIONAL.value
    candidate["reason"] = (
        f"Connected to {candidate['source_path_count']} nozzle(s) via HILT piping topology. "
        "Topology-authoritative."
    )


def _hilt_source_path(nozzle_tag, hops, flow_roles, source_component_id=None, source_visual_id=None, branch=None):
    return {
        "source_component_tag": nozzle_tag,
        "source_component_id": source_component_id or nozzle_tag,
        "source_visual_id": source_visual_id,
        "branch_id": (branch or {}).get("branch_id"),
        "branch_status": (branch or {}).get("status"),
        "source_flow_role": role_for_source(flow_roles, nozzle_tag),
        "reason": "hilt_topology",
        "traversal_depth": hops,
    }


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

    if graph_only_unlabeled and _has_instrument_context_evidence(sample, lines, instrument_symbols):
        return "instrument_signal_context" if has_signal_line else "instrument_adjacent_context"

    return ""


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


def _dedupe_source_candidates(items):
    merged = {}
    for item in items:
        key = _norm(item.get("visual_id") or item.get("candidate_id"))
        if key not in merged or _visual_sort_key(item) < _visual_sort_key(merged[key]):
            merged[key] = item
    return list(merged.values())


def _dedupe_candidates(candidates):
    merged = {}
    for candidate in candidates:
        key = (candidate.get("equipment_tag"), _norm(candidate.get("visual_id") or candidate.get("candidate_id")))
        path = {
            "source_component_tag": candidate.get("source_component_tag"),
            "source_component_id": candidate.get("source_component_id"),
            "source_visual_id": candidate.get("source_visual_id"),
            "branch_id": candidate.get("branch_id"),
            "branch_status": candidate.get("branch_status"),
            "source_name": candidate.get("source_name"),
            "traversal_depth": candidate.get("traversal_depth"),
            "source_visual_distance": candidate.get("source_visual_distance"),
            "reason": candidate.get("reason"),
        }
        if key not in merged:
            copied = dict(candidate)
            copied["source_paths"] = [path]
            copied["source_path_count"] = 1
            merged[key] = copied
            continue
        existing = merged[key]
        existing["source_paths"].append(path)
        existing["source_path_count"] = len(existing["source_paths"])
        if _visual_sort_key(candidate) < _visual_sort_key(existing):
            for field in ("source_component_tag", "source_component_id", "source_visual_id", "source_bbox", "source_visual_node_id", "source_visual_distance", "traversal_depth", "source_name", "confidence", "reason"):
                existing[field] = candidate.get(field)
    return list(merged.values())


def _visual_sort_key(candidate):
    return (
        0 if candidate.get("bbox") and candidate.get("source_bbox") else 1,
        _distance_sort_value(candidate.get("source_visual_distance")),
        int(candidate.get("traversal_depth") or 99),
        -int(candidate.get("confidence") or 0),
        str(candidate.get("tag_number") or ""),
        str(candidate.get("candidate_id") or ""),
    )


def _source_key(candidate):
    return (str(candidate.get("equipment_tag") or ""), str(candidate.get("source_component_id") or candidate.get("source_component_tag") or ""))


def _bbox_distance(source_bbox, candidate_bbox):
    source_center = _bbox_center(source_bbox)
    candidate_center = _bbox_center(candidate_bbox)
    if not source_center or not candidate_center:
        return None
    return round(_point_distance(source_center, candidate_center), 4)


def _direction_sector(source_bbox, candidate_bbox):
    source_center = _bbox_center(source_bbox)
    candidate_center = _bbox_center(candidate_bbox)
    if not source_center or not candidate_center:
        return None
    sx, sy = source_center
    cx, cy = candidate_center
    dx = cx - sx
    dy = cy - sy
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def _point_distance(first, second):
    fx, fy = first
    sx, sy = second
    return ((fx - sx) ** 2 + (fy - sy) ** 2) ** 0.5


def _bbox_center(bbox):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    return (float(bbox[0]) + float(bbox[2]) / 2.0, float(bbox[1]) + float(bbox[3]) / 2.0)


def _bbox_near(inner_bbox, outer_bbox, padding=0):
    inner_center = _bbox_center(inner_bbox)
    if not inner_center or not isinstance(outer_bbox, list) or len(outer_bbox) != 4:
        return False
    x, y, w, h = [float(value) for value in outer_bbox]
    cx, cy = inner_center
    return x - padding <= cx <= x + w + padding and y - padding <= cy <= y + h + padding


def _looks_like_nozzle_label(value):
    value = str(value or "").strip().upper()
    if len(value) < 2 or value[0] != "N":
        return False
    return value[1:].isdigit()


def _dedupe_hilt_lines(lines):
    merged = {}
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        key = line.get("line_id") or (line.get("source"), line.get("target"), line.get("entity_class"))
        if key and key not in merged:
            merged[key] = line
    return list(merged.values())


def _attr_value(attributes, name):
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        if str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return None


def _hilt_text_value(items):
    values = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value not in (None, "", []):
            values.append(str(value))
    return ", ".join(values) if values else None


def _hilt_graphical_lines(lines):
    result = []
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        p1 = line.get("p1") or {}
        p2 = line.get("p2") or {}
        if p1.get("x") is None or p1.get("y") is None or p2.get("x") is None or p2.get("y") is None:
            continue
        result.append(
            {
                "point1": [round(float(p1.get("x")), 4), round(float(p1.get("y")), 4)],
                "point2": [round(float(p2.get("x")), 4), round(float(p2.get("y")), 4)],
                "line_type": line.get("line_type"),
            }
        )
    return result


def _hilt_nodes_by_id(hilt_payload, y_flip):
    graph = (hilt_payload or {}).get("hilt_graph") if isinstance(hilt_payload, dict) else None
    if not isinstance(graph, dict):
        return {}
    nodes = {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        payload = node.get("payload") or {}
        node_id = node.get("id") or payload.get("id") or payload.get("source_id")
        if not node_id:
            continue
        summary = {
            "uuid": str(node_id),
            "source_id": payload.get("source_id"),
            "entity_type": payload.get("entity_type"),
            "entity_class": payload.get("entity_class"),
            "tag_number": _hilt_text_value(payload.get("text")) or _attr_value(payload.get("attributes"), "tag"),
            "bbox": _hilt_bbox(payload, y_flip=y_flip),
        }
        for key in (node_id, payload.get("id"), payload.get("source_id"), payload.get("uuid")):
            if key:
                nodes[_norm(key)] = summary
    return nodes


def _find_hilt_node(hilt_node_by_id, *values):
    for value in values:
        if value in (None, "", []):
            continue
        node = hilt_node_by_id.get(_norm(value))
        if node:
            return node
    return None


def _find_hilt_node_by_tag(hilt_node_by_id, tag):
    normalized = _norm(tag)
    if not normalized:
        return None
    for node in hilt_node_by_id.values():
        if _norm(node.get("tag_number")) == normalized:
            return node
    return None


def _hilt_bbox(payload, y_flip=None):
    location = payload.get("bounding_box_location") or {}
    width = payload.get("bounding_box_width")
    height = payload.get("bounding_box_height")
    if location.get("x") is None or location.get("y") is None or width is None or height is None:
        return []
    cx = float(location.get("x"))
    cy = float(location.get("y"))
    w = float(width)
    h = float(height)
    x = cx - w / 2.0
    y = (float(y_flip) - cy - h / 2.0) if y_flip is not None else (cy - h / 2.0)
    return [int(round(x)), int(round(y)), int(round(w)), int(round(h))]


def _distance_sort_value(value):
    return float(value) if value is not None else 999999.0


def _symbol_attr(symbol, name):
    target = str(name or "").strip().lower().replace("_", " ")
    for attr in symbol.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        attr_name = str(attr.get("name") or "").strip().lower().replace("_", " ")
        if attr_name == target:
            return attr.get("value")
    return None


def _extract_symbols(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "symbols", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_symbols(value)
            if nested:
                return nested
    symbol_json = payload.get("symbol_json")
    if isinstance(symbol_json, list):
        return symbol_json
    if isinstance(symbol_json, dict):
        symbols = []
        for key, value in symbol_json.items():
            if isinstance(value, dict):
                symbol = dict(value)
                symbol.setdefault("uuid", key)
                symbols.append(symbol)
        return symbols
    return []


def _extract_text_items(payload):
    if not isinstance(payload, dict):
        return []
    text_json = payload.get("text_json")
    if not isinstance(text_json, dict):
        return []
    items = []
    for key, value in text_json.items():
        if not isinstance(value, dict):
            continue
        bbox = value.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        items.append(
            {
                "id": key,
                "bbox": [int(round(float(part))) for part in bbox],
                "text": value.get("text") or value.get("value") or value.get("ocr_text") or "",
            }
        )
    return items


def _symbol_bbox(symbol):
    if isinstance(symbol.get("bbox"), list) and len(symbol["bbox"]) == 4:
        return [int(round(float(value))) for value in symbol["bbox"]]
    keys = ("orig_x", "orig_y", "orig_bbox_width", "orig_bbox_height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    keys = ("x", "y", "width", "height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    return []


def _fallback_hilt_yflip(config, client, hilt_payload, debug):
    height = _hilt_image_height(hilt_payload)
    if height:
        debug["hilt_y_flip_source"] = "hilt_payload_image_height"
        return float(height)
    height = _job_image_height(config, client, debug)
    if height:
        debug["hilt_y_flip_source"] = "job_image_height"
        return float(height)
    debug["hilt_y_flip_source"] = "unavailable"
    return None


def _hilt_image_height(hilt_payload):
    if not isinstance(hilt_payload, dict):
        return None
    candidates = []
    for container in (hilt_payload, hilt_payload.get("hilt_graph") or {}, hilt_payload.get("metadata") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("image_height", "original_image_height", "page_height", "height"):
            value = container.get(key)
            number = _positive_number(value)
            if number and number > 500:
                candidates.append(number)
    return candidates[0] if candidates else None


def _job_image_height(config, client, debug):
    job_id = config.resolved_job_id
    if not job_id:
        return None
    try:
        job = client.get_json(f"/jobs/{job_id}")
    except Exception as exc:
        debug["hilt_y_flip_job_lookup_error"] = str(exc)
        return None
    file_id = job.get("input_file_image")
    if not file_id:
        debug["hilt_y_flip_image_error"] = "missing_input_file_image"
        return None
    try:
        content, _content_type = client.get_bytes(f"/uploads/{file_id}")
    except Exception as exc:
        debug["hilt_y_flip_image_error"] = str(exc)
        return None
    dimensions = _image_dimensions(content)
    if not dimensions:
        debug["hilt_y_flip_image_error"] = "unsupported_or_invalid_image"
        return None
    debug["hilt_y_flip_image_width"] = dimensions[0]
    debug["hilt_y_flip_image_height"] = dimensions[1]
    return dimensions[1]


def _image_dimensions(content):
    if not isinstance(content, (bytes, bytearray)):
        return None
    data = bytes(content)
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return (width, height) if width > 0 and height > 0 else None
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    return None


def _jpeg_dimensions(data):
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        while marker == 0xFF and index < len(data):
            marker = data[index]
            index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 7:
                return None
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return (width, height) if width > 0 and height > 0 else None
        index += length
    return None


def _positive_number(value):
    try:
        number = float(value)
    except Exception:
        return None
    return number if number > 0 else None


def _calibrate_hilt_yflip(hilt_nodes, symbols):
    """Derive the image height H such that image_y = H - hilt_y. Pairs HILT nodes
    with STLM symbols (STLM coords are image-space) by node id first, then by tag.
    Returns None if no pairs (calibration failed -> HILT merge is skipped)."""
    if not symbols:
        return None
    stlm_by_id = {}
    for sym in symbols:
        for key in ("source_id", "uuid", "id"):
            value = sym.get(key)
            if value:
                stlm_by_id[str(value).lower()] = sym
    stlm_by_tag = {}
    for sym in symbols:
        tag = sym.get("tag") or _symbol_attr(sym, "tag")
        if tag:
            stlm_by_tag[_norm(tag)] = sym

    heights = []
    for node in hilt_nodes:
        payload = node.get("payload") or {}
        loc = payload.get("bounding_box_location") or {}
        if loc.get("y") is None:
            continue
        sym = stlm_by_id.get(str(node.get("id") or "").lower())
        if sym is None:
            tag = _symbol_attr(payload, "tag")
            sym = stlm_by_tag.get(_norm(tag)) if tag else None
        if sym is None:
            continue
        sb = _symbol_bbox(sym)
        if not sb:
            continue
        stlm_center_y = sb[1] + sb[3] / 2.0
        heights.append(stlm_center_y + float(loc.get("y")))
    if not heights:
        return None
    return sum(heights) / len(heights)


def _norm(value):
    return str(value or "").strip().lower()


def _tag_prefix(value):
    result = []
    for char in str(value or "").strip().lower():
        if char.isalpha():
            result.append(char)
            continue
        break
    return "".join(result)
