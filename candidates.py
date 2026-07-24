from domain.classification import class_matches, classify_candidate, normalize_class
from domain.enums import IsolationDecision
from domain.models import BBox, IsolationCandidate
from domain.topology import FAR_DISTANCE
from domain.topology import tag_prefix as _tag_prefix


VALVE_KEYWORDS = {
    "valve",
    "generic_inline_valve",
    "gate_valve",
    "ball_valve",
    "globe_valve",
    "check_valve",
    "control_valve",
    "undefined_valve",
}
NON_DEVICE_LABELS = {"equipment", "loop", "pdffile", "pns", "pnsg", "plant", "section", "tagnode", "unit"}
NON_DEVICE_TYPES = {"equipment", "loop", "pns", "pnsg", "plant", "section", "tagnode", "unit"}
MAX_TOTAL_CANDIDATES = 20


def find_candidates(boundary_data, policy):
    raw_candidates = []
    skipped_count = 0
    selected_equipment_nodes = []
    for boundary in boundary_data.get("equipment_boundaries", []):
        equipment = boundary.get("equipment", {}) or {}
        selected_equipment_nodes.append(
            {
                "id": equipment.get("id"),
                "label": equipment.get("label"),
                "properties": equipment.get("properties") or {},
            }
        )
        equipment_tag = _tag(equipment.get("properties", {}) or {}) or str(equipment.get("id"))
        for component_boundary in boundary.get("component_boundaries", []):
            component = component_boundary.get("component", {}) or {}
            component_props = component.get("properties", {}) or {}
            source_component_id = component.get("id")
            source_component_tag = _tag(component_props) or str(source_component_id)
            sources = []
            sources.extend(("component direct neighbor", item) for item in component_boundary.get("direct_neighbors", []) or [])
            sources.extend(("component traversal sample", item) for item in component_boundary.get("traversal_sample", []) or [])
            for source_name, vertex in sources:
                candidate = _candidate_from_vertex(
                    equipment_tag,
                    source_component_tag,
                    source_component_id,
                    component_props,
                    vertex,
                    source_name,
                    policy,
                )
                if candidate:
                    raw_candidates.append(candidate)
                else:
                    skipped_count += 1

    selectable_candidates = [
        candidate
        for candidate in raw_candidates
        if candidate.get("policy_decision")
        in {IsolationDecision.AUTOMATIC.value, IsolationDecision.CONDITIONAL_MANUAL_REVIEW.value}
    ]
    selected, source_selection_samples = _select_nearest_per_source(selectable_candidates)
    deduped = _dedupe_candidates(selected)
    deduped.sort(key=_candidate_sort_key)
    ranked = deduped[:MAX_TOTAL_CANDIDATES]
    for rank, candidate in enumerate(ranked, start=1):
        candidate["path_selection"] = {
            "mode": "nearest_isolation_candidate_per_source_component",
            "primary_source_component_tag": candidate.get("source_component_tag"),
            "primary_source_component_id": candidate.get("source_component_id"),
            "selected_depth": candidate.get("traversal_depth"),
            "rank": rank,
            "source_path_count": candidate.get("source_path_count", 1),
        }

    return {
        "error": False,
        "total_candidates": len(ranked),
        "all_candidates_before_ranking": len(deduped),
        "candidates": ranked,
        "_candidate_pool": raw_candidates,
        "selected_equipment_nodes": selected_equipment_nodes,
        "context": boundary_data.get("context") or {},
        "debug": {
            "candidate_finder_mode": "local_nearest_boundary_candidate_per_source_component",
            "raw_candidate_count_before_dedupe": len(raw_candidates),
            "candidate_pool_count": len(raw_candidates),
            "selectable_candidate_count": len(selectable_candidates),
            "conditional_manual_review_candidate_count": sum(
                1 for candidate in raw_candidates if candidate.get("policy_decision") == IsolationDecision.CONDITIONAL_MANUAL_REVIEW.value
            ),
            "source_component_selection_samples": source_selection_samples[:25],
            "deduped_candidate_count": len(deduped),
            "skipped_count": skipped_count,
            "traversal_limit_hit": boundary_data.get("traversal_limit_hit"),
        },
    }


def _candidate_from_vertex(equipment_tag, source_component_tag, source_component_id, source_component_props, vertex, source_name, policy):
    properties = vertex.get("properties", {}) or {}
    label = _norm(vertex.get("label"))
    vertex_type = _norm(properties.get("type"))
    if label in NON_DEVICE_LABELS or vertex_type in NON_DEVICE_TYPES:
        return None
    classification = classify_candidate(properties, vertex.get("label"), policy)
    if classification.decision == IsolationDecision.EXCLUDED:
        return None
    keywords = list(classification.matched_policy_classes)
    if not keywords:
        return None

    depth = int(vertex.get("traversal_depth") or 1)
    if depth > policy.max_traversal_depth:
        return None

    candidate_id = vertex.get("id")
    visual_id = _first_property(properties, ("node_id", "source_id", "uuid", "name")) or str(candidate_id)
    source_visual_id = _first_property(source_component_props, ("node_id", "source_id", "uuid", "name")) or str(source_component_id)
    tag_number = _tag(properties)
    source_distance = _point_distance(source_component_props, properties)
    method = "close and lock valve" if any(
        class_matches(keyword, valve_keyword) for keyword in keywords for valve_keyword in VALVE_KEYWORDS
    ) else "isolate and lock/tag"
    classification = classify_candidate(properties, vertex.get("label"), policy, method_text=method, tag_prefix=_tag_prefix(tag_number))
    confidence = 120 - (depth * 20)
    if source_name == "component direct neighbor":
        confidence += 15

    candidate = IsolationCandidate(
        equipment_tag=equipment_tag,
        source_component_tag=source_component_tag,
        source_component_id=source_component_id,
        candidate_id=candidate_id,
        visual_id=visual_id,
        candidate_label=str(vertex.get("label") or ""),
        tag_number=tag_number,
        isolation_method=method,
        matched_keywords=tuple(keywords),
        classification=classification,
        traversal_depth=depth,
        reason=f"Matched {', '.join(keywords)} at depth {depth} in {source_name} near {source_component_tag}",
        properties=properties,
        bbox=BBox.from_any([]),
        extra={
            "source_visual_id": source_visual_id,
            "source_parent_id": source_component_props.get("parent_id") or source_component_props.get("parent"),
            "source_nozzle_id": source_component_props.get("Nozzle Id") or source_component_props.get("nozzle_id"),
            "cnvrt_id": _first_property(properties, ("cnvrt_id", "cnvrtId", "CNVRT_ID")),
            "unit_name": properties.get("unit_name"),
            "tag_type": "line",
            "energy_type": ["process"],
            "source_distance": source_distance,
            "source_name": source_name,
            "confidence": confidence,
            "property_preview": {key: properties[key] for key in sorted(properties)[:12] if properties.get(key) is not None},
        },
    )
    return candidate.to_dict()


def _select_nearest_per_source(candidates):
    by_source = {}
    for candidate in candidates:
        source_key = (candidate.get("equipment_tag"), candidate.get("source_component_id") or candidate.get("source_component_tag"))
        by_source.setdefault(source_key, []).append(candidate)
    selected = []
    samples = []
    for source_key, items in by_source.items():
        items.sort(key=_candidate_sort_key)
        winner = items[0]
        selected.append(winner)
        samples.append(
            {
                "equipment_tag": source_key[0],
                "source_component": source_key[1],
                "candidate_count": len(items),
                "selected_candidate_ids": [winner.get("candidate_id")],
                "selected_depths": [winner.get("traversal_depth")],
            }
        )
    return selected, samples


def _dedupe_candidates(candidates):
    merged = {}
    for candidate in candidates:
        key = (candidate.get("equipment_tag"), _norm(candidate.get("visual_id") or candidate.get("candidate_id")))
        path = _path_trace(candidate)
        if key not in merged:
            copied = dict(candidate)
            copied["source_paths"] = [path]
            copied["source_path_count"] = 1
            merged[key] = copied
            continue
        existing = merged[key]
        existing["source_paths"].append(path)
        existing["source_path_count"] = len(existing["source_paths"])
        if _candidate_sort_key(candidate) < _candidate_sort_key(existing):
            for field in ("source_component_tag", "source_component_id", "traversal_depth", "source_name", "confidence", "reason"):
                existing[field] = candidate.get(field)
    return list(merged.values())


def _candidate_sort_key(candidate):
    return (
        int(candidate.get("traversal_depth") or 99),
        _distance_sort_value(candidate.get("source_distance")),
        -int(candidate.get("confidence") or 0),
        str(candidate.get("tag_number") or ""),
        str(candidate.get("candidate_id") or ""),
    )


def _path_trace(candidate):
    return {
        "source_component_tag": candidate.get("source_component_tag"),
        "source_component_id": candidate.get("source_component_id"),
        "source_name": candidate.get("source_name"),
        "traversal_depth": candidate.get("traversal_depth"),
        "source_distance": candidate.get("source_distance"),
        "reason": candidate.get("reason"),
    }


def _tag(properties):
    for key in ("tag_number", "tag", "name", "label", "equipment_number", "Equipment Name"):
        value = properties.get(key)
        if value and not _looks_like_uuid(value):
            return str(value)
    return None


def _first_property(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return None


def _point_distance(source_props, candidate_props):
    sx = _number(source_props.get("x_pos") or source_props.get("x"))
    sy = _number(source_props.get("y_pos") or source_props.get("y"))
    cx = _number(candidate_props.get("x_pos") or candidate_props.get("x"))
    cy = _number(candidate_props.get("y_pos") or candidate_props.get("y"))
    if sx is None or sy is None or cx is None or cy is None:
        return None
    return round(((sx - cx) ** 2 + (sy - cy) ** 2) ** 0.5, 4)


def _distance_sort_value(value):
    return float(value) if value is not None else FAR_DISTANCE


def _number(value):
    try:
        return float(value)
    except Exception:
        return None


def _looks_like_uuid(value):
    parts = str(value or "").strip().split("-")
    return len(parts) == 5 and [len(part) for part in parts] == [8, 4, 4, 4, 12]


def _norm(value):
    return normalize_class(value)


