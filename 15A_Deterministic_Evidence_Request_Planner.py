from langflow.custom import Component
from langflow.io import DataInput, IntInput, Output
from langflow.schema import Data, Message
import json


PLANNER_CODE_VERSION = "15A_deterministic_evidence_request_planner_2026-06-29_v1"


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


class DeterministicEvidenceRequestPlanner(Component):
    display_name = "Deterministic Evidence Request Planner"
    description = "Requests graph/API evidence deterministically without any LLM"
    icon = "list-checks"
    name = "DeterministicEvidenceRequestPlanner"

    inputs = [
        DataInput(name="evidence_data", display_name="Isolation Evidence Data"),
        IntInput(name="max_tool_requests", display_name="Max Tool Requests", value=8),
    ]

    outputs = [
        Output(display_name="Planner Data", name="planner_data", method="build_data"),
        Output(display_name="Planner Summary", name="planner_summary", method="build_summary"),
    ]

    def _request(self, tool_name, reason, arguments=None, priority="medium"):
        return {
            "tool_name": tool_name,
            "priority": priority if priority in {"high", "medium", "low"} else "medium",
            "reason": reason,
            "arguments": arguments or {},
            "status": "requested",
            "source": "deterministic",
        }

    def _limit_requests(self, requests):
        try:
            max_requests = int(self.max_tool_requests)
        except Exception:
            max_requests = 8
        return requests[:max_requests] if max_requests > 0 else requests

    def _build_requests(self, evidence, context):
        work_scope = evidence.get("work_scope") or {}
        requests = []
        common_args = {
            "job_id": context.get("job_id"),
            "job_name": context.get("job_name"),
            "project_id": context.get("project_id"),
            "collection_id": context.get("collection_id"),
        }

        if evidence.get("missing_boundary_count"):
            requests.append(
                self._request(
                    "find_paths_from_nozzle",
                    "Resolve equipment boundary paths without a selected isolation candidate.",
                    common_args,
                    "high",
                )
            )
            requests.append(
                self._request(
                    "find_nearest_isolation_devices",
                    "Find nearest approved isolation device for each unresolved boundary path.",
                    common_args,
                    "high",
                )
            )

        if not evidence.get("verification_candidate_ids"):
            requests.append(
                self._request(
                    "find_bleeds_vents_drains",
                    "Find stored-energy release points for proving zero or safe energy.",
                    common_args,
                    "high",
                )
            )
            requests.append(
                self._request(
                    "find_pressure_indicators",
                    "Find pressure gauges, pressure indicators, or approved test points near isolated sections.",
                    common_args,
                    "high",
                )
            )

        requires_positive = any(
            bool(work_scope.get(key))
            for key in (
                "intrusive_work",
                "confined_space_entry",
                "hot_work",
                "high_risk_service",
            )
        )
        if requires_positive and not evidence.get("positive_candidate_ids"):
            requests.append(
                self._request(
                    "find_blinds_spades_flanges",
                    "Work scope requires positive isolation evidence.",
                    common_args,
                    "high",
                )
            )

        requests.append(
            self._request(
                "find_bypass_paths",
                "Check for bypasses or alternate routes around selected barriers.",
                common_args,
                "medium",
            )
        )

        if evidence.get("unresolved_bbox_candidate_ids"):
            requests.append(
                self._request(
                    "fetch_stlm_symbols",
                    "Resolve exact STLM visual evidence for candidates without safe bboxes.",
                    common_args,
                    "medium",
                )
            )
            requests.append(
                self._request(
                    "fetch_hilt_graph",
                    "Resolve HILT visual graph evidence for candidates without safe bboxes.",
                    common_args,
                    "medium",
                )
            )

        if (
            not evidence.get("positive_candidate_ids")
            or not evidence.get("verification_candidate_ids")
            or evidence.get("bypass_candidate_ids")
        ):
            requests.append(
                self._request(
                    "fetch_pid_visual_json",
                    "Inspect P&ID visual JSON when graph evidence lacks required safety devices.",
                    common_args,
                    "low",
                )
            )

        return self._limit_requests(requests)

    def _build_payload(self):
        data = _unwrap_data(self.evidence_data) or {}
        if data.get("error"):
            return data

        evidence = data.get("evidence_state") or {}
        context = data.get("context") or {}
        requests = self._build_requests(evidence, context)
        summary = (
            "Deterministic planner requested graph/API evidence for unresolved "
            "isolation assurance categories. No LLM was used."
        )

        planner_state = {
            "code_version": PLANNER_CODE_VERSION,
            "mode": "deterministic_graph_api_evidence_requests",
            "llm_enabled": False,
            "llm_error": None,
            "summary": summary,
            "approved_tool_requests": requests,
            "loop_break_conditions": [
                "deterministic_validator_reaches_terminal_status",
                "approved_tool_budget_exhausted",
                "all_missing_evidence_categories_have_been_requested",
            ],
        }

        debug = dict(data.get("debug", {}) or {})
        debug.update(
            {
                "planner_code_version": PLANNER_CODE_VERSION,
                "planner_mode": planner_state["mode"],
                "planner_llm_error": None,
                "planner_tool_request_count": len(requests),
                "planner_requested_tools": [request["tool_name"] for request in requests],
            }
        )

        return {
            **data,
            "debug": debug,
            "planner_state": planner_state,
            "approved_tool_requests": requests,
            "llm_planner_result": None,
            "deterministic_planner_result": {
                "summary": summary,
                "evidence_requests": requests,
            },
        }

    def build_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_summary(self) -> Message:
        return Message(
            text="Deterministic evidence request plan:\n"
            + json.dumps(self._build_payload(), indent=2)
        )
