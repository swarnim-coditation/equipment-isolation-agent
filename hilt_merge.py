"""HILT-authoritative candidate merges.

The parsed P&ID piping graph (HILT) beats JanusGraph depth + bbox distance for
nozzle<->valve connectivity, so after visual selection bbox.py overlays the HILT
result. There are currently TWO HILT resolution strategies applied in sequence:

  - _merge_hilt_source_branches (B): keyed on (equipment_tag, source_component),
    driven by resolve_source_branch_isolation (per-branch isolating valve). This
    is the richer, coverage-aware view that also feeds obligations.py.
  - _merge_hilt_topology (C): keyed on source_component_tag (nozzle), driven by
    resolve_nozzle_isolation (nozzle -> connected valve).

B runs first, then C, so C can re-drop candidates B just added. They are isolated
here (rather than interleaved in bbox.py) so the overlap can be reviewed and,
eventually, collapsed to a single canonical strategy.
"""

from bbox_util import _dedupe_candidates
from domain.classification import classify_candidate
from domain.enums import FlowRole
from flow import role_for_source


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
