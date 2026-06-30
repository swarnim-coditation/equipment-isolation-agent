from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Message


class BoundaryDataSummary(Component):
    display_name = "Boundary Data Summary"
    description = "Formats equipment boundary data for readable debugging"
    icon = "message-square"
    name = "BoundaryDataSummary"

    inputs = [
        DataInput(
            name="boundary_data",
            display_name="Equipment Boundary Data",
        ),
    ]

    outputs = [
        Output(
            display_name="Summary",
            name="summary",
            method="build_summary",
        ),
    ]

    def build_summary(self) -> Message:
        data = (
            self.boundary_data.value
            if hasattr(self.boundary_data, "value")
            else self.boundary_data
        )

        if data.get("error"):
            return Message(text=f"Error: {data.get('message')}")

        lines = [
            f"Target mode: {data.get('target_mode')}",
            f"Requested tags: {', '.join(data.get('requested_equipment_tags', []))}",
            f"Matched equipment: {data.get('matched_equipment_count')}",
            f"Max traversal depth: {data.get('max_traversal_depth')}",
            "",
        ]

        for item in data.get("equipment_boundaries", []):
            eq = item.get("equipment", {})
            props = eq.get("properties", {})

            tag = (
                props.get("tag")
                or props.get("tag_number")
                or props.get("Equipment Name")
                or props.get("name")
                or str(eq.get("id"))
            )

            lines.extend(
                [
                    f"Equipment: {tag}",
                    f"  id: {eq.get('id')}",
                    f"  class: {props.get('entity_class')}",
                    f"  type: {props.get('entity_type') or props.get('type')}",
                    f"  edge labels: {', '.join(item.get('edge_labels', []))}",
                    f"  components: {len(item.get('components', []))}",
                    f"  direct neighbors: {len(item.get('direct_neighbors', []))}",
                    "",
                    "  Components:",
                ]
            )

            for component in item.get("components", [])[:20]:
                cprops = component.get("properties", {})
                ctag = (
                    cprops.get("tag")
                    or cprops.get("tag_number")
                    or cprops.get("name")
                    or str(component.get("id"))
                )
                lines.append(
                    f"    - {ctag} | {cprops.get('entity_class')} | {cprops.get('entity_type') or cprops.get('type')}"
                )

            if len(item.get("components", [])) > 20:
                lines.append(f"    ... {len(item.get('components', [])) - 20} more")

            lines.append("")
            lines.append("  Component boundary samples:")

            for boundary in item.get("component_boundaries", [])[:5]:
                comp = boundary.get("component", {})
                cprops = comp.get("properties", {})
                ctag = cprops.get("tag") or cprops.get("name") or str(comp.get("id"))

                lines.append(f"    Component: {ctag}")
                lines.append(
                    f"      edge labels: {', '.join(boundary.get('edge_labels', []))}"
                )
                lines.append(
                    f"      direct neighbors: {len(boundary.get('direct_neighbors', []))}"
                )
                lines.append(
                    f"      traversal sample: {len(boundary.get('traversal_sample', []))}"
                )

        return Message(text="\n".join(lines))
