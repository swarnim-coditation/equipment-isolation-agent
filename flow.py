"""Deterministic flow-direction classifier over the Plant360 HILT graph.

The HILT graph (from cnvrt-backend-api ``get_job_hilt_graph``) already encodes the
P&ID's flow direction that was parsed at conversion time:
  - ``payload.flow``: "ONE_WAY" (directional) vs "UNKNOWN_FLOW"
  - ``payload.arrow``: list of {from_id, to_id, ...} -- the actual flow arrows
  - link ``source``/``target`` (== payload ``from``/``to``): topological endpoints

This classifies each equipment nozzle as INLET (flow INTO the equipment),
OUTLET (flow OUT of the equipment), or UNKNOWN, so the LOTO isolation order can
be grounded (isolate inlets/upstream first) instead of guessed by the LLM.
"""
from __future__ import annotations


def classify_nozzle_flow(hilt_payload: dict) -> dict:
    """Return {nozzle_tag_normalized: {"role", "inlet_score", "outlet_score", "node_id"}}
    for every equipment_nozzle in the HILT graph.

    role is one of: inlet, outlet, bidirectional, unknown.
    """
    graph = hilt_payload.get("hilt_graph") if isinstance(hilt_payload, dict) else None
    if not isinstance(graph, dict):
        return {}
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []

    nozzle_tag_by_id: dict[str, str] = {}
    for node in nodes:
        payload = node.get("payload") or {}
        if payload.get("entity_class") != "equipment_nozzle":
            continue
        node_id = node.get("id") or payload.get("id") or payload.get("source_id")
        tag = _attr(payload.get("attributes"), "tag")
        if node_id and tag:
            nozzle_tag_by_id[str(node_id)] = str(tag)

    roles: dict[str, dict] = {}
    for node_id, tag in nozzle_tag_by_id.items():
        inward = 0.0  # flow arriving at the nozzle -> INLET
        outward = 0.0  # flow leaving the nozzle -> OUTLET
        for link in links:
            source = str(link.get("source") or "")
            target = str(link.get("target") or "")
            payload = link.get("payload") or {}
            flow = str(payload.get("flow") or "").upper()
            # ONE_WAY topology is reliable; UNKNOWN_FLOW is weak evidence.
            topo_weight = 2.0 if flow == "ONE_WAY" else (0.5 if flow == "UNKNOWN_FLOW" else 1.0)
            if target == node_id:
                inward += topo_weight
            elif source == node_id:
                outward += topo_weight
            # Parsed flow arrows are the strongest directional evidence.
            for arrow in payload.get("arrow") or []:
                if not isinstance(arrow, dict):
                    continue
                if str(arrow.get("to_id")) == node_id:
                    inward += 3.0
                if str(arrow.get("from_id")) == node_id:
                    outward += 3.0
        role = _role_from_scores(inward, outward)
        roles[_norm(tag)] = {
            "role": role,
            "tag": tag,
            "node_id": node_id,
            "inlet_score": inward,
            "outlet_score": outward,
        }
    return roles


def _role_from_scores(inward: float, outward: float) -> str:
    if inward == 0 and outward == 0:
        return "unknown"
    if inward > 0 and outward > 0:
        return "bidirectional" if abs(inward - outward) < 0.5 else ("inlet" if inward > outward else "outlet")
    if inward > 0:
        return "inlet"
    if outward > 0:
        return "outlet"
    return "unknown"


def role_for_source(flow_roles: dict, source_component_tag: str | None, source_visual_id: str | None = None) -> str:
    """Look up the flow role for an isolation candidate's source nozzle.
    Matches on the nozzle tag (e.g. 'N1_CT05') first, then falls back to node id.
    Returns 'inlet', 'outlet', 'bidirectional', or 'unknown'."""
    if not flow_roles:
        return "unknown"
    for key in (source_component_tag, source_component_tag.replace("_", "-") if source_component_tag else None):
        if key and _norm(key) in flow_roles:
            return flow_roles[_norm(key)]["role"]
    if source_visual_id:
        for info in flow_roles.values():
            if str(info.get("node_id")) == str(source_visual_id):
                return info["role"]
    return "unknown"


def _attr(attributes, name):
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if isinstance(attr, dict) and str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return None


def _norm(value):
    return str(value or "").strip().upper().replace("-", "_")
