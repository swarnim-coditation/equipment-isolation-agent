from langflow.custom import Component
from langflow.io import BoolInput, MultilineInput, Output
from langflow.schema import Data, Message
import json


DEFAULT_PLANT_INSTRUCTIONS = """Use conservative process isolation rules.

Do not claim complete isolation from block valves alone unless every connected path has a verification method such as a bleed, vent, drain, pressure gauge, pressure indicator, or approved test point.

Prefer positive isolation for intrusive work, line breaking, high pressure, toxic, flammable, corrosive, steam, hot service, confined-space entry, or any high-consequence exposure. Positive isolation means physical separation or a physical barrier such as blind flange, blank flange, spade, spectacle blind in closed position, removable spool removed, or disconnection.

Treat check valves and control valves as traversal context, not final isolation barriers, unless the plant instruction explicitly permits them for the specific work scope.

Require residual energy planning. Identify how trapped pressure, residual liquid, vapor, thermal energy, pneumatic energy, hydraulic energy, electrical energy, and mechanical stored energy will be released, restrained, or verified safe.

If graph/P&ID evidence does not show required blinds, bleeds, vents, drains, gauges, or bypass status, return missing evidence and require human review. Do not invent missing devices.

Final procedure order should follow: prepare and identify hazards, notify affected personnel, orderly shutdown, isolate source paths, apply locks/tags, release stored energy, verify zero or safe energy, perform work, then restore in controlled sequence.
"""


class PlantIsolationInstructions(Component):
    display_name = "Plant Isolation Instructions"
    description = "Provides site/work-scope instructions for the LLM isolation planner"
    icon = "clipboard-check"
    name = "PlantIsolationInstructions"

    inputs = [
        MultilineInput(
            name="instructions",
            display_name="Plant / Site Isolation Instructions",
            info="Natural-language rules the LLM planner must follow. These do not override deterministic validation.",
            value=DEFAULT_PLANT_INSTRUCTIONS,
        ),
        BoolInput(
            name="intrusive_work",
            display_name="Intrusive Work / Line Breaking",
            value=True,
        ),
        BoolInput(
            name="confined_space_entry",
            display_name="Confined Space Entry",
            value=False,
        ),
        BoolInput(
            name="hot_work",
            display_name="Hot Work",
            value=False,
        ),
        BoolInput(
            name="high_risk_service",
            display_name="High-Risk Service",
            info="Use when service is high pressure, toxic, flammable, corrosive, steam, hot, or otherwise high consequence.",
            value=True,
        ),
    ]

    outputs = [
        Output(display_name="Instructions Data", name="instructions_data", method="build_data"),
        Output(display_name="Instructions Summary", name="instructions_summary", method="build_summary"),
    ]

    def _payload(self):
        work_scope = {
            "intrusive_work": bool(self.intrusive_work),
            "confined_space_entry": bool(self.confined_space_entry),
            "hot_work": bool(self.hot_work),
            "high_risk_service": bool(self.high_risk_service),
        }
        return {
            "plant_isolation_instructions": str(self.instructions or "").strip(),
            "work_scope": work_scope,
            "llm_instruction_rules": {
                "may_plan_sequence": True,
                "may_request_more_evidence": True,
                "may_classify_isolation_quality": True,
                "must_use_only_supplied_evidence": True,
                "must_not_invent_components_or_ids": True,
                "must_not_override_validator": True,
            },
        }

    def build_data(self) -> Data:
        return Data(value=self._payload())

    def build_summary(self) -> Message:
        return Message(text="Plant isolation instructions:\n" + json.dumps(self._payload(), indent=2))
