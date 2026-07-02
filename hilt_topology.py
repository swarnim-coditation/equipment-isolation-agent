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

PROCESS_LINE_CLASSES = {
    "secondary_process_line",
    "main_process_line",
    "primary_process_line",
    "process_line",
}
VALVE_KEYWORD = "valve"


def resolve_nozzle_isolation(hilt_payload: dict, equipment_tag: str, y_flip: float | None = None) -> dict:
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
    links = graph.get("links") or []

    node_by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id") or (node.get("payload") or {}).get("id")
        if nid:
            node_by_id[str(nid)] = node

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

    valve_ids = {
        str(node.get("id"))
        for node in nodes
        if VALVE_KEYWORD in str((node.get("payload") or {}).get("entity_class") or "").lower()
        and (node.get("id") or (node.get("payload") or {}).get("id"))
    }

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

    result: dict[str, list] = {}
    for tag, nozzle_id in nozzles.items():
        result[tag] = _nearest_valves(nozzle_id, adj, valve_ids, node_by_id, max_hops=10, y_flip=y_flip)
    return result


def _nearest_valves(start, adj, valve_ids, node_by_id, max_hops, y_flip=None):
    """BFS from a nozzle over process lines; record the first valve on each branch.
    Valves are leaves (we do not traverse through them) -- the nearest valve on a
    branch is that branch's isolation point."""
    seen = {start}
    queue = deque([(start, 0, [start])])
    found: list[dict] = []
    found_ids: set = set()
    while queue:
        node, hops, path = queue.popleft()
        if hops >= max_hops:
            continue
        for nbr in adj.get(node, ()):
            if nbr in seen:
                continue
            seen.add(nbr)
            new_path = path + [nbr]
            if nbr in valve_ids:
                if nbr not in found_ids:
                    found_ids.add(nbr)
                    found.append(_valve_summary(nbr, node_by_id, hops + 1, new_path, y_flip))
                # valves are leaves -- do not traverse past the isolation point
                continue
            queue.append((nbr, hops + 1, new_path))
    found.sort(key=lambda item: (item["hop_distance"], str(item["valve_id"])))
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
        "connectivity_source": "hilt_topology",
    }


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
