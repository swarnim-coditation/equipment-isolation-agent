"""STLM/P&ID raster payload parsing: text items, image dimensions, y-flip fallback.

Self-contained byte/JSON parsing extracted from bbox.py. Reads PNG and JPEG headers
directly rather than depending on an image library. Pure apart from the optional
Plant360 job-metadata lookup, which is passed in as a client.
"""
from __future__ import annotations



def _extract_text_items(payload):
    if not isinstance(payload, dict):
        return []
    text_json = payload.get("text_json")
    if not isinstance(text_json, dict):
        return []
    items = []
    for key, value in text_json.items():
        if not isinstance(value, dict):
            continue
        bbox = value.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        items.append(
            {
                "id": key,
                "bbox": [int(round(float(part))) for part in bbox],
                "text": value.get("text") or value.get("value") or value.get("ocr_text") or "",
            }
        )
    return items


def _fallback_hilt_yflip(config, client, hilt_payload, debug):
    height = _hilt_image_height(hilt_payload)
    if height:
        debug["hilt_y_flip_source"] = "hilt_payload_image_height"
        return float(height)
    height = _job_image_height(config, client, debug)
    if height:
        debug["hilt_y_flip_source"] = "job_image_height"
        return float(height)
    debug["hilt_y_flip_source"] = "unavailable"
    return None


def _hilt_image_height(hilt_payload):
    if not isinstance(hilt_payload, dict):
        return None
    candidates = []
    for container in (hilt_payload, hilt_payload.get("hilt_graph") or {}, hilt_payload.get("metadata") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("image_height", "original_image_height", "page_height", "height"):
            value = container.get(key)
            number = _positive_number(value)
            if number and number > 500:
                candidates.append(number)
    return candidates[0] if candidates else None


def _job_image_height(config, client, debug):
    job_id = config.resolved_job_id
    if not job_id:
        return None
    try:
        job = client.get_json(f"/jobs/{job_id}")
    except Exception as exc:
        debug["hilt_y_flip_job_lookup_error"] = str(exc)
        return None
    file_id = job.get("input_file_image")
    if not file_id:
        debug["hilt_y_flip_image_error"] = "missing_input_file_image"
        return None
    try:
        content, _content_type = client.get_bytes(f"/uploads/{file_id}")
    except Exception as exc:
        debug["hilt_y_flip_image_error"] = str(exc)
        return None
    dimensions = _image_dimensions(content)
    if not dimensions:
        debug["hilt_y_flip_image_error"] = "unsupported_or_invalid_image"
        return None
    debug["hilt_y_flip_image_width"] = dimensions[0]
    debug["hilt_y_flip_image_height"] = dimensions[1]
    return dimensions[1]


def _image_dimensions(content):
    if not isinstance(content, (bytes, bytearray)):
        return None
    data = bytes(content)
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return (width, height) if width > 0 and height > 0 else None
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    return None


def _jpeg_dimensions(data):
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        while marker == 0xFF and index < len(data):
            marker = data[index]
            index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if length < 7:
                return None
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return (width, height) if width > 0 and height > 0 else None
        index += length
    return None


def _positive_number(value):
    try:
        number = float(value)
    except Exception:
        return None
    return number if number > 0 else None


# Alias, not a wrapper: normalize_tag is the single implementation.
