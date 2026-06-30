import json
import requests
from pydantic.v1 import SecretStr

from langflow.custom import Component
from langflow.io import (
    BoolInput,
    DataInput,
    IntInput,
    MessageTextInput,
    MultilineInput,
    Output,
    SecretStrInput,
)
from langflow.schema import Data, Message


PLANNER_CODE_VERSION = "15_llm_evidence_request_planner_2026-06-26_strip_unsafe_notes_v4"

APPROVED_TOOLS = {
    "find_equipment_boundaries",
    "find_paths_from_nozzle",
    "find_nearest_isolation_devices",
    "find_bleeds_vents_drains",
    "find_pressure_indicators",
    "find_blinds_spades_flanges",
    "find_bypass_paths",
    "check_alternate_route_to_equipment",
    "classify_connected_energy_sources",
    "fetch_pid_visual_json",
    "fetch_hilt_graph",
    "fetch_stlm_symbols",
    "inspect_pid_image_region",
}

DEFAULT_LLM_PROMPT = """You are an equipment isolation planning assistant.

You will receive deterministic evidence from Unigraph/CNVRT. You may reason over that evidence and request more evidence using only approved named tools. You must not invent equipment, valves, blinds, bleeds, vents, drains, tags, graph paths, visual IDs, or bboxes.

Return strict JSON with this shape:
{
  "summary": "short explanation",
  "evidence_requests": [
    {
      "tool_name": "one approved tool name",
      "priority": "high | medium | low",
      "reason": "why this evidence is needed",
      "arguments": {}
    }
  ],
  "isolation_quality_observation": "short non-authoritative observation",
  "procedure_notes": ["ordered planning note"]
}

Important rules:
- The deterministic validator will decide final assurance status.
- If evidence is missing, request evidence rather than assuming it exists.
- Prefer positive isolation evidence for intrusive, hot, confined-space, or high-risk work.
- Request verification evidence such as bleed, vent, drain, gauge, pressure indicator, or test point when proof of zero or safe energy is missing.
- Request bypass/alternate-route checks before claiming complete isolation.
- Do not write procedure notes that instruct installing, opening, using, or verifying against blinds, spades, bleeds, vents, drains, gauges, or bypass status unless those devices are present in the supplied deterministic evidence.
"""


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _secret_value(value):
    if value in (None, ""):
        return ""
    try:
        return SecretStr(value).get_secret_value()
    except Exception:
        return str(value)


class LLMEvidenceRequestPlanner(Component):
    display_name = "LLM Evidence Request Planner"
    description = "Uses an LLM to request constrained evidence for isolation planning"
    icon = "bot-message-square"
    name = "LLMEvidenceRequestPlanner"

    inputs = [
        DataInput(name="evidence_data", display_name="Isolation Evidence Data"),
        BoolInput(
            name="enable_llm",
            display_name="Enable LLM Planning",
            value=True,
            info="If disabled or if the LLM call fails, the node falls back to deterministic tool-request planning.",
        ),
        MessageTextInput(
            name="llm_client_name",
            display_name="LLM Client Name",
            value="gemini",
            info="Currently supports gemini.",
        ),
        MessageTextInput(
            name="llm_model",
            display_name="LLM Model",
            value="gemini-2.5-flash",
        ),
        SecretStrInput(
            name="llm_api_key",
            display_name="LLM Authentication Token",
            required=False,
            value="",
        ),
        MultilineInput(
            name="custom_prompt",
            display_name="Planner Prompt",
            value=DEFAULT_LLM_PROMPT,
        ),
        IntInput(name="max_tool_requests", display_name="Max Tool Requests", value=8),
        BoolInput(name="verify_ssl", display_name="Verify SSL", value=True),
    ]

    outputs = [
        Output(display_name="Planner Data", name="planner_data", method="build_data"),
        Output(display_name="Planner Summary", name="planner_summary", method="build_summary"),
    ]

    def _request(self, tool_name, reason, arguments=None, priority="medium", source="deterministic"):
        return {
            "tool_name": tool_name,
            "priority": priority if priority in {"high", "medium", "low"} else "medium",
            "reason": reason,
            "arguments": arguments or {},
            "status": "requested",
            "source": source,
        }

    def _deterministic_requests(self, evidence, context):
        work_scope = evidence.get("work_scope") or {}
        requests = []
        common_args = {
            "job_id": context.get("job_id"),
            "job_name": context.get("job_name"),
            "project_id": context.get("project_id"),
            "collection_id": context.get("collection_id"),
        }

        if evidence.get("missing_boundary_count"):
            requests.append(self._request("find_paths_from_nozzle", "Resolve equipment boundary paths without a selected isolation candidate.", common_args, "high"))
            requests.append(self._request("find_nearest_isolation_devices", "Find nearest approved isolation device for each unresolved boundary path.", common_args, "high"))

        if not evidence.get("verification_candidate_ids"):
            requests.append(self._request("find_bleeds_vents_drains", "Find stored-energy release points for proving zero or safe energy.", common_args, "high"))
            requests.append(self._request("find_pressure_indicators", "Find pressure gauges, pressure indicators, or approved test points near isolated sections.", common_args, "high"))

        requires_positive = any(bool(work_scope.get(key)) for key in ("intrusive_work", "confined_space_entry", "hot_work", "high_risk_service"))
        if requires_positive and not evidence.get("positive_candidate_ids"):
            requests.append(self._request("find_blinds_spades_flanges", "Work scope requires positive isolation evidence.", common_args, "high"))

        requests.append(self._request("find_bypass_paths", "Check for bypasses or alternate routes around selected barriers.", common_args, "medium"))

        if evidence.get("unresolved_bbox_candidate_ids"):
            requests.append(self._request("fetch_stlm_symbols", "Resolve exact visual evidence for candidates without safe bboxes.", common_args, "medium"))
            requests.append(self._request("fetch_hilt_graph", "Fallback visual graph lookup for candidates without safe bboxes.", common_args, "medium"))

        requests.append(self._request("fetch_pid_visual_json", "Inspect P&ID visual JSON when graph evidence lacks required safety devices.", common_args, "low"))
        return requests

    def _limit_requests(self, requests):
        try:
            max_requests = int(self.max_tool_requests)
        except Exception:
            max_requests = 8
        if max_requests > 0:
            return requests[:max_requests]
        return requests

    def _sanitize_llm_requests(self, raw_requests, context):
        sanitized = []
        for item in raw_requests or []:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            if tool_name not in APPROVED_TOOLS:
                continue
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            for key in ("job_id", "job_name", "project_id", "collection_id"):
                if context.get(key) not in (None, "", []):
                    arguments.setdefault(key, context.get(key))
            sanitized.append(
                self._request(
                    tool_name,
                    str(item.get("reason") or "LLM requested additional evidence."),
                    arguments,
                    str(item.get("priority") or "medium"),
                    "llm",
                )
            )
        return sanitized

    def _merge_required_requests(self, llm_requests, deterministic_requests):
        merged = []
        seen = set()

        for request in (llm_requests or []) + (deterministic_requests or []):
            tool_name = request.get("tool_name") if isinstance(request, dict) else None
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            merged.append(request)
        return merged

    def _sanitize_llm_result(self, llm_result, sanitized_requests):
        if not isinstance(llm_result, dict):
            return llm_result

        safe_result = dict(llm_result)
        safe_result["evidence_requests"] = [
            {
                "tool_name": request.get("tool_name"),
                "priority": request.get("priority"),
                "reason": request.get("reason"),
                "arguments": request.get("arguments") or {},
            }
            for request in sanitized_requests
        ]

        # LLM procedure notes can accidentally become instructions for devices that
        # have only been requested, not deterministically found. Keep procedure
        # writing in the validator-backed Procedure Writer node.
        if safe_result.get("procedure_notes"):
            safe_result["procedure_notes"] = []
            safe_result[
                "procedure_notes_omitted_reason"
            ] = "Procedure notes are omitted until requested evidence is deterministically resolved."
        return safe_result

    def _call_gemini(self, evidence, deterministic_requests):
        api_key = _secret_value(getattr(self, "llm_api_key", ""))
        if not api_key:
            return None, "missing_llm_api_key"

        client_name = str(getattr(self, "llm_client_name", "gemini") or "gemini").strip().lower()
        if client_name != "gemini":
            return None, f"unsupported_llm_client: {client_name}"

        model = str(getattr(self, "llm_model", "gemini-2.5-flash") or "gemini-2.5-flash").strip()
        prompt = str(getattr(self, "custom_prompt", "") or DEFAULT_LLM_PROMPT)
        request_body = {
            "evidence_state": evidence,
            "deterministic_request_hints": deterministic_requests,
            "approved_tools": sorted(APPROVED_TOOLS),
        }

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"text": "Evidence package:\n" + json.dumps(request_body, indent=2)},
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_json_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "evidence_requests": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tool_name": {"type": "string"},
                                    "priority": {"type": "string"},
                                    "reason": {"type": "string"},
                                    "arguments": {"type": "object"},
                                },
                                "required": ["tool_name", "priority", "reason"],
                            },
                        },
                        "isolation_quality_observation": {"type": "string"},
                        "procedure_notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["summary", "evidence_requests"],
                },
            },
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        response = requests.post(url, headers=headers, json=payload, timeout=60, verify=bool(self.verify_ssl))
        response.raise_for_status()
        result = response.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text), None

    def _build_payload(self):
        data = _unwrap_data(self.evidence_data) or {}
        if data.get("error"):
            return data

        evidence = data.get("evidence_state") or {}
        context = data.get("context") or {}
        deterministic_requests = self._deterministic_requests(evidence, context)
        llm_result = None
        llm_error = None

        if bool(getattr(self, "enable_llm", True)):
            try:
                llm_result, llm_error = self._call_gemini(evidence, deterministic_requests)
            except Exception as exc:
                llm_error = str(exc)
        else:
            llm_error = "llm_disabled"

        llm_requests = self._sanitize_llm_requests(
            (llm_result or {}).get("evidence_requests"), context
        )
        sanitized_llm_result = self._sanitize_llm_result(llm_result, llm_requests)
        if llm_requests:
            requests = self._limit_requests(
                self._merge_required_requests(llm_requests, deterministic_requests)
            )
        else:
            requests = self._limit_requests(deterministic_requests)
        planner_mode = "llm_constrained_named_tool_requests" if llm_requests else "deterministic_fallback_named_tool_requests"

        planner_state = {
            "code_version": PLANNER_CODE_VERSION,
            "mode": planner_mode,
            "llm_client_name": getattr(self, "llm_client_name", "gemini"),
            "llm_model": getattr(self, "llm_model", None),
            "llm_enabled": bool(getattr(self, "enable_llm", True)),
            "llm_error": llm_error,
            "llm_result": sanitized_llm_result,
            "llm_may_request_more_evidence": True,
            "llm_must_not_invent_evidence": True,
            "llm_must_not_override_validator": True,
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
                "planner_mode": planner_mode,
                "planner_llm_error": llm_error,
                "planner_tool_request_count": len(requests),
                "planner_requested_tools": [request["tool_name"] for request in requests],
            }
        )

        return {
            **data,
            "debug": debug,
            "planner_state": planner_state,
            "approved_tool_requests": requests,
            "llm_planner_result": sanitized_llm_result,
        }

    def build_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_summary(self) -> Message:
        return Message(text="LLM evidence request plan:\n" + json.dumps(self._build_payload(), indent=2))
