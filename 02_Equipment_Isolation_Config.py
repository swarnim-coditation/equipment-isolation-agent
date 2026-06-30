from langflow.custom import Component
from langflow.io import DataInput, MessageTextInput, Output, BoolInput, IntInput
from langflow.schema import Data
import logging

logger = logging.getLogger(__name__)


class EquipmentIsolationConfigNode(Component):
    display_name = "Equipment Isolation Config"
    description = "Configuration for equipment isolation graph traversal"
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "settings"
    name = "EquipmentIsolationConfigNode"

    inputs = [
        MessageTextInput(
            name="target_mode",
            display_name="Target Mode",
            info="Use 'selected_equipment' or 'all_equipment'",
            value="selected_equipment",
        ),
        MessageTextInput(
            name="equipment_tags",
            display_name="Equipment Tags",
            info="Comma-separated equipment tags, e.g. P-08,VE-01. Ignored if target mode is all_equipment.",
            value="P-08",
        ),
        DataInput(
            name="request_context",
            display_name="Request/API Context",
            info="Optional webhook/API payload containing job_id, project_id, collection_id, names, or selected equipment.",
            required=False,
        ),
        IntInput(
            name="max_traversal_depth",
            display_name="Max Traversal Depth",
            info="How far to traverse from equipment/nozzles when searching for isolation points",
            value=3,
        ),
        BoolInput(
            name="include_utilities",
            display_name="Include Utilities",
            info="Include utility connections such as steam, water, air, nitrogen if present",
            value=True,
        ),
        BoolInput(
            name="include_drains_vents",
            display_name="Include Drains and Vents",
            info="Include drain/vent connections if present",
            value=True,
        ),
        BoolInput(
            name="prefer_positive_isolation",
            display_name="Prefer Positive Isolation",
            info="Recommend blinds/spades/physical separation for hazardous services where possible",
            value=True,
        ),
        MessageTextInput(
            name="isolation_device_keywords",
            display_name="Isolation Device Keywords",
            info="Comma-separated keywords used to identify isolation candidates",
            value="valve,gate_valve,ball_valve,globe_valve,check_valve,control_valve,blind,spade,flange,breaker,disconnect",
        ),
        MessageTextInput(
            name="excluded_classes",
            display_name="Excluded Classes",
            info="Comma-separated classes that should not be returned as isolation points",
            value="equipment,pump,tank,vessel,line,pipe",
        ),
        MessageTextInput(
            name="conditional_classes",
            display_name="Conditional Classes",
            info="Classes that require explicit policy permission before being returned",
            value="check_valve,control_valve",
        ),
        BoolInput(
            name="include_conditional_candidates",
            display_name="Include Conditional Candidates",
            info="Allow check/control valves as final isolation candidates",
            value=False,
        ),
        MessageTextInput(
            name="hazardous_service_keywords",
            display_name="Hazardous Service Keywords",
            info="Comma-separated service keywords that should increase isolation strictness",
            value="HC,H2S,acid,caustic,steam,fuel,methanol,hydrogen,flammable,toxic,hot",
        ),
    ]

    outputs = [
        Output(
            display_name="Isolation Config",
            name="config",
            method="build_config",
        ),
    ]

    def _csv(self, value: str):
        return [item.strip() for item in value.split(",") if item.strip()]

    def _unwrap(self, value):
        if hasattr(value, "value") and value.value is not None:
            return value.value
        if hasattr(value, "data") and value.data is not None:
            return value.data
        return value

    def _first(self, data, *keys):
        if not isinstance(data, dict):
            return None
        for key in keys:
            value = data.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _context_from_request(self):
        data = self._unwrap(getattr(self, "request_context", None)) or {}
        if not isinstance(data, dict):
            return {}

        nested = self._first(data, "context", "payload", "data")
        if isinstance(nested, dict):
            data = {**nested, **data}

        return {
            "job_id": self._first(data, "job_id", "jobId", "pid_id", "pidId", "p_id"),
            "job_name": self._first(data, "job_name", "jobName", "pid_name", "pidName", "name"),
            "project_id": self._first(data, "project_id", "projectId", "project"),
            "project_name": self._first(data, "project_name", "projectName"),
            "collection_id": self._first(data, "collection_id", "collectionId"),
            "collection_name": self._first(data, "collection_name", "collectionName"),
            "equipment_tags": self._first(data, "equipment_tags", "equipmentTags", "selected_equipment", "selectedEquipment", "tag", "tag_number"),
        }

    def _context_equipment_tags(self, value):
        if isinstance(value, list):
            tags = []
            for item in value:
                if isinstance(item, dict):
                    tag = self._first(item, "tag", "tag_number", "equipment_tag", "equipmentTag", "name")
                    if tag:
                        tags.append(str(tag))
                elif item not in (None, ""):
                    tags.append(str(item))
            return tags
        if value not in (None, ""):
            return self._csv(str(value))
        return []

    def build_config(self) -> Data:
        try:
            target_mode = self.target_mode.strip()
            request_context = self._context_from_request()

            if target_mode not in ["selected_equipment", "all_equipment"]:
                target_mode = "selected_equipment"

            context_tags = self._context_equipment_tags(request_context.get("equipment_tags"))

            config_data = {
                "target_mode": target_mode,
                "equipment_tags": context_tags or self._csv(self.equipment_tags),
                "max_traversal_depth": int(self.max_traversal_depth),
                "include_utilities": bool(self.include_utilities),
                "include_drains_vents": bool(self.include_drains_vents),
                "prefer_positive_isolation": bool(self.prefer_positive_isolation),
                "isolation_device_keywords": self._csv(self.isolation_device_keywords),
                "eligible_classes": self._csv(self.isolation_device_keywords),
                "excluded_classes": self._csv(self.excluded_classes),
                "conditional_classes": self._csv(self.conditional_classes),
                "include_conditional_candidates": bool(self.include_conditional_candidates),
                "hazardous_service_keywords": self._csv(self.hazardous_service_keywords),
                "equipment_labels": ["Equipment"],
                "component_labels": ["Component"],
                "line_labels": ["PNS", "PNSG"],
                "candidate_edge_labels": [
                    "PHYSICALLY_HAS_A",
                    "PHYSICALLY_CONNECTED_TO",
                    "ASSOCIATED_WITH",
                ],
                "context": {key: value for key, value in request_context.items() if value not in (None, "", [])},
            }

            logger.info("Equipment isolation config created: %s", config_data)

            return Data(value=config_data)

        except Exception as e:
            logger.exception("Failed to create equipment isolation config")

            return Data(
                value={
                    "error": True,
                    "message": f"Equipment isolation config failed: {str(e)}",
                }
            )
