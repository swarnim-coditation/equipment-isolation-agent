from langflow.custom import Component
from langflow.io import BoolInput, IntInput, MessageTextInput, Output
from langflow.schema import Data


class IsolationPolicyConfig(Component):
    display_name = "Isolation Policy Config"
    description = "Defines deterministic traversal and isolation-candidate policy"
    icon = "shield-check"
    name = "IsolationPolicyConfig"

    inputs = [
        IntInput(
            name="max_traversal_depth",
            display_name="Max Traversal Depth",
            value=3,
        ),
        BoolInput(
            name="include_utilities",
            display_name="Include Utilities",
            value=True,
        ),
        BoolInput(
            name="include_drains_vents",
            display_name="Include Drains and Vents",
            value=True,
        ),
        BoolInput(
            name="prefer_positive_isolation",
            display_name="Prefer Positive Isolation",
            value=True,
        ),
        MessageTextInput(
            name="isolation_device_keywords",
            display_name="Isolation Device Keywords",
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
            value="HC,H2S,acid,caustic,steam,fuel,methanol,hydrogen,flammable,toxic,hot",
        ),
    ]

    outputs = [
        Output(display_name="Policy Data", name="policy_data", method="build_policy"),
    ]

    def _csv(self, value):
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    def build_policy(self) -> Data:
        isolation_keywords = self._csv(self.isolation_device_keywords)
        return Data(
            value={
                "max_traversal_depth": int(self.max_traversal_depth),
                "include_utilities": bool(self.include_utilities),
                "include_drains_vents": bool(self.include_drains_vents),
                "prefer_positive_isolation": bool(self.prefer_positive_isolation),
                "isolation_device_keywords": isolation_keywords,
                "eligible_classes": isolation_keywords,
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
            }
        )
