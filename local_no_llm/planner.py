SAFETY_CRITICAL_CHECKS = {
    "find_bypass_paths",
    "find_blinds_spades_flanges",
    "find_bleeds_vents_drains",
    "find_pressure_indicators",
}


def plan_requests(evidence_data, config):
    evidence = evidence_data.get("evidence_state") or {}
    context = evidence_data.get("context") or config.context
    checks = []
    args = {
        "job_id": context.get("job_id"),
        "job_name": context.get("job_name"),
        "project_id": context.get("project_id"),
        "collection_id": context.get("collection_id"),
    }
    if not evidence.get("verification_candidate_ids"):
        checks.append(_check("find_bleeds_vents_drains", "Find stored-energy release points for proving zero or safe energy.", args, "high"))
        checks.append(_check("find_pressure_indicators", "Find pressure gauges, pressure indicators, or approved test points near isolated sections.", args, "high"))
    if config.work_scope.requires_positive_isolation and not evidence.get("positive_candidate_ids"):
        checks.append(_check("find_blinds_spades_flanges", "Work scope requires positive isolation evidence.", args, "high"))
    checks.append(_check("find_bypass_paths", "Check for bypasses or alternate routes around selected barriers.", args, "medium"))
    if not evidence.get("positive_candidate_ids") or not evidence.get("verification_candidate_ids"):
        checks.append(_check("fetch_pid_visual_json", "Inspect P&ID visual JSON when graph evidence lacks required safety devices.", args, "low"))

    debug = dict(evidence_data.get("debug", {}) or {})
    debug.update(
        {
            "planner_code_version": "local_deterministic_planner_2026-06-29_v1",
            "planner_mode": "deterministic_graph_api_evidence_checks",
            "planner_required_evidence_check_count": len(checks),
            "planner_required_evidence_checks": [check["check_name"] for check in checks],
        }
    )
    return {
        **evidence_data,
        "debug": debug,
        "required_evidence_checks": checks,
        "planner_state": {
            "mode": "deterministic_graph_api_evidence_checks",
            "required_evidence_checks": checks,
        },
    }


def _check(check_name, reason, arguments, priority):
    return {
        "check_name": check_name,
        "priority": priority,
        "reason": reason,
        "arguments": arguments,
        "status": "required",
        "source": "deterministic_rule",
    }
