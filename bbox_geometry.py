"""Planar geometry helpers for bbox selection. Pure, stdlib only.

NOTE: ``_bbox_near`` here is NOT interchangeable with instrument_context's
same-named helper -- this one does float math and accepts lists only, that one
int-truncates and accepts tuples. tests/test_geometry_helpers.py pins the
difference. Do not merge them without deciding which semantics win.
"""
from __future__ import annotations


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
