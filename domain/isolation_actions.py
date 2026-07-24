from __future__ import annotations

from typing import Any

OPERABLE_VALVE_CLASSES = (
    "valve",
    "generic_inline_valve",
    "gate_valve",
    "ball_valve",
    "globe_valve",
    "undefined_valve",
)
BACKFLOW_CONTEXT_CLASSES = ("check_valve", "non_return_valve")
CONTROL_CONTEXT_CLASSES = ("control_valve",)
ELECTRICAL_ACTION_CLASSES = ("breaker", "disconnect")
INSTALLED_POSITIVE_CLASSES = ("blind", "spade", "spectacle", "blank_flange", "blind_flange")
FIELD_CONFIRMED_POSITIVE_CLASSES = ("flange", "line_break_point")


def normalize_class(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _matches_generic_valve(value: str) -> bool:
    return value in {"valve", "generic_valve", "generic_inline_valve"} or value.endswith("_valve")


def _matches_exact_or_suffix(value: str, classes: tuple[str, ...]) -> bool:
    if not value:
        return False
    return any(value == item or value.endswith(f"_{item}") for item in classes)


def _matches_installed_positive(value: str) -> bool:
    return _matches_exact_or_suffix(value, INSTALLED_POSITIVE_CLASSES)


def _matches_field_confirmed_positive(value: str) -> bool:
    if _matches_installed_positive(value):
        return False
    return value in FIELD_CONFIRMED_POSITIVE_CLASSES or value.endswith("_flange")


def operation_kind(entity_class: Any) -> str:
    value = normalize_class(entity_class)
    if _matches_exact_or_suffix(value, ELECTRICAL_ACTION_CLASSES):
        return "electrical_isolation"
    if _matches_installed_positive(value):
        return "installed_positive_isolation"
    if _matches_field_confirmed_positive(value):
        return "field_confirmed_positive_isolation"
    if _matches_exact_or_suffix(value, BACKFLOW_CONTEXT_CLASSES):
        return "directional_context"
    if _matches_exact_or_suffix(value, CONTROL_CONTEXT_CLASSES):
        return "control_context"
    if value in OPERABLE_VALVE_CLASSES or (value.endswith("_valve") and value not in set(BACKFLOW_CONTEXT_CLASSES + CONTROL_CONTEXT_CLASSES)):
        return "valve_isolation"
    return "isolation_device"


def requires_positive_field_confirmation(entity_class: Any) -> bool:
    return operation_kind(entity_class) == "field_confirmed_positive_isolation"


def is_operable_barrier(entity_class: Any) -> bool:
    return operation_kind(entity_class) in {
        "valve_isolation",
        "electrical_isolation",
        "installed_positive_isolation",
    }


def is_installed_positive_isolation(entity_class: Any) -> bool:
    return operation_kind(entity_class) == "installed_positive_isolation"


def is_field_confirmed_positive_candidate(entity_class: Any) -> bool:
    return operation_kind(entity_class) == "field_confirmed_positive_isolation"


def manual_candidate_label(entity_class: Any, source_type: Any = "") -> str:
    source = normalize_class(source_type)
    kind = operation_kind(entity_class)
    if source == "instrument_context":
        return "verify secondary line"
    if kind == "field_confirmed_positive_isolation":
        return "verify positive-isolation point"
    if kind == "installed_positive_isolation":
        return "verify blind/spade point"
    if kind == "directional_context":
        return "verify check valve/backflow context"
    if kind == "control_context":
        return "verify control valve is not used as isolation"
    if kind == "valve_isolation":
        return "verify bypass/parallel valve"
    return "manual field check"
