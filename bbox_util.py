"""Leaf helpers for candidate de-duplication and sort ordering.

Extracted from bbox.py so that both bbox.py (candidate selection) and hilt_merge.py
(HILT-authoritative merges) can share them without an import cycle. Pure functions,
no I/O.
"""

from domain.topology import FAR_DISTANCE, normalize_tag


def _distance_sort_value(value):
    return float(value) if value is not None else FAR_DISTANCE


def _visual_sort_key(candidate):
    return (
        0 if candidate.get("bbox") and candidate.get("source_bbox") else 1,
        _distance_sort_value(candidate.get("source_visual_distance")),
        int(candidate.get("traversal_depth") or 99),
        -int(candidate.get("confidence") or 0),
        str(candidate.get("tag_number") or ""),
        str(candidate.get("candidate_id") or ""),
    )


def _dedupe_candidates(candidates):
    """Collapse candidates that resolve to the same physical device (same
    equipment_tag + visual/candidate id), accumulating each occurrence as a
    source_path. The surviving record keeps the source fields of the
    best-ranked (_visual_sort_key) duplicate."""
    merged = {}
    for candidate in candidates:
        key = (candidate.get("equipment_tag"), normalize_tag(candidate.get("visual_id") or candidate.get("candidate_id")))
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
