from domain.classification import classify_candidate
from domain.enums import IsolationDecision, ObligationStatus, SourceType
from domain.isolation_actions import is_installed_positive_isolation, is_operable_barrier
from domain.keywords import VERIFICATION_ENTITY_KEYWORDS, VERIFY_TAG_PREFIXES
from domain.topology import tag_prefix as _tag_prefix


BARRIER_KEYWORDS = {"valve", "generic_inline_valve", "gate_valve", "ball_valve", "globe_valve", "undefined_valve", "blind", "spade", "blank_flange", "blind_flange", "disconnect", "breaker"}
POSITIVE_ENTITY_KEYWORDS = {"blind", "spade", "spectacle", "blank_flange", "blind_flange"}
VERIFICATION_TAG_PREFIXES = VERIFY_TAG_PREFIXES


def build_evidence(candidate_data, config):
    candidates = candidate_data.get("candidates", []) or []
    source_keys = set()
    covered_sources = set()
    summaries = []
    barrier_ids = []
    positive_ids = []
    verification_ids = []
    manual_review_ids = []
    manual_review_labels = []
    unresolved_bbox_ids = []

    for candidate in candidates:
        for path in candidate.get("source_paths") or []:
            key = str(path.get("source_component_id") or path.get("source_component_tag") or "").strip()
            if key:
                source_keys.add(key)
                covered_sources.add(key)
        flags = candidate_flags(candidate, config.policy)
        if flags["barrier"]:
            barrier_ids.append(candidate.get("candidate_id"))
        if flags["positive"]:
            positive_ids.append(candidate.get("candidate_id"))
        if flags["verification"]:
            verification_ids.append(candidate.get("candidate_id"))
        if _requires_manual_review(candidate):
            manual_review_ids.append(candidate.get("candidate_id"))
            manual_review_labels.append(_candidate_review_label(candidate))
        if not candidate.get("bbox"):
            unresolved_bbox_ids.append(candidate.get("candidate_id"))
        summaries.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "visual_id": candidate.get("visual_id"),
                "tag_number": candidate.get("tag_number"),
                "entity_class": (candidate.get("properties") or {}).get("entity_class") or candidate.get("candidate_label"),
                "equipment_tag": candidate.get("equipment_tag"),
                "source_component_tag": candidate.get("source_component_tag"),
                "source_path_count": candidate.get("source_path_count", 1),
                "traversal_depth": candidate.get("traversal_depth"),
                "bbox_resolved": bool(candidate.get("bbox")),
                "policy_decision": candidate.get("policy_decision") or (candidate.get("classification") or {}).get("decision"),
                "requires_manual_review": bool(candidate.get("requires_manual_review")),
                "barrier_evidence": flags["barrier"],
                "positive_isolation_evidence": flags["positive"],
                "verification_evidence": flags["verification"],
            }
        )

    missing = []
    if not candidates:
        missing.append("No isolation candidates were found for the selected equipment.")
    if not verification_ids:
        missing.append("No bleed, vent, drain, gauge, pressure indicator, or approved test-point evidence was found.")
    if config.work_scope.requires_positive_isolation and not positive_ids:
        missing.append("Work scope requires positive isolation evidence, but no blind, spade, blank flange, disconnection, breaker, or equivalent was found.")
    if manual_review_ids:
        labels = ", ".join(label for label in manual_review_labels[:8] if label)
        suffix = f": {labels}" if labels else ""
        missing.append(f"Selected conditional isolation candidate(s) require manual review before acceptance{suffix}.")

    obligations = candidate_data.get("isolation_obligations") or {}
    obligation_counts = _obligation_counts(obligations)
    expected_boundary_count = obligation_counts.get("expected")
    covered_count = obligation_counts.get("covered", len(covered_sources))
    missing_boundary_count = obligation_counts.get("missing")
    if expected_boundary_count is None:
        expected_boundary_count = _expected_boundary_count(candidate_data)
        covered_count = len(covered_sources)
        missing_boundary_count = max(expected_boundary_count - covered_count, 0) if expected_boundary_count is not None else None
    if missing_boundary_count:
        missing.append(f"{missing_boundary_count} equipment boundary path(s) do not have a selected isolation candidate.")
    unselected_sources = (candidate_data.get("debug") or {}).get("bbox_unselected_source_components") or []
    unresolved_obligations = _unresolved_obligations(obligations)
    context_instruments = candidate_data.get("context_instruments") or (candidate_data.get("debug") or {}).get("context_instruments") or []
    boundary_context_sources = candidate_data.get("boundary_context_sources") or context_instruments
    if unselected_sources and (obligations.get("status") != "completed"):
        source_tags = ", ".join(_source_warning_label(item) for item in unselected_sources[:8])
        missing.append(f"Some equipment boundary source(s) were not selected because only distant or visually unresolved candidates were found: {source_tags}.")
    if unresolved_obligations:
        labels = ", ".join(_obligation_label(item) for item in unresolved_obligations[:8])
        missing.append(f"Unresolved process isolation obligation(s): {labels}. Field/UI resolution is required.")

    evidence_state = {
        "code_version": "local_evidence_state_2026-06-29_v1",
        "context": candidate_data.get("context") or config.context,
        "work_scope": config.work_scope.__dict__,
        "candidate_count": len(candidates),
        "expected_boundary_count": expected_boundary_count,
        "covered_boundary_source_count": covered_count,
        "missing_boundary_count": missing_boundary_count,
        "unselected_boundary_sources": unselected_sources,
        "boundary_context_sources": boundary_context_sources,
        "context_instruments": context_instruments,
        "isolation_obligations": obligations,
        "unresolved_isolation_obligations": unresolved_obligations,
        "candidate_summaries": summaries,
        "barrier_candidate_ids": barrier_ids,
        "positive_candidate_ids": positive_ids,
        "verification_candidate_ids": verification_ids,
        "manual_review_candidate_ids": manual_review_ids,
        "bypass_candidate_ids": [],
        "unresolved_bbox_candidate_ids": unresolved_bbox_ids,
        "missing_evidence": missing,
    }
    debug = dict(candidate_data.get("debug", {}) or {})
    debug.update(
        {
            "evidence_candidate_count": len(candidates),
            "evidence_barrier_candidate_count": len(barrier_ids),
            "evidence_positive_candidate_count": len(positive_ids),
            "evidence_verification_candidate_count": len(verification_ids),
            "evidence_manual_review_candidate_count": len(manual_review_ids),
            "evidence_missing_evidence_count": len(missing),
            "evidence_isolation_obligation_count": obligation_counts.get("total"),
            "evidence_unresolved_isolation_obligation_count": len(unresolved_obligations),
        }
    )
    return {**candidate_data, "debug": debug, "evidence_state": evidence_state, "missing_evidence": missing}


def _expected_boundary_count(candidate_data):
    debug = candidate_data.get("debug", {}) or {}
    if debug.get("bbox_source_visual_selection_samples") is not None and debug.get("bbox_unselected_source_components") is not None:
        return len(debug.get("bbox_source_visual_selection_samples") or []) + len(debug.get("bbox_unselected_source_components") or [])
    value = debug.get("boundary_component_boundary_count")
    if value is not None:
        return int(value)
    return None


def _obligation_counts(obligations):
    if (obligations or {}).get("status") != "completed":
        return {}
    items = obligations.get("items") or []
    process_items = [item for item in items if item.get("source_type") == SourceType.PROCESS.value]
    return {
        "total": len(items),
        "expected": len(process_items),
        "covered": sum(1 for item in process_items if item.get("status") == ObligationStatus.ISOLATED.value),
        "missing": sum(1 for item in process_items if item.get("status") == ObligationStatus.UNRESOLVED.value),
    }


def _unresolved_obligations(obligations):
    if (obligations or {}).get("status") != "completed":
        return []
    return [
        item
        for item in obligations.get("items") or []
        if item.get("source_type") == SourceType.PROCESS.value and item.get("status") == ObligationStatus.UNRESOLVED.value
    ]


def _obligation_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label:
        return label
    return str(item.get("source_component") or "unknown source")


def _source_warning_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label:
        return label
    if item.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return str(item.get("source_component") or "unknown source")


def _requires_manual_review(candidate):
    decision = str(candidate.get("policy_decision") or (candidate.get("classification") or {}).get("decision") or "")
    return bool(candidate.get("requires_manual_review")) or decision == IsolationDecision.CONDITIONAL_MANUAL_REVIEW.value


def _candidate_review_label(candidate):
    properties = candidate.get("properties") or {}
    label = (
        candidate.get("tag_number")
        or properties.get("tag")
        or properties.get("entity_class")
        or candidate.get("candidate_label")
        or candidate.get("candidate_id")
    )
    source = candidate.get("source_component_tag")
    if source:
        return f"{label} for {source}"
    return str(label or "")


def candidate_flags(candidate, policy=None):
    classification = _candidate_classification(candidate, policy)
    return {
        "barrier": classification.is_barrier,
        "positive": classification.is_positive_isolation,
        "verification": classification.is_verification,
    }


def _candidate_classification(candidate, policy=None):
    existing = candidate.get("classification") or {}
    if existing and existing.get("decision"):
        return _classification_from_payload(existing)
    if policy is None:
        policy = _FallbackPolicy()
    properties = candidate.get("properties", {}) or {}
    method_text = " ".join(str(candidate.get(key) or "").lower() for key in ("isolation_method", "reason"))
    tag_prefix = _tag_prefix(properties.get("tag") or candidate.get("tag_number"))
    return classify_candidate(properties, candidate.get("candidate_label"), policy, method_text=method_text, tag_prefix=tag_prefix)


def _classification_from_payload(payload):
    from domain.models import CandidateClassification

    class_values = tuple(payload.get("class_values") or ())
    raw_entity_class = str(payload.get("raw_entity_class") or "")
    values = class_values or ((raw_entity_class,) if raw_entity_class else ())
    decision = IsolationDecision(str(payload.get("decision") or IsolationDecision.NOT_ISOLATION.value))
    is_barrier = payload.get("is_barrier")
    if is_barrier is None:
        is_barrier = decision == IsolationDecision.AUTOMATIC and any(is_operable_barrier(value) for value in values)
    is_positive = payload.get("is_positive_isolation")
    if is_positive is None:
        is_positive = any(is_installed_positive_isolation(value) for value in values)
    return CandidateClassification(
        raw_entity_class=raw_entity_class,
        raw_entity_type=str(payload.get("raw_entity_type") or ""),
        class_values=class_values,
        matched_policy_classes=tuple(payload.get("matched_policy_classes") or ()),
        decision=decision,
        is_barrier=bool(is_barrier),
        is_positive_isolation=bool(is_positive),
        is_verification=bool(payload.get("is_verification")),
    )


class _FallbackPolicy:
    eligible_classes = tuple(BARRIER_KEYWORDS)
    excluded_classes = ()
    conditional_classes = ()
    include_conditional_candidates = False
    positive_isolation_classes = tuple(POSITIVE_ENTITY_KEYWORDS)
    verification_classes = tuple(VERIFICATION_ENTITY_KEYWORDS)
    verification_tag_prefixes = tuple(VERIFICATION_TAG_PREFIXES)
