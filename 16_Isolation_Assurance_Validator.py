from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data, Message
import json


VALIDATOR_CODE_VERSION = "16_isolation_assurance_validator_2026-06-26_unresolved_requests_v2"


SAFETY_CRITICAL_REQUEST_TO_MISSING_EVIDENCE = {
    "find_bypass_paths": "Bypass or alternate-route check was requested but no completed deterministic result is available.",
    "check_alternate_route_to_equipment": "Alternate-route check was requested but no completed deterministic result is available.",
    "find_blinds_spades_flanges": "Positive isolation evidence was requested but no completed deterministic result is available.",
    "find_bleeds_vents_drains": "Bleed, vent, or drain evidence was requested but no completed deterministic result is available.",
    "find_pressure_indicators": "Pressure gauge, pressure indicator, or test-point evidence was requested but no completed deterministic result is available.",
}


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


class IsolationAssuranceValidator(Component):
    display_name = "Isolation Assurance Validator"
    description = "Deterministically classifies isolation assurance from supplied evidence"
    icon = "shield-check"
    name = "IsolationAssuranceValidator"

    inputs = [DataInput(name="planner_data", display_name="Planner / Evidence Data")]

    outputs = [
        Output(display_name="Validation Data", name="validation_data", method="build_data"),
        Output(display_name="Validation Summary", name="validation_summary", method="build_summary"),
    ]

    def _build_payload(self):
        data = _unwrap_data(self.planner_data) or {}
        if data.get("error"):
            return data

        candidates = data.get("candidates", []) or []
        evidence = data.get("evidence_state") or {}
        missing_evidence = list(evidence.get("missing_evidence") or data.get("missing_evidence") or [])
        expected_boundary_count = evidence.get("expected_boundary_count")
        covered_count = evidence.get("covered_boundary_source_count") or 0
        missing_boundary_count = evidence.get("missing_boundary_count")
        barrier_ids = evidence.get("barrier_candidate_ids") or []
        positive_ids = evidence.get("positive_candidate_ids") or []
        verification_ids = evidence.get("verification_candidate_ids") or []
        bypass_ids = evidence.get("bypass_candidate_ids") or []
        unresolved_bbox_ids = evidence.get("unresolved_bbox_candidate_ids") or []
        approved_tool_requests = data.get("approved_tool_requests") or []
        unresolved_tool_requests = []

        for request in approved_tool_requests:
            if not isinstance(request, dict):
                continue
            tool_name = request.get("tool_name")
            status_value = str(request.get("status") or "requested").lower()
            if tool_name in SAFETY_CRITICAL_REQUEST_TO_MISSING_EVIDENCE and status_value not in {
                "completed",
                "resolved",
                "not_applicable",
            }:
                unresolved_tool_requests.append(request)
                message = SAFETY_CRITICAL_REQUEST_TO_MISSING_EVIDENCE[tool_name]
                if message not in missing_evidence:
                    missing_evidence.append(message)

        if not candidates:
            status = "not_isolated"
            rationale = "No isolation candidates were found."
        elif not barrier_ids:
            status = "not_isolated"
            rationale = "No selected candidate has deterministic isolation barrier evidence."
        elif missing_boundary_count is None and expected_boundary_count is None:
            status = "insufficient_data"
            rationale = "Boundary coverage count is unavailable, so complete isolation cannot be proven."
        elif missing_boundary_count and missing_boundary_count > 0:
            status = "not_isolated"
            rationale = "At least one equipment boundary path has no selected isolation barrier."
        elif bypass_ids:
            status = "provisional_unproven_isolation"
            rationale = "Selected barriers exist, but possible bypass or alternate-route evidence remains unresolved."
        elif unresolved_tool_requests:
            status = "provisional_unproven_isolation"
            rationale = "Selected barriers exist, but safety-critical evidence requests remain unresolved."
        elif positive_ids and verification_ids:
            status = "complete_positive_isolation"
            rationale = "Every known boundary path has a selected barrier, positive isolation evidence exists, and verification evidence exists."
        elif verification_ids:
            status = "complete_proven_isolation"
            rationale = "Every known boundary path has a selected barrier and verification evidence exists, but positive isolation evidence was not found."
        else:
            status = "provisional_unproven_isolation"
            rationale = "Selected barriers exist for known boundary paths, but proof of zero or safe energy was not found."

        if unresolved_bbox_ids:
            missing_evidence.append(
                "One or more selected isolation candidates do not have a safely resolved P&ID bbox."
            )

        validation = {
            "code_version": VALIDATOR_CODE_VERSION,
            "assurance_status": status,
            "rationale": rationale,
            "terminal": status
            in {
                "complete_positive_isolation",
                "complete_proven_isolation",
                "not_isolated",
                "insufficient_data",
            },
            "candidate_count": len(candidates),
            "expected_boundary_count": expected_boundary_count,
            "covered_boundary_source_count": covered_count,
            "missing_boundary_count": missing_boundary_count,
            "barrier_candidate_ids": barrier_ids,
            "positive_candidate_ids": positive_ids,
            "verification_candidate_ids": verification_ids,
            "bypass_candidate_ids": bypass_ids,
            "unresolved_bbox_candidate_ids": unresolved_bbox_ids,
            "unresolved_tool_requests": unresolved_tool_requests,
            "missing_evidence": missing_evidence,
            "validator_rules": [
                "All known boundary paths require selected barriers.",
                "Bypass and alternate-route evidence prevents complete assurance until resolved.",
                "Positive isolation requires supplied evidence for a physical barrier or physical separation.",
                "Proven isolation requires supplied evidence for bleed, vent, drain, gauge, pressure indicator, or approved test point.",
                "The validator does not accept invented or inferred devices without supplied evidence.",
            ],
        }

        debug = dict(data.get("debug", {}) or {})
        debug.update(
            {
                "validator_code_version": VALIDATOR_CODE_VERSION,
                "assurance_status": status,
                "validator_terminal": validation["terminal"],
                "validator_unresolved_tool_request_count": len(unresolved_tool_requests),
                "validator_missing_evidence_count": len(missing_evidence),
            }
        )

        return {
            **data,
            "debug": debug,
            "missing_evidence": missing_evidence,
            "isolation_validation": validation,
            "assurance_status": status,
        }

    def build_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_summary(self) -> Message:
        return Message(text="Isolation assurance validation:\n" + json.dumps(self._build_payload(), indent=2))
