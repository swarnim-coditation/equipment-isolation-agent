MISSING_BY_CHECK = {
    "find_bypass_paths": "Bypass or alternate-route evidence check is required but has not been completed by this local deterministic runner.",
    "find_blinds_spades_flanges": "Positive isolation evidence check is required but has not been completed by this local deterministic runner.",
    "find_bleeds_vents_drains": "Bleed, vent, or drain evidence check is required but has not been completed by this local deterministic runner.",
    "find_pressure_indicators": "Pressure gauge, pressure indicator, or test-point evidence check is required but has not been completed by this local deterministic runner.",
}


def validate(planner_data):
    candidates = planner_data.get("candidates", []) or []
    evidence = planner_data.get("evidence_state") or {}
    missing = list(evidence.get("missing_evidence") or planner_data.get("missing_evidence") or [])
    unresolved = []
    for check in planner_data.get("required_evidence_checks") or []:
        check_name = check.get("check_name")
        if check_name in MISSING_BY_CHECK and check.get("status") not in {"completed", "resolved", "not_applicable"}:
            unresolved.append(check)
            if MISSING_BY_CHECK[check_name] not in missing:
                missing.append(MISSING_BY_CHECK[check_name])

    missing_boundary_count = evidence.get("missing_boundary_count")
    barrier_ids = evidence.get("barrier_candidate_ids") or []
    positive_ids = evidence.get("positive_candidate_ids") or []
    verification_ids = evidence.get("verification_candidate_ids") or []

    if not candidates:
        status = "not_isolated"
        rationale = "No isolation candidates were found."
    elif not barrier_ids:
        status = "not_isolated"
        rationale = "No selected candidate has deterministic isolation barrier evidence."
    elif missing_boundary_count and missing_boundary_count > 0:
        status = "not_isolated"
        rationale = "At least one equipment boundary path has no selected isolation barrier."
    elif unresolved:
        status = "provisional_unproven_isolation"
        rationale = "Selected barriers exist, but safety-critical evidence checks remain unresolved."
    elif positive_ids and verification_ids:
        status = "complete_positive_isolation"
        rationale = "Every known boundary path has a selected barrier, positive isolation evidence exists, and verification evidence exists."
    elif verification_ids:
        status = "complete_proven_isolation"
        rationale = "Every known boundary path has a selected barrier and verification evidence exists, but positive isolation evidence was not found."
    else:
        status = "provisional_unproven_isolation"
        rationale = "Selected barriers exist for known boundary paths, but proof of zero or safe energy was not found."

    validation = {
        "code_version": "local_validator_2026-06-29_v1",
        "assurance_status": status,
        "rationale": rationale,
        "terminal": status in {"complete_positive_isolation", "complete_proven_isolation", "not_isolated", "insufficient_data"},
        "candidate_count": len(candidates),
        "expected_boundary_count": evidence.get("expected_boundary_count"),
        "covered_boundary_source_count": evidence.get("covered_boundary_source_count"),
        "missing_boundary_count": missing_boundary_count,
        "unselected_boundary_sources": evidence.get("unselected_boundary_sources") or [],
        "context_instruments": evidence.get("context_instruments") or [],
        "barrier_candidate_ids": barrier_ids,
        "positive_candidate_ids": positive_ids,
        "verification_candidate_ids": verification_ids,
        "bypass_candidate_ids": evidence.get("bypass_candidate_ids") or [],
        "unresolved_bbox_candidate_ids": evidence.get("unresolved_bbox_candidate_ids") or [],
        "unresolved_evidence_checks": unresolved,
        "missing_evidence": missing,
    }
    debug = dict(planner_data.get("debug", {}) or {})
    debug.update(
        {
            "assurance_status": status,
            "validator_terminal": validation["terminal"],
            "validator_unresolved_evidence_check_count": len(unresolved),
            "validator_missing_evidence_count": len(missing),
        }
    )
    return {**planner_data, "debug": debug, "missing_evidence": missing, "isolation_validation": validation, "assurance_status": status}
