BARRIER_KEYWORDS = {"valve", "blind", "spade", "disconnect", "breaker"}
POSITIVE_ENTITY_KEYWORDS = {"blind", "spade", "spectacle", "blank", "disconnect", "breaker", "spool"}
VERIFICATION_ENTITY_KEYWORDS = {"bleed", "vent", "drain", "gauge", "indicator", "test point"}
VERIFICATION_TAG_PREFIXES = {"pi", "pg"}


def build_evidence(candidate_data, config):
    candidates = candidate_data.get("candidates", []) or []
    source_keys = set()
    covered_sources = set()
    summaries = []
    barrier_ids = []
    positive_ids = []
    verification_ids = []
    unresolved_bbox_ids = []

    for candidate in candidates:
        for path in candidate.get("source_paths") or []:
            key = str(path.get("source_component_id") or path.get("source_component_tag") or "").strip()
            if key:
                source_keys.add(key)
                covered_sources.add(key)
        flags = _flags(candidate)
        if flags["barrier"]:
            barrier_ids.append(candidate.get("candidate_id"))
        if flags["positive"]:
            positive_ids.append(candidate.get("candidate_id"))
        if flags["verification"]:
            verification_ids.append(candidate.get("candidate_id"))
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

    expected_boundary_count = _expected_boundary_count(candidate_data)
    covered_count = len(covered_sources)
    missing_boundary_count = max(expected_boundary_count - covered_count, 0) if expected_boundary_count is not None else None
    if missing_boundary_count:
        missing.append(f"{missing_boundary_count} equipment boundary path(s) do not have a selected isolation candidate.")
    unselected_sources = (candidate_data.get("debug") or {}).get("bbox_unselected_source_components") or []
    context_instruments = candidate_data.get("context_instruments") or (candidate_data.get("debug") or {}).get("context_instruments") or []
    boundary_context_sources = candidate_data.get("boundary_context_sources") or context_instruments
    if unselected_sources:
        source_tags = ", ".join(_source_warning_label(item) for item in unselected_sources[:8])
        missing.append(f"Some equipment boundary source(s) were not selected because only distant or visually unresolved candidates were found: {source_tags}.")

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
        "candidate_summaries": summaries,
        "barrier_candidate_ids": barrier_ids,
        "positive_candidate_ids": positive_ids,
        "verification_candidate_ids": verification_ids,
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
            "evidence_missing_evidence_count": len(missing),
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


def _source_warning_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label:
        return label
    if item.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return str(item.get("source_component") or "unknown source")


def _flags(candidate):
    properties = candidate.get("properties", {}) or {}
    entity_text = " ".join(
        str(properties.get(key) or candidate.get(key) or "").lower()
        for key in ("entity_class", "candidate_label", "type", "entity_type", "valve_type", "category")
    )
    method_text = " ".join(str(candidate.get(key) or "").lower() for key in ("isolation_method", "reason"))
    tag_prefix = _tag_prefix(properties.get("tag") or candidate.get("tag_number"))
    barrier = any(keyword in entity_text for keyword in BARRIER_KEYWORDS) or "close and lock" in method_text
    positive = any(keyword in entity_text for keyword in POSITIVE_ENTITY_KEYWORDS)
    verification = any(keyword in entity_text for keyword in VERIFICATION_ENTITY_KEYWORDS) or tag_prefix in VERIFICATION_TAG_PREFIXES
    if "valve" in entity_text:
        positive = any(keyword in entity_text for keyword in POSITIVE_ENTITY_KEYWORDS)
        verification = tag_prefix in VERIFICATION_TAG_PREFIXES or any(keyword in entity_text for keyword in VERIFICATION_ENTITY_KEYWORDS)
    return {"barrier": barrier, "positive": positive, "verification": verification}


def _tag_prefix(value):
    result = []
    for char in str(value or "").strip().lower():
        if char.isalpha():
            result.append(char)
            continue
        break
    return "".join(result)
