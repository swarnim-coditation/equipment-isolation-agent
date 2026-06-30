from langflow.custom import Component
from langflow.io import DataInput, IntInput, MessageTextInput, Output
from langflow.schema import Data, Message
import json


FINAL_OUTPUT_CODE_VERSION = "07B_final_ui_output_2026-06-25_input_details_v6"


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


class FinalIsolationUIOutput(Component):
    display_name = "Final Isolation UI Output"
    description = "Builds Isolation Points UI payload from ranked candidates"
    icon = "braces"
    name = "FinalIsolationUIOutput"

    inputs = [
        DataInput(name="candidate_data", display_name="Isolation Candidates"),
        IntInput(
            name="max_results",
            display_name="Max Results (0 = all; old 3 = all)",
            value=0,
        ),
        MessageTextInput(name="job_id", display_name="P&ID ID", value="274"),
        MessageTextInput(
            name="job_name",
            display_name="P&ID Name",
            value="Equipment Isolation - P-08",
        ),
        MessageTextInput(name="project_id", display_name="Project ID", value="274"),
        MessageTextInput(
            name="project_name", display_name="Project Name", value="Project 274"
        ),
        MessageTextInput(name="collection_id", display_name="Collection ID", value="0"),
        MessageTextInput(
            name="collection_name",
            display_name="Collection Name",
            value="Graph Isolation Results",
        ),
    ]

    outputs = [
        Output(display_name="Final Data", name="final_data", method="build_final_data"),
        Output(
            display_name="Final Message",
            name="final_message",
            method="build_final_message",
        ),
    ]

    def _int_or_zero(self, value):
        try:
            return int(value)
        except Exception:
            return 0

    def _context_value(self, context, key, fallback=None):
        value = context.get(key) if isinstance(context, dict) else None
        return value if value not in (None, "", []) else fallback

    def _as_list(self, value):
        if value in (None, "", []):
            return []
        if isinstance(value, list):
            return [item for item in value if item not in (None, "", [])]
        return [value]

    def _selected_equipment(self, data, debug, candidates):
        for value in (
            data.get("selected_equipment"),
            data.get("equipment_tags"),
            debug.get("boundary_requested_equipment_tags"),
            debug.get("config_equipment_tags"),
        ):
            equipment = self._as_list(value)
            if equipment:
                return [str(item) for item in equipment]

        candidate_equipment = []
        seen = set()
        for candidate in candidates:
            equipment = candidate.get("equipment_tag")
            if equipment in (None, "", []) or equipment in seen:
                continue
            seen.add(equipment)
            candidate_equipment.append(str(equipment))
        return candidate_equipment

    def _limited_candidates(self, candidates):
        try:
            max_results = int(self.max_results)
        except Exception:
            max_results = 0
        # Existing Langflow canvases may keep the old default value of 3 even
        # after code import. Treat that legacy default the same as the new all.
        if max_results == 3:
            return candidates, 0
        if max_results > 0:
            return candidates[:max_results], max_results
        return candidates, 0

    def _build_payload(self):
        data = _unwrap_data(self.candidate_data) or {}
        debug = data.get("debug", {}) or {}
        context = data.get("context") or {}

        if data.get("error"):
            return {
                "error": True,
                "message": data.get("message"),
                "data": [],
                "debug": debug,
            }

        source_candidates = data.get("candidates", []) or []
        isolation_plan = data.get("isolation_plan") or {}
        isolation_validation = data.get("isolation_validation") or {}
        missing_evidence = (
            data.get("missing_evidence")
            or isolation_validation.get("missing_evidence")
            or isolation_plan.get("missing_evidence")
            or []
        )
        assurance_status = (
            data.get("assurance_status")
            or isolation_validation.get("assurance_status")
            or isolation_plan.get("assurance_status")
        )
        output_candidates, active_max_results = self._limited_candidates(
            source_candidates
        )
        selected_equipment = self._selected_equipment(data, debug, source_candidates)
        target_mode = (
            data.get("target_mode")
            or debug.get("boundary_target_mode")
            or debug.get("config_target_mode")
        )

        points = []
        for candidate in output_candidates:
            energy_type = candidate.get("energy_type") or []
            if isinstance(energy_type, list):
                energy_type = energy_type[0] if energy_type else None

            reason = (
                candidate.get("reason") or "Ranked deterministic isolation candidate"
            )
            candidate_id = candidate.get("candidate_id")
            source = candidate.get("source_component_tag")
            if candidate_id:
                reason = f"{reason}. Candidate vertex id: {candidate_id}."
            if source:
                reason = f"{reason} Source component: {source}."

            properties = candidate.get("properties", {}) or {}
            points.append(
                {
                    "equipment_id": candidate.get("equipment_tag"),
                    "uuid": str(candidate.get("candidate_id") or ""),
                    "bbox": candidate.get("bbox") or [],
                    "entity_class": properties.get("entity_class")
                    or candidate.get("candidate_label")
                    or "isolation_point",
                    "tag_number": candidate.get("tag_number"),
                    "energy_type": energy_type,
                    "isolation_method": candidate.get("isolation_method"),
                    "reason": reason,
                }
            )

        return {
            "error": False,
            "message": "Completed",
            "total_jobs_processed": 1,
            "debug": {
                **debug,
                "final_output_code_version": FINAL_OUTPUT_CODE_VERSION,
                "final_input_candidate_count": len(source_candidates),
                "final_output_point_count": len(points),
                "final_max_results": active_max_results,
                "assurance_status": assurance_status,
                "missing_evidence_count": len(missing_evidence),
                "isolation_plan_step_count": len(
                    isolation_plan.get("ordered_steps") or []
                ),
                "selected_equipment": selected_equipment,
                "target_mode": target_mode,
                "bbox_resolved_count": debug.get("bbox_resolved_count"),
                "bbox_job_graph_resolved_count": debug.get(
                    "bbox_job_graph_resolved_count"
                ),
                "bbox_token_resolved_count": debug.get("bbox_token_resolved_count"),
                "bbox_position_resolved_count": debug.get(
                    "bbox_position_resolved_count"
                ),
                "bbox_transform_resolved_count": debug.get(
                    "bbox_transform_resolved_count"
                ),
                "bbox_manual_transform_resolved_count": debug.get(
                    "bbox_manual_transform_resolved_count"
                ),
                "bbox_rejected_count": debug.get("bbox_rejected_count"),
                "bbox_rejected_samples": debug.get("bbox_rejected_samples"),
                "tag_number_resolved_count": debug.get("tag_number_resolved_count"),
                "tag_number_match_samples": debug.get("tag_number_match_samples"),
                "bbox_unresolved_candidate_ids": debug.get(
                    "bbox_unresolved_candidate_ids"
                ),
                "bbox_image_size": debug.get("bbox_image_size"),
                "bbox_hilt_node_count": debug.get("bbox_hilt_node_count"),
                "bbox_job_graph_node_count": debug.get("bbox_job_graph_node_count"),
                "bbox_job_graph_error": debug.get("bbox_job_graph_error"),
                "bbox_job_graph_context": debug.get("bbox_job_graph_context"),
                "bbox_transform": debug.get("bbox_transform"),
                "bbox_transform_control_points": debug.get(
                    "bbox_transform_control_points"
                ),
                "bbox_match_samples": debug.get("bbox_match_samples"),
                "bbox_error": debug.get("bbox_error"),
            },
            "data": [
                {
                    "job_id": self._int_or_zero(
                        self._context_value(context, "job_id", self.job_id)
                    ),
                    "job_name": self._context_value(context, "job_name", self.job_name),
                    "project_id": self._int_or_zero(
                        self._context_value(context, "project_id", self.project_id)
                    ),
                    "project_name": self._context_value(
                        context, "project_name", self.project_name
                    ),
                    "collection_id": self._int_or_zero(
                        self._context_value(
                            context, "collection_id", self.collection_id
                        )
                    ),
                    "collection_name": self._context_value(
                        context, "collection_name", self.collection_name
                    ),
                    "selected_equipment": selected_equipment,
                    "input_details": {
                        "selected_equipment": selected_equipment,
                        "target_mode": target_mode,
                        "job_id": self._int_or_zero(
                            self._context_value(context, "job_id", self.job_id)
                        ),
                        "job_name": self._context_value(
                            context, "job_name", self.job_name
                        ),
                        "project_id": self._int_or_zero(
                            self._context_value(context, "project_id", self.project_id)
                        ),
                        "collection_id": self._int_or_zero(
                            self._context_value(
                                context, "collection_id", self.collection_id
                            )
                        ),
                        "collection_name": self._context_value(
                            context, "collection_name", self.collection_name
                        ),
                    },
                    "assurance_status": assurance_status,
                    "isolation_validation": isolation_validation,
                    "isolation_plan": isolation_plan,
                    "missing_evidence": missing_evidence,
                    "isolation_points": points,
                }
            ],
        }

    def build_final_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_final_message(self) -> Message:
        return Message(
            text="Final isolation UI payload:\n"
            + json.dumps(self._build_payload(), indent=2)
        )
