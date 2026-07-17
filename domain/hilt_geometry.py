"""Shared HILT/STLM symbol geometry helpers.

These were previously copy-pasted verbatim in bbox.py and impact.py (and imported
from bbox by instrument_context.py). Symbol extraction, attribute lookup, bbox
normalization, and y-flip calibration are pure geometry with no module-specific
behavior, so they live here as the single source of truth.

Calibration note: STLM symbol coordinates are image-space; HILT node coordinates
are y-flipped. `calibrate_yflip` derives the image height H such that
`image_y = H - hilt_y` by pairing HILT nodes with STLM symbols (by id, then tag).
It returns None when no pairs are found -- callers treat that as "calibration
failed" and skip the HILT-authoritative merge.
"""

from __future__ import annotations

from domain.topology import normalize_tag


def symbol_attr(symbol, name):
    target = str(name or "").strip().lower().replace("_", " ")
    for attr in symbol.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        attr_name = str(attr.get("name") or "").strip().lower().replace("_", " ")
        if attr_name == target:
            return attr.get("value")
    return None


def extract_symbols(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "symbols", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_symbols(value)
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


def symbol_bbox(symbol):
    if isinstance(symbol.get("bbox"), list) and len(symbol["bbox"]) == 4:
        return [int(round(float(value))) for value in symbol["bbox"]]
    keys = ("orig_x", "orig_y", "orig_bbox_width", "orig_bbox_height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    keys = ("x", "y", "width", "height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    return []


def calibrate_yflip(hilt_nodes, symbols):
    """Derive the image height H such that image_y = H - hilt_y. Pairs HILT nodes
    with STLM symbols (STLM coords are image-space) by node id first, then by tag.
    Returns None if no pairs (calibration failed -> HILT merge is skipped)."""
    if not symbols:
        return None
    stlm_by_id = {}
    for sym in symbols:
        for key in ("source_id", "uuid", "id"):
            value = sym.get(key)
            if value:
                stlm_by_id[str(value).lower()] = sym
    stlm_by_tag = {}
    for sym in symbols:
        tag = sym.get("tag") or symbol_attr(sym, "tag")
        if tag:
            stlm_by_tag[normalize_tag(tag)] = sym

    heights = []
    for node in hilt_nodes:
        payload = node.get("payload") or {}
        loc = payload.get("bounding_box_location") or {}
        if loc.get("y") is None:
            continue
        sym = stlm_by_id.get(str(node.get("id") or "").lower())
        if sym is None:
            tag = symbol_attr(payload, "tag")
            sym = stlm_by_tag.get(normalize_tag(tag)) if tag else None
        if sym is None:
            continue
        sb = symbol_bbox(sym)
        if not sb:
            continue
        stlm_center_y = sb[1] + sb[3] / 2.0
        heights.append(stlm_center_y + float(loc.get("y")))
    if not heights:
        return None
    return sum(heights) / len(heights)
