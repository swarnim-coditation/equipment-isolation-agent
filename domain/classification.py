from __future__ import annotations

from typing import Any

from domain.enums import IsolationDecision
from domain.models import CandidateClassification


GENERIC_VALVE_CLASSES = {"valve", "generic_valve", "generic_inline_valve"}


def normalize_class(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def class_matches(value: Any, class_name: Any) -> bool:
    value_norm = normalize_class(value)
    class_norm = normalize_class(class_name)
    if not value_norm or not class_norm:
        return False
    if class_norm == "valve":
        return value_norm in GENERIC_VALVE_CLASSES
    return value_norm == class_norm or value_norm.endswith(f"_{class_norm}")


def matches_any_class(value: Any, classes: tuple[str, ...] | list[str] | set[str]) -> bool:
    return any(class_matches(value, class_name) for class_name in classes)


def class_values_from_properties(properties: dict[str, Any], label: Any = "") -> tuple[str, ...]:
    values = []
    for value in (
        properties.get("entity_class"),
        properties.get("class"),
        label,
        properties.get("type"),
        properties.get("entity_type"),
        properties.get("category"),
        properties.get("valve_type"),
    ):
        normalized = normalize_class(value)
        if normalized:
            values.append(normalized)
    return tuple(dict.fromkeys(values))


def classify_candidate(properties: dict[str, Any], label: Any, policy, method_text: str = "", tag_prefix: str = "") -> CandidateClassification:
    class_values = class_values_from_properties(properties, label)
    raw_entity_class = normalize_class(properties.get("entity_class") or properties.get("class") or label)
    raw_entity_type = normalize_class(properties.get("entity_type") or properties.get("type"))
    if any(matches_any_class(value, policy.excluded_classes) for value in class_values):
        return CandidateClassification(
            raw_entity_class=raw_entity_class,
            raw_entity_type=raw_entity_type,
            class_values=class_values,
            decision=IsolationDecision.EXCLUDED,
        )

    eligible_matches = tuple(
        sorted({keyword for keyword in policy.eligible_classes if any(class_matches(value, keyword) for value in class_values)})
    )
    conditional_matches = tuple(
        sorted({keyword for keyword in policy.conditional_classes if any(class_matches(value, keyword) for value in class_values)})
    )
    matched = eligible_matches + tuple(item for item in conditional_matches if item not in eligible_matches)
    if eligible_matches:
        decision = IsolationDecision.AUTOMATIC
    elif conditional_matches:
        decision = IsolationDecision.CONDITIONAL_MANUAL_REVIEW
    else:
        decision = IsolationDecision.NOT_ISOLATION

    barrier_classes = tuple(policy.eligible_classes)
    if getattr(policy, "include_conditional_candidates", False):
        barrier_classes = barrier_classes + tuple(policy.conditional_classes)
    is_barrier = any(matches_any_class(value, barrier_classes) for value in class_values) or "close and lock" in str(method_text).lower()
    is_positive = any(matches_any_class(value, policy.positive_isolation_classes) for value in class_values)
    verification_prefixes = {str(value).lower() for value in policy.verification_tag_prefixes}
    is_verification = (
        any(matches_any_class(value, policy.verification_classes) for value in class_values)
        or str(tag_prefix or "").lower() in verification_prefixes
    )
    if any(class_matches(value, "valve") or normalize_class(value).endswith("_valve") for value in class_values):
        is_positive = any(matches_any_class(value, policy.positive_isolation_classes) for value in class_values)
        is_verification = (
            str(tag_prefix or "").lower() in verification_prefixes
            or any(matches_any_class(value, policy.verification_classes) for value in class_values)
        )

    return CandidateClassification(
        raw_entity_class=raw_entity_class,
        raw_entity_type=raw_entity_type,
        class_values=class_values,
        matched_policy_classes=matched,
        decision=decision,
        is_barrier=is_barrier,
        is_positive_isolation=is_positive,
        is_verification=is_verification,
    )


def is_policy_isolation_device(entity_class: Any, policy) -> bool:
    classification = classify_candidate({"entity_class": entity_class}, entity_class, policy)
    return classification.decision in {
        IsolationDecision.AUTOMATIC,
        IsolationDecision.CONDITIONAL_MANUAL_REVIEW,
    }
