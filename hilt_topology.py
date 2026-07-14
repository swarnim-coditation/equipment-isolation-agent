"""HILT-topology isolation resolver.

The HILT graph (cnvrt-backend-api) is the parsed P&ID piping network: nodes
(nozzles, valves, junctions, equipment) connected by process-line links. This is
the AUTHORITATIVE source for "which valve is physically piped to which nozzle" --
far more trustworthy than JanusGraph traversal depth + bbox distance, which can
pick a geographically-near but topologically-wrong valve.

For each equipment nozzle we walk the process-line graph and find the FIRST valve
on each branch (valves are treated as leaves -- the nearest valve on a branch is
the isolation point for that branch). Returns nozzle -> [valves] with HILT bboxes.
"""
from __future__ import annotations

from collections import deque

from domain.classification import class_matches, classify_candidate, is_policy_isolation_device, normalize_class
from domain.enums import IsolationDecision

PROCESS_LINE_CLASSES = {
    "secondary_process_line",
    "main_process_line",
    "primary_process_line",
    "process_line",
}

BRANCH_CONTEXT_VALVE_CLASSES = {"check_valve", "control_valve"}


def resolve_nozzle_isolation(hilt_payload: dict, equipment_tag: str, y_flip: float | None = None, policy=None) -> dict:
    """Walk the HILT piping graph to find the valve isolating each equipment nozzle.

    ``y_flip`` is the image-height constant H such that image_y = H - hilt_y (HILT
    uses a CAD bottom-left origin; the P&ID image uses a top-left origin). When
    provided, valve bboxes are flipped into image coordinates. Calibrate H per job
    by matching HILT nozzles to STLM nozzles (STLM coords are already image-space).
    """
    graph = hilt_payload.get("hilt_graph") if isinstance(hilt_payload, dict) else None
    if not isinstance(graph, dict):
        return {}
    nodes = graph.get("nodes") or []
    node_by_id, adj = _hilt_index(graph)

    eq_norm = _norm(equipment_tag)
    nozzles: dict[str, str] = {}  # tag -> node_id
    for node in nodes:
        payload = node.get("payload") or {}
        if payload.get("entity_class") != "equipment_nozzle":
            continue
        tag = _attr(payload.get("attributes"), "tag")
        if tag and eq_norm and _norm(tag).endswith("_" + eq_norm):
            nid = node.get("id") or payload.get("id")
            if nid:
                nozzles[str(tag)] = str(nid)

    result: dict[str, list] = {}
    for tag, nozzle_id in nozzles.items():
        result[tag] = _nearest_valves(nozzle_id, adj, node_by_id, max_hops=10, y_flip=y_flip, policy=policy)
    return result


def resolve_source_branch_isolation(
    hilt_payload: dict,
    source_entries: list[dict],
    y_flip: float | None = None,
    policy=None,
    max_hops: int = 10,
) -> list[dict]:
    """Resolve required isolation per process branch from concrete HILT source UUIDs.

    A source is an equipment connection/nozzle already matched between UniGraph/STLM
    and HILT. This path is more reliable for Aker-style drawings where equipment
    nozzles are present but not tagged with the equipment name.
    """
    graph = hilt_payload.get("hilt_graph") if isinstance(hilt_payload, dict) else None
    if not isinstance(graph, dict):
        return []
    node_by_id, adj = _hilt_index(graph)
    results = []
    seen_sources = set()
    for source in source_entries or []:
        if source.get("source_context_type") or source.get("source_type") == "instrument_context":
            continue
        source_visual_id = str(source.get("source_visual_id") or source.get("source_visual_node_id") or "").strip()
        if not source_visual_id or source_visual_id not in node_by_id or source_visual_id not in adj:
            continue
        source_component = str(source.get("source_component_id") or source.get("source_component_tag") or "").strip()
        equipment_tag = str(source.get("equipment_tag") or "").strip()
        source_key = (equipment_tag, source_component, source_visual_id)
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        branches = _nearest_branch_devices(
            source_visual_id,
            adj,
            node_by_id,
            max_hops=max_hops,
            y_flip=y_flip,
            policy=policy,
        )
        if not branches:
            continue
        for index, branch in enumerate(branches, start=1):
            branch["branch_index"] = index
            branch["branch_id"] = f"{source_component or source_visual_id}:branch:{index}"
        results.append(
            {
                "equipment_tag": equipment_tag,
                "source_component": source_component,
                "source_component_tag": str(source.get("source_component_tag") or source_component),
                "source_visual_id": source_visual_id,
                "source_bbox": source.get("source_bbox") or [],
                "branches": branches,
            }
        )
    return results


def _hilt_index(graph):
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    node_by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id") or (node.get("payload") or {}).get("id")
        if nid:
            node_by_id[str(nid)] = node

    # Adjacency over PROCESS lines only (instrument/electrical/companion lines excluded).
    adj: dict[str, set] = {}
    for link in links:
        payload = link.get("payload") or {}
        if payload.get("entity_class") not in PROCESS_LINE_CLASSES:
            continue
        source = str(link.get("source") or payload.get("from") or "")
        target = str(link.get("target") or payload.get("to") or "")
        if source and target:
            adj.setdefault(source, set()).add(target)
            adj.setdefault(target, set()).add(source)
    return node_by_id, adj


def _nearest_valves(start, adj, node_by_id, max_hops, y_flip=None, policy=None):
    return [
        branch["valve"]
        for branch in _nearest_branch_devices(start, adj, node_by_id, max_hops=max_hops, y_flip=y_flip, policy=policy)
        if branch.get("status") == "isolated" and branch.get("valve")
    ]


def _nearest_branch_devices(start, adj, node_by_id, max_hops, y_flip=None, policy=None):
    """BFS from a nozzle over process lines; record the first valve on each branch.
    Valves are leaves (we do not traverse through them) -- the nearest valve on a
    branch is that branch's isolation point."""
    seen = {start}
    queue = deque([(start, 0, [start], [])])
    found: list[dict] = []
    found_ids: set = set()
    while queue:
        node, hops, path, context_devices = queue.popleft()
        if hops >= max_hops:
            found.append(_unresolved_branch(path, context_devices, "max_hops_reached", node_by_id))
            continue
        expanded = False
        for nbr in adj.get(node, ()):
            if nbr in seen:
                continue
            seen.add(nbr)
            new_path = path + [nbr]
            expanded = True
            branch_role = _branch_device_role(nbr, node_by_id, policy)
            if branch_role == "required_isolation":
                if nbr not in found_ids:
                    found_ids.add(nbr)
                    found.append(
                        {
                            "status": "isolated",
                            "valve": _valve_summary(nbr, node_by_id, hops + 1, new_path, y_flip),
                            "path_node_ids": new_path,
                            "path_node_classes": [_node_class(node_id, node_by_id) for node_id in new_path],
                            "context_devices": context_devices,
                            "basis": "first required isolation device on HILT process branch",
                        }
                    )
                # valves are leaves -- do not traverse past the isolation point
                continue
            next_context = context_devices
            if branch_role == "backflow_or_control_context":
                next_context = context_devices + [_valve_summary(nbr, node_by_id, hops + 1, new_path, y_flip)]
            queue.append((nbr, hops + 1, new_path, next_context))
        if not expanded and node != start:
            found.append(_unresolved_branch(path, context_devices, "no_required_isolation_device_found", node_by_id))
    found.sort(
        key=lambda item: (
            0 if item.get("status") == "isolated" else 1,
            int(((item.get("valve") or {}).get("hop_distance") or len(item.get("path_node_ids") or []))),
            str((item.get("valve") or {}).get("valve_id") or item.get("branch_id") or ""),
        )
    )
    return found


def _valve_summary(valve_id: str, node_by_id: dict, hop_distance: int, path, y_flip: float | None = None) -> dict:
    node = node_by_id.get(valve_id) or {}
    payload = node.get("payload") or {}
    return {
        "valve_id": valve_id,
        "entity_class": payload.get("entity_class"),
        "entity_type": payload.get("entity_type"),
        "tag": _attr(payload.get("attributes"), "tag"),
        "bbox": _hilt_bbox(payload, y_flip),
        "hop_distance": hop_distance,
        "path_node_count": len(path),
        "path_node_ids": list(path),
        "connectivity_source": "hilt_topology",
    }


def _unresolved_branch(path, context_devices, reason, node_by_id):
    return {
        "status": "unresolved",
        "valve": None,
        "path_node_ids": list(path),
        "path_node_classes": [_node_class(node_id, node_by_id) for node_id in path],
        "context_devices": context_devices,
        "basis": reason,
    }


def _branch_device_role(node_id, node_by_id, policy):
    node = node_by_id.get(str(node_id)) or {}
    payload = node.get("payload") or {}
    entity_class = payload.get("entity_class")
    if _is_branch_context_device(entity_class):
        return "backflow_or_control_context"
    if policy is None:
        return "required_isolation" if "valve" in str(entity_class or "").lower() else "traversable"
    classification = classify_candidate({"entity_class": entity_class, "entity_type": payload.get("entity_type")}, entity_class, policy)
    if classification.decision in {IsolationDecision.AUTOMATIC, IsolationDecision.CONDITIONAL_MANUAL_REVIEW}:
        return "required_isolation"
    return "traversable"


def _is_branch_context_device(entity_class):
    normalized = normalize_class(entity_class)
    return any(class_matches(normalized, value) for value in BRANCH_CONTEXT_VALVE_CLASSES)


def _node_class(node_id, node_by_id):
    return (node_by_id.get(str(node_id)) or {}).get("payload", {}).get("entity_class")


def _hilt_bbox(payload: dict, y_flip: float | None = None) -> list:
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
    y = (y_flip - cy - h / 2.0) if y_flip is not None else (cy - h / 2.0)
    return [int(round(x)), int(round(y)), int(round(w)), int(round(h))]


def _is_isolation_device_class(entity_class, policy):
    if policy is None:
        return "valve" in str(entity_class or "").lower()
    return is_policy_isolation_device(entity_class, policy)


def _attr(attributes, name):
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if isinstance(attr, dict) and str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return None


def _norm(value):
    return str(value or "").strip().upper().replace("-", "")
