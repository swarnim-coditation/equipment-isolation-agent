"""Visual candidate selection: nearest-per-source picking and companion detection.

Given the resolved candidate pool, chooses which candidates become isolation
points -- nearest valve per boundary source, plus parallel/branch companions that
form double-block arrangements. Returns a debug dict whose keys reach the output
payload, so changes here are visible to the golden harness.
"""
from __future__ import annotations

from bbox_geometry import (
    _bbox_center,
    _direction_sector,
    _point_distance,
)
from bbox_util import _dedupe_candidates, _dedupe_source_candidates, _selectable_candidate_pool, _source_key, _visual_sort_key
from domain.topology import normalize_tag

# Alias, not a wrapper: normalize_tag is the single implementation.
_norm = normalize_tag

# Companion-detection tuning. Moved with the functions that use them.
MAX_PARALLEL_COMPANIONS_PER_SOURCE = 2
PARALLEL_COMPANION_DISTANCE_DELTA = 55.0
PARALLEL_COMPANION_MAX_DISTANCE = 170.0
MAX_BRANCH_COMPANIONS_PER_SOURCE = 2
BRANCH_COMPANION_MAX_DISTANCE = 800.0
BRANCH_COMPANION_MIN_SEPARATION = 140.0


def _sources_owning_isolation_valve(candidate_pool, policy):
    """Source keys that own at least one policy-selectable isolation valve.

    "Selectable" means exactly what selection means elsewhere, so this reuses
    `_selectable_candidate_pool`."""
    return {_source_key(candidate) for candidate in _selectable_candidate_pool(candidate_pool, policy)}


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
