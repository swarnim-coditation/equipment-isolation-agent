from __future__ import annotations

from collections import deque

from domain.classification import class_matches, classify_candidate, normalize_class
from domain.enums import IsolationDecision
from domain.topology import PROCESS_LINE_CLASSES


BACKPRESSURE_CONTEXT_CLASSES = {"check_valve", "control_valve"}
RELIEF_TERMS = ("bleed", "vent", "drain", "open_vent")
POSITIVE_TERMS = ("blind", "spade", "spectacle", "flange", "blank_flange", "line_break_point", "disconnect", "breaker")
MAX_ENVELOPE_HOPS = 14
MAX_SCHEME_LOOKAHEAD_HOPS = 10


def analyze_isolation_schemes_and_relief(data: dict, config) -> dict:
    hilt_payload = data.get("_hilt_payload") or {}
    graph = hilt_payload.get("hilt_graph") if isinstance(hilt_payload, dict) else None
    debug = dict(data.get("debug") or {})
    if not isinstance(graph, dict):
        result = _unavailable("missing_hilt_graph")
        return {**data, **result, "debug": {**debug, "relief_analysis_status": "unavailable"}}

    node_by_id, adj, link_by_pair = _hilt_index(graph)
    y_flip = debug.get("hilt_y_flip_calibrated")
    selected = _selected_barriers(data.get("candidates") or [])
    if not selected:
        result = _completed([], [], node_by_id, [], reason="no_selected_barriers")
        return {**data, **result, "debug": {**debug, "relief_analysis_status": "completed"}}

    envelope = _isolated_envelope(selected, adj, link_by_pair)
    schemes, scheme_relief_ids = _detect_schemes(selected, node_by_id, adj, config.policy, y_flip=y_flip)
    relief_candidates = _discover_relief_candidates(
        envelope,
        schemes,
        node_by_id,
        selected_barrier_ids={item["barrier_id"] for item in selected},
        scheme_relief_ids=scheme_relief_ids,
        policy=config.policy,
        y_flip=y_flip,
    )
    result = _completed(envelope, schemes, node_by_id, relief_candidates)
    debug.update(
        {
            "relief_analysis_status": "completed",
            "isolated_envelope_node_count": len(result["isolated_envelope"]["node_ids"]),
            "detected_isolation_scheme_count": len(schemes),
            "relief_candidate_count": len(relief_candidates),
            "llm_relief_candidate_count": sum(1 for item in relief_candidates if item.get("classified_by") == "llm"),
        }
    )
    return {**data, **result, "debug": debug}


def _unavailable(error):
    return {
        "isolated_envelope": {"status": "unavailable", "error": error, "node_ids": [], "line_ids": [], "boundary_valve_ids": []},
        "detected_isolation_schemes": {"status": "unavailable", "error": error, "items": []},
        "relief_candidates": {"status": "unavailable", "error": error, "items": []},
    }


def _completed(envelope, schemes, node_by_id, relief_candidates=None, reason=""):
    relief_candidates = relief_candidates or []
    node_ids = sorted({node_id for item in envelope for node_id in item.get("node_ids", [])})
    line_ids = sorted({line_id for item in envelope for line_id in item.get("line_ids", []) if line_id})
    boundary_ids = sorted({barrier for item in envelope for barrier in item.get("boundary_valve_ids", []) if barrier})
    unresolved = [item for item in envelope if item.get("status") == "unresolved"]
    return {
        "isolated_envelope": {
            "status": "completed",
            "reason": reason,
            "node_ids": node_ids,
            "line_ids": line_ids,
            "boundary_valve_ids": boundary_ids,
            "unresolved_paths": unresolved,
            "node_count": len(node_ids),
            "line_count": len(line_ids),
        },
        "detected_isolation_schemes": {
            "status": "completed",
            "items": schemes,
            "summary": _scheme_summary(schemes),
        },
        "relief_candidates": {
            "status": "completed",
            "items": relief_candidates,
            "summary": _relief_summary(relief_candidates),
        },
    }


def _hilt_index(graph):
    node_by_id = {}
    for node in graph.get("nodes") or []:
        node_id = _node_id(node)
        if node_id:
            node_by_id[node_id] = node

    adj = {}
    link_by_pair = {}
    for link in graph.get("links") or []:
        payload = link.get("payload") or {}
        if normalize_class(payload.get("entity_class")) not in PROCESS_LINE_CLASSES:
            continue
        source = str(link.get("source") or payload.get("from") or "").strip()
        target = str(link.get("target") or payload.get("to") or "").strip()
        if not source or not target:
            continue
        adj.setdefault(source, set()).add(target)
        adj.setdefault(target, set()).add(source)
        summary = {
            "id": payload.get("id") or payload.get("source_id"),
            "entity_class": payload.get("entity_class"),
            "entity_type": payload.get("entity_type"),
        }
        link_by_pair[(source, target)] = summary
        link_by_pair[(target, source)] = summary
    return node_by_id, adj, link_by_pair


def _selected_barriers(candidates):
    selected = []
    seen = set()
    for candidate in candidates or []:
        barrier_id = str(candidate.get("candidate_id") or candidate.get("visual_id") or "").strip()
        if not barrier_id or barrier_id in seen:
            continue
        seen.add(barrier_id)
        paths = candidate.get("source_paths") or []
        source_node = (
            candidate.get("source_visual_node_id")
            or candidate.get("source_visual_id")
            or next((path.get("source_visual_id") for path in paths if path.get("source_visual_id")), "")
        )
        path_nodes = candidate.get("branch_path_node_ids") or []
        if path_nodes and not source_node:
            source_node = path_nodes[0]
        selected.append(
            {
                "barrier_id": barrier_id,
                "source_node": str(source_node or "").strip(),
                "source_component": candidate.get("source_component_id") or candidate.get("source_component_tag"),
                "source_component_tag": candidate.get("source_component_tag"),
                "branch_id": candidate.get("branch_id") or (paths[0].get("branch_id") if paths else ""),
                "path_node_ids": [str(item) for item in path_nodes],
                "entity_class": (candidate.get("properties") or {}).get("entity_class") or candidate.get("candidate_label"),
                "bbox": candidate.get("bbox") or [],
            }
        )
    return selected


def _isolated_envelope(selected, adj, link_by_pair):
    barrier_ids = {item["barrier_id"] for item in selected}
    results = []
    for item in selected:
        start = item.get("source_node")
        if not start:
            continue
        node_ids = {start}
        line_ids = set()
        boundary_valves = set()
        unresolved = False
        queue = deque([(start, 0, [])])
        seen = {start}
        while queue:
            node, hops, path = queue.popleft()
            if hops >= MAX_ENVELOPE_HOPS:
                unresolved = True
                continue
            for nbr in sorted(adj.get(node, ())):
                line = link_by_pair.get((node, nbr)) or {}
                if line.get("id"):
                    line_ids.add(str(line["id"]))
                if nbr in barrier_ids:
                    boundary_valves.add(nbr)
                    continue
                if nbr in seen:
                    continue
                seen.add(nbr)
                node_ids.add(nbr)
                queue.append((nbr, hops + 1, path + [nbr]))
        results.append(
            {
                "status": "unresolved" if unresolved else "completed",
                "source_component": item.get("source_component"),
                "source_component_tag": item.get("source_component_tag"),
                "branch_id": item.get("branch_id"),
                "node_ids": sorted(node_ids),
                "line_ids": sorted(line_ids),
                "boundary_valve_ids": sorted(boundary_valves),
            }
        )
    return results


def _detect_schemes(selected, node_by_id, adj, policy, y_flip=None):
    schemes = []
    relief_ids = set()
    for item in selected:
        first = item["barrier_id"]
        if first not in node_by_id:
            continue
        previous = _previous_node(item.get("path_node_ids") or [], first)
        extension = _look_beyond_first_barrier(first, previous, node_by_id, adj, policy, y_flip=y_flip)
        first_summary = _node_summary(first, node_by_id, y_flip=y_flip)
        first_positive = _is_positive(first_summary.get("entity_class"))
        second = extension.get("second_block")
        series_proven = extension.get("series_proven", True)
        relief_between = extension.get("relief_between") or []
        context_devices = extension.get("context_devices") or []
        # Positive isolation (blind/spade/removed spool) is complete on its own; any
        # "second" the lookahead finds is the other half of the same break or an
        # unrelated downstream device, and must NOT become a separate lockable step.
        # Otherwise: only a second block proven to be in series on the same segment
        # counts as a double block and enters the mandatory barrier list. One reached
        # across a tee/junction or a directional check valve is downgraded to a
        # field-verify advisory so it is not asserted as certain isolation nor auto-
        # added to LOTO.
        proven_second = second if (second and series_proven and not first_positive) else ""
        unverified_second = second if (second and not series_proven and not first_positive) else ""
        if first_positive:
            scheme_type = "positive isolation"
        elif proven_second and relief_between:
            scheme_type = "double block with bleed"
        elif proven_second:
            scheme_type = "double block"
        else:
            scheme_type = "single block"
        relief_ids.update(relief_between)
        barrier_ids = [first] + ([proven_second] if proven_second else [])
        scheme = {
            "source_component": item.get("source_component"),
            "source_component_tag": item.get("source_component_tag"),
            "branch_id": item.get("branch_id"),
            "scheme_type": scheme_type,
            "barrier_ids": barrier_ids,
            "relief_candidate_ids": relief_between,
            "context_device_ids": context_devices,
            "basis": "detected from HILT process-line topology; no hazard-based scheme recommendation was made",
            "devices": [_node_summary(node_id, node_by_id, y_flip=y_flip) for node_id in barrier_ids if node_id],
        }
        if unverified_second:
            summary = _node_summary(unverified_second, node_by_id, y_flip=y_flip)
            scheme["unverified_additional_blocks"] = [
                {
                    **summary,
                    "note": "additional nearby block valve reached across a branch/junction or directional element; not a proven in-series double block — field-verify before relying on it as a second barrier.",
                }
            ]
        schemes.append(scheme)
    return schemes, relief_ids


def _previous_node(path, node_id):
    if node_id not in path:
        return ""
    index = path.index(node_id)
    if index <= 0:
        return ""
    return path[index - 1]


# Fittings/elements that break a *proven in-series* double block: a branch fitting
# (the second valve is on a parallel run, not the same trapped segment) or a
# directional element (a check/non-return valve is not a lockable manual block).
_SERIES_BREAKING_CLASSES = ("tee", "cross", "header", "manifold", "check_valve", "non_return")


def _breaks_series(node, process_degree):
    """True if routing *through* this node to reach a further block valve means the
    two blocks are not a proven in-series pair — a branch point (>=3 process-line
    connections or a branch fitting) or a directional check valve."""
    if process_degree >= 3:
        return True
    ec = str(node.get("entity_class") or "").lower()
    return any(token in ec for token in _SERIES_BREAKING_CLASSES)


def _look_beyond_first_barrier(first, previous, node_by_id, adj, policy, y_flip=None):
    queue = deque()
    seen = {first}
    for nbr in sorted(adj.get(first, ())):
        if previous and nbr == previous:
            continue
        queue.append((nbr, 1, [], False))
        seen.add(nbr)

    relief_between = []
    context_devices = []
    while queue:
        node_id, hops, reliefs, crossed = queue.popleft()
        if hops > MAX_SCHEME_LOOKAHEAD_HOPS:
            continue
        node = _node_summary(node_id, node_by_id, y_flip=y_flip)
        next_reliefs = list(reliefs)
        if _relief_type(node) != "not_relief":
            next_reliefs.append(node_id)
        role = _branch_device_role(node, policy)
        if role == "context":
            context_devices.append(node_id)
        elif role == "block":
            # `crossed` reflects only the intermediate nodes traversed to get here,
            # so a block sitting at a tee is still valid as the series endpoint.
            return {
                "second_block": node_id,
                "relief_between": next_reliefs,
                "context_devices": context_devices,
                "series_proven": not crossed,
            }
        node_crossed = crossed or _breaks_series(node, len(adj.get(node_id, ())))
        for nbr in sorted(adj.get(node_id, ())):
            if nbr in seen:
                continue
            seen.add(nbr)
            queue.append((nbr, hops + 1, next_reliefs, node_crossed))
    return {
        "second_block": "",
        "relief_between": relief_between,
        "context_devices": context_devices,
        "series_proven": True,
    }


def _discover_relief_candidates(envelope, schemes, node_by_id, selected_barrier_ids, scheme_relief_ids, policy, y_flip=None):
    envelope_nodes = {node_id for item in envelope for node_id in item.get("node_ids", [])}
    scheme_nodes = set(scheme_relief_ids)
    candidates = {}
    for node_id in sorted(envelope_nodes | scheme_nodes):
        if node_id in selected_barrier_ids:
            continue
        node = _node_summary(node_id, node_by_id, y_flip=y_flip)
        relief_type = _relief_type(node)
        if relief_type == "not_relief":
            if not _is_ambiguous_relief(node, policy):
                continue
            # Ambiguous valve-like component inside the isolated envelope: flag it
            # deterministically as 'uncertain' (manual review) rather than dropping it.
            # (Previously this fed an LLM pass that never actually ran, so these were
            # silently stored as 'not_relief' and hidden from the UI.)
            relief_type = "uncertain"
        classified_by = "deterministic"
        confidence = "high" if relief_type != "uncertain" else "low"
        candidates[node_id] = {
            "id": node_id,
            "tag": node.get("tag"),
            "entity_class": node.get("entity_class"),
            "entity_type": node.get("entity_type"),
            "bbox": node.get("bbox") or [],
            "inside_envelope": node_id in envelope_nodes,
            "relief_type": relief_type,
            "classification_confidence": confidence,
            "classified_by": classified_by,
            "basis": _relief_basis(node, relief_type),
            "source_branch_id": _source_branch_for_node(envelope, schemes, node_id),
        }
    return list(candidates.values())


def _scheme_summary(schemes):
    counts = {}
    for item in schemes:
        counts[item.get("scheme_type") or "unknown"] = counts.get(item.get("scheme_type") or "unknown", 0) + 1
    return {"scheme_count": len(schemes), "counts_by_type": counts}


def _relief_summary(candidates):
    counts = {}
    for item in candidates:
        counts[item.get("relief_type") or "unknown"] = counts.get(item.get("relief_type") or "unknown", 0) + 1
    return {"candidate_count": len(candidates), "counts_by_type": counts}


def _branch_device_role(node, policy):
    entity_class = node.get("entity_class")
    if any(class_matches(entity_class, value) for value in BACKPRESSURE_CONTEXT_CLASSES):
        return "context"
    classification = classify_candidate(
        {"entity_class": entity_class, "entity_type": node.get("entity_type")},
        entity_class,
        policy,
    )
    if classification.decision in {IsolationDecision.AUTOMATIC, IsolationDecision.CONDITIONAL_MANUAL_REVIEW}:
        return "block"
    return "traversable"


def _node_summary(node_id, node_by_id, y_flip=None):
    node = node_by_id.get(str(node_id)) or {}
    payload = node.get("payload") or {}
    return {
        "id": str(node_id),
        "tag": _attr(payload.get("attributes"), "tag") or _text(payload.get("text")),
        "entity_class": payload.get("entity_class"),
        "entity_type": payload.get("entity_type"),
        "bbox": _hilt_bbox(payload, y_flip=y_flip),
        "text": _text(payload.get("text")),
    }


def _relief_type(node):
    text = _node_text(node)
    if "bleed" in text:
        return "bleed"
    if "drain" in text:
        return "drain"
    if "open_vent" in text or " vent" in f" {text}" or text.startswith("vent"):
        return "vent"
    return "not_relief"


def _is_ambiguous_relief(node, policy):
    entity_class = normalize_class(node.get("entity_class"))
    if "instrument" in entity_class or entity_class in {"pressure_indicator", "level_indicator"}:
        return False
    if "valve" not in entity_class:
        return False
    classification = classify_candidate(
        {"entity_class": node.get("entity_class"), "entity_type": node.get("entity_type")},
        node.get("entity_class"),
        policy,
    )
    return classification.decision not in {IsolationDecision.AUTOMATIC}


def _relief_basis(node, relief_type):
    if relief_type == "uncertain":
        return "Ambiguous valve-like component inside the isolated envelope; LLM/manual classification may be needed."
    if relief_type == "not_relief":
        return "Classified as not a relief point."
    return f"Deterministic {relief_type} classification from HILT class/tag/text."


def _source_branch_for_node(envelope, schemes, node_id):
    for item in schemes:
        if node_id in set(item.get("relief_candidate_ids") or []):
            return item.get("branch_id") or item.get("source_component")
    for item in envelope:
        if node_id in set(item.get("node_ids") or []):
            return item.get("branch_id") or item.get("source_component")
    return ""


def _is_positive(entity_class):
    normalized = normalize_class(entity_class)
    return any(value in normalized for value in POSITIVE_TERMS)


def _node_text(node):
    return " ".join(
        normalize_class(value)
        for value in (node.get("entity_class"), node.get("entity_type"), node.get("tag"), node.get("text"))
        if value
    )


def _node_id(node):
    payload = node.get("payload") or {}
    value = node.get("id") or payload.get("id") or payload.get("source_id")
    return str(value).strip() if value else ""


def _attr(attributes, name):
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if isinstance(attr, dict) and str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return ""


def _text(items):
    values = []
    for item in items or []:
        if isinstance(item, dict) and item.get("value") not in (None, "", []):
            values.append(str(item.get("value")))
    return ", ".join(values)


def _hilt_bbox(payload, y_flip=None):
    location = payload.get("bounding_box_location") or {}
    width = payload.get("bounding_box_width")
    height = payload.get("bounding_box_height")
    if location.get("x") is None or location.get("y") is None or width is None or height is None:
        return []
    x = float(location.get("x")) - float(width) / 2.0
    cy = float(location.get("y"))
    h = float(height)
    y = (float(y_flip) - cy - h / 2.0) if y_flip is not None else (cy - h / 2.0)
    return [int(round(x)), int(round(y)), int(round(float(width))), int(round(float(height)))]
