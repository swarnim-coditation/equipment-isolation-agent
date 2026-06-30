from langflow.custom import Component
from langflow.io import DataInput, IntInput, MessageTextInput, Output
from langflow.schema import Data, Message
import json


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


class FinalIsolationOutput(Component):
    display_name = "Final Isolation Output"
    description = "Builds deterministic final isolation JSON from ranked candidates"
    icon = "braces"
    name = "FinalIsolationOutput"

    inputs = [
        DataInput(name="candidate_data", display_name="Isolation Candidates"),
        IntInput(
            name="max_results",
            display_name="Max Results",
            info="Maximum ranked isolation candidates to return",
            value=3,
        ),
        MessageTextInput(
            name="job_id",
            display_name="P&ID ID",
            info="P&ID/job id used by the rigid Isolation Points UI",
            value="274",
        ),
        MessageTextInput(
            name="job_name",
            display_name="P&ID Name",
            info="P&ID/job name shown in the Isolation Points UI",
            value="Equipment Isolation - P-08",
        ),
        MessageTextInput(
            name="project_id",
            display_name="Project ID",
            value="274",
        ),
        MessageTextInput(
            name="project_name",
            display_name="Project Name",
            value="Project 274",
        ),
        MessageTextInput(
            name="collection_id",
            display_name="Collection ID",
            value="0",
        ),
        MessageTextInput(
            name="collection_name",
            display_name="Collection Name",
            value="Graph Isolation Results",
        ),
    ]

    outputs = [
        Output(display_name="Final Data", name="final_data", method="build_final_data"),
        Output(display_name="Final Message", name="final_message", method="build_final_message"),
    ]

    def _int_or_zero(self, value):
        try:
            return int(value)
        except Exception:
            return 0

    def _build_points(self):
        data = _unwrap_data(self.candidate_data) or {}
        if data.get("error"):
            return [], data.get("message"), data.get("debug", {})

        points = []
        for candidate in data.get("candidates", [])[: int(self.max_results)]:
            energy_type = candidate.get("energy_type") or []
            if isinstance(energy_type, list):
                energy_type = energy_type[0] if energy_type else None

            reason = candidate.get("reason") or "Ranked deterministic isolation candidate"
            candidate_id = candidate.get("candidate_id")
            source = candidate.get("source_component_tag")
            if candidate_id:
                reason = f"{reason}. Candidate vertex id: {candidate_id}."
            if source:
                reason = f"{reason} Source component: {source}."

            points.append(
                {
                    "equipment_id": candidate.get("equipment_tag"),
                    "uuid": str(candidate.get("candidate_id") or ""),
                    "bbox": candidate.get("bbox") or [],
                    "entity_class": candidate.get("properties", {}).get("entity_class") or candidate.get("candidate_label") or "isolation_point",
                    "tag_number": candidate.get("tag_number"),
                    "energy_type": energy_type,
                    "isolation_method": candidate.get("isolation_method"),
                    "reason": reason,
                }
            )

        return points, None, data.get("debug", {})

    def _build_payload(self):
        points, error, debug = self._build_points()
        if error:
            return {"error": True, "message": error, "data": [], "debug": debug}

        return {
            "error": False,
            "message": "Completed",
            "total_jobs_processed": 1,
            "debug": {
                "bbox_resolved_count": debug.get("bbox_resolved_count"),
                "bbox_token_resolved_count": debug.get("bbox_token_resolved_count"),
                "bbox_position_resolved_count": debug.get("bbox_position_resolved_count"),
                "bbox_transform_resolved_count": debug.get("bbox_transform_resolved_count"),
                "bbox_unresolved_candidate_ids": debug.get("bbox_unresolved_candidate_ids"),
                "bbox_image_size": debug.get("bbox_image_size"),
                "bbox_hilt_node_count": debug.get("bbox_hilt_node_count"),
                "bbox_transform": debug.get("bbox_transform"),
                "bbox_transform_control_points": debug.get("bbox_transform_control_points"),
                "bbox_match_samples": debug.get("bbox_match_samples"),
                "bbox_error": debug.get("bbox_error"),
            },
            "data": [
                {
                    "job_id": self._int_or_zero(self.job_id),
                    "job_name": self.job_name,
                    "project_id": self._int_or_zero(self.project_id),
                    "project_name": self.project_name,
                    "collection_id": self._int_or_zero(self.collection_id),
                    "collection_name": self.collection_name,
                    "isolation_points": points,
                }
            ],
        }

    def build_final_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_final_message(self) -> Message:
        lines = ["Final isolation UI payload:", json.dumps(self._build_payload(), indent=2)]
        return Message(text="\n".join(lines))
