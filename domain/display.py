from __future__ import annotations

from typing import Any


_DEVICE_DISPLAY_NAMES = {
    "undefined_valve": "manual valve",
    "generic_inline_valve": "manual valve",
    "valve": "manual valve",
    "gate_valve": "gate valve",
    "ball_valve": "ball valve",
    "globe_valve": "globe valve",
    "check_valve": "check valve",
    "control_valve": "control valve",
    "blind": "blind",
    "spade": "spade",
    "flange": "flange",
    "blank_flange": "blank flange",
    "breaker": "breaker",
    "disconnect": "disconnect",
}


def device_display_name(entity_class: Any, fallback: str = "isolation device") -> str:
    raw = str(entity_class or "").strip()
    if not raw:
        return fallback
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in _DEVICE_DISPLAY_NAMES:
        return _DEVICE_DISPLAY_NAMES[normalized]
    return raw.replace("_", " ")


def device_display_label(device: dict[str, Any], fallback: str = "isolation device") -> str:
    tag = str(device.get("tag") or device.get("tag_number") or "").strip()
    if tag:
        return tag
    return device_display_name(device.get("entity_class"), fallback=fallback)
