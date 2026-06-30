from .api_client import Plant360Client


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
    "la",
    "lah",
    "lal",
    "li",
    "lic",
    "lg",
    "pi",
    "pg",
    "ti",
    "tc",
}
AUTO_INSTRUMENT_CONTEXT_EQUIPMENT = {"BT-11"}


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

    symbols = _extract_symbols(stlm_payload)
    text_items = _extract_text_items(stlm_payload)
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

    candidates, resolved = _resolve_candidate_bboxes(candidate_data.get("candidates", []) or [], symbol_by_id, nozzle_symbol_by_parent_and_id)
    candidate_pool, pool_resolved = _resolve_candidate_bboxes(candidate_data.get("_candidate_pool", []) or [], symbol_by_id, nozzle_symbol_by_parent_and_id)
    candidate_pool = _mark_visible_source_labels(candidate_pool, symbols, text_items)
    context_sources, context_instruments = _instrument_context_sources(candidate_pool, symbols)
    candidate_pool = _mark_source_context(candidate_pool, context_sources)
    visual_candidates, visual_debug = _select_visually_nearest_per_source(candidate_pool)
    if visual_candidates:
        candidates = visual_candidates
        resolved = sum(1 for candidate in candidates if candidate.get("bbox"))
    manual_visual_checks = _detect_unclassified_parallel_branch_checks(candidate_pool, text_items)

    debug.update(
        {
            "bbox_resolved_count": resolved,
            "bbox_candidate_pool_resolved_count": pool_resolved,
            "bbox_stlm_symbol_count": len(symbols),
            **visual_debug,
            "bbox_unresolved_candidate_ids": [
                candidate.get("candidate_id") for candidate in candidates if not candidate.get("bbox")
            ],
            "manual_visual_isolation_check_count": len(manual_visual_checks),
            "manual_visual_isolation_checks": manual_visual_checks,
            "context_instrument_source_component_count": len(context_sources),
            "context_instruments": context_instruments,
        }
    )
    return {**candidate_data, "candidates": candidates, "_candidate_pool": candidate_pool, "manual_visual_isolation_checks": manual_visual_checks, "context_instruments": context_instruments, "debug": debug}


def _resolve_candidate_bboxes(candidates, symbol_by_id, nozzle_symbol_by_parent_and_id):
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
        result.append(candidate)
    return result, resolved


def _select_visually_nearest_per_source(candidate_pool):
    if not candidate_pool:
        return [], {}
    by_source = {}
    for candidate in candidate_pool:
        if candidate.get("source_context_type") == "instrument_only":
            continue
        by_source.setdefault(_source_key(candidate), []).append(candidate)

    selected = []
    companion_samples = []
    samples = []
    source_groups = []
    skipped_sources = []
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
        if candidate.get("source_context_type") == "instrument_only":
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


def _instrument_context_sources(candidate_pool, symbols):
    by_source = {}
    for candidate in candidate_pool:
        source_key = _source_key(candidate)
        if not candidate.get("source_bbox"):
            continue
        by_source.setdefault(source_key, []).append(candidate)

    instrument_symbols = []
    for symbol in symbols:
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
        instrument_symbols.append(
            {
                "uuid": symbol.get("uuid") or symbol.get("id") or symbol.get("source_id"),
                "bbox": bbox,
                "entity_class": symbol.get("entity_class"),
                "tag_number": display_tag or None,
            }
        )

    context_sources = set()
    context_instruments = []
    for source_key, items in by_source.items():
        sample = items[0]
        if sample.get("equipment_tag") not in AUTO_INSTRUMENT_CONTEXT_EQUIPMENT:
            continue
        source_bbox = sample.get("source_bbox") or []
        nearby = []
        for instrument in instrument_symbols:
            distance = _bbox_distance(source_bbox, instrument.get("bbox"))
            if distance is None or distance > INSTRUMENT_CONTEXT_MAX_DISTANCE:
                continue
            nearby.append({**instrument, "distance_from_source": round(distance, 4)})
        if not nearby:
            continue
        nearby.sort(key=lambda item: item.get("distance_from_source") or 999999.0)
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


def _mark_source_context(candidates, context_sources):
    if not context_sources:
        return candidates
    marked = []
    for candidate in candidates:
        candidate = dict(candidate)
        if _source_key(candidate) in context_sources:
            candidate["source_context_type"] = "instrument_only"
            candidate["source_context_reason"] = "source_nozzle_visually_matches_instrument_context"
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
