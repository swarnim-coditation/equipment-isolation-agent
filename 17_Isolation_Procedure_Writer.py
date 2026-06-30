from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data, Message
import json


PROCEDURE_CODE_VERSION = "17_isolation_procedure_writer_2026-06-25_v1"


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _candidate_label(candidate):
    properties = candidate.get("properties", {}) or {}
    tag = candidate.get("tag_number")
    entity_class = properties.get("entity_class") or candidate.get("candidate_label") or "isolation point"
    candidate_id = candidate.get("candidate_id")
    if tag:
        return f"{tag} ({entity_class}, id {candidate_id})"
    return f"{entity_class} id {candidate_id}"


class IsolationProcedureWriter(Component):
    display_name = "Isolation Procedure Writer"
    description = "Writes ordered isolation and return-to-service steps from validated evidence"
    icon = "list-checks"
    name = "IsolationProcedureWriter"

    inputs = [DataInput(name="validation_data", display_name="Validation Data")]

    outputs = [
        Output(display_name="Procedure Data", name="procedure_data", method="build_data"),
        Output(display_name="Procedure Summary", name="procedure_summary", method="build_summary"),
    ]

    def _build_payload(self):
        data = _unwrap_data(self.validation_data) or {}
        if data.get("error"):
            return data

        candidates = data.get("candidates", []) or []
        context = data.get("context") or {}
        validation = data.get("isolation_validation") or {}
        missing_evidence = validation.get("missing_evidence") or data.get("missing_evidence") or []
        equipment_tags = sorted(
            {
                str(candidate.get("equipment_tag"))
                for candidate in candidates
                if candidate.get("equipment_tag")
            }
        )
        equipment_label = ", ".join(equipment_tags) if equipment_tags else "selected equipment"

        steps = [
            {
                "order": 1,
                "phase": "prepare",
                "action": f"Review work scope, hazards, P&ID {context.get('job_name') or context.get('job_id')}, and plant isolation instructions for {equipment_label}.",
                "evidence_basis": "plant_instructions_and_context",
            },
            {
                "order": 2,
                "phase": "notify",
                "action": "Notify affected personnel and obtain required operations/safety approvals before changing equipment state.",
                "evidence_basis": "standard_loto_sequence",
            },
            {
                "order": 3,
                "phase": "shutdown",
                "action": f"Shut down and depressurize {equipment_label} using approved operating procedures.",
                "evidence_basis": "standard_loto_sequence",
            },
        ]

        order = 4
        for candidate in candidates:
            source = candidate.get("source_component_tag") or candidate.get("source_component_id")
            method = candidate.get("isolation_method") or "isolate and lock/tag"
            steps.append(
                {
                    "order": order,
                    "phase": "isolate",
                    "action": f"Apply {method} at {_candidate_label(candidate)} for source path {source}.",
                    "candidate_id": candidate.get("candidate_id"),
                    "bbox": candidate.get("bbox") or [],
                    "evidence_basis": "deterministic_candidate",
                }
            )
            order += 1

        steps.extend(
            [
                {
                    "order": order,
                    "phase": "lock_tag",
                    "action": "Apply locks and tags according to the approved isolation certificate or LOTO procedure.",
                    "evidence_basis": "standard_loto_sequence",
                },
                {
                    "order": order + 1,
                    "phase": "release_stored_energy",
                    "action": "Release, drain, vent, restrain, or otherwise control stored pressure, liquid, vapor, thermal, pneumatic, hydraulic, electrical, and mechanical energy.",
                    "evidence_basis": "plant_instructions",
                },
                {
                    "order": order + 2,
                    "phase": "verify",
                    "action": "Verify zero or safe energy using supplied bleed, vent, drain, gauge, pressure indicator, or approved test-point evidence. If evidence is missing, stop for human review.",
                    "evidence_basis": "validator_missing_evidence",
                },
                {
                    "order": order + 3,
                    "phase": "perform_work",
                    "action": "Perform work only after authorization confirms the validator status and all missing evidence items are resolved or accepted by responsible personnel.",
                    "evidence_basis": "assurance_validation",
                },
                {
                    "order": order + 4,
                    "phase": "return_to_service",
                    "action": "After work, inspect the area, remove tools and temporary blinds/spades only under authorization, restore valves and devices in controlled sequence, remove locks/tags, notify affected personnel, and monitor startup.",
                    "evidence_basis": "standard_return_to_service_sequence",
                },
            ]
        )

        isolation_plan = {
            "code_version": PROCEDURE_CODE_VERSION,
            "assurance_status": validation.get("assurance_status")
            or data.get("assurance_status"),
            "rationale": validation.get("rationale"),
            "human_review_required": bool(missing_evidence)
            or validation.get("assurance_status")
            not in {"complete_positive_isolation", "complete_proven_isolation"},
            "missing_evidence": missing_evidence,
            "ordered_steps": steps,
            "return_to_service_required": True,
        }

        debug = dict(data.get("debug", {}) or {})
        debug.update(
            {
                "procedure_code_version": PROCEDURE_CODE_VERSION,
                "procedure_step_count": len(steps),
                "procedure_human_review_required": isolation_plan[
                    "human_review_required"
                ],
            }
        )

        return {**data, "debug": debug, "isolation_plan": isolation_plan}

    def build_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_summary(self) -> Message:
        return Message(text="Isolation procedure:\n" + json.dumps(self._build_payload(), indent=2))
