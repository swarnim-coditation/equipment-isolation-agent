from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.traversal import T, P
from gremlin_python.process.graph_traversal import __
import logging

logger = logging.getLogger(__name__)


class EquipmentBoundaryFetcher(Component):
    display_name = "Equipment Boundary Fetcher"
    description = "Fetches target equipment, attached components, and nearby graph boundary data"
    documentation: str = "https://docs.langflow.org/components-custom-components"
    icon = "network"
    name = "EquipmentBoundaryFetcher"

    inputs = [
        DataInput(
            name="selector_data",
            display_name="Equipment Selector Data",
            info="Selected equipment, graph connection, and job context",
        ),
        DataInput(
            name="policy_data",
            display_name="Isolation Policy Data",
            info="Traversal and isolation-candidate policy settings",
        ),
    ]

    outputs = [
        Output(
            display_name="Equipment Boundary Data",
            name="boundary_data",
            method="fetch_boundary_data",
        ),
    ]

    def _unwrap(self, data):
        return data.value if hasattr(data, "value") else data

    def _normalize_vertex(self, vertex):
        normalized = {}
        for k, v in vertex.items():
            if k in [T.id, T.label]:
                normalized[str(k)] = v
            elif isinstance(v, list) and len(v) == 1:
                normalized[str(k)] = v[0]
            else:
                normalized[str(k)] = v
        return normalized

    def _vertex_id(self, vertex):
        for key in [T.id, "id", "T.id"]:
            if key in vertex:
                return vertex[key]
        return None

    def _vertex_label(self, vertex):
        for key in [T.label, "label", "T.label"]:
            if key in vertex:
                return vertex[key]
        return None

    def _props_only(self, vertex):
        excluded = {str(T.id), str(T.label), "id", "label", "T.id", "T.label"}
        return {k: v for k, v in vertex.items() if k not in excluded and v is not None}

    def _safe_value_map(self, g, vertex_id):
        try:
            rows = g.V(vertex_id).valueMap(True).toList()
            if not rows:
                return None
            return self._normalize_vertex(rows[0])
        except Exception as exc:
            logger.warning("Failed to fetch vertex map for %s: %s", vertex_id, exc)
            return None

    def fetch_boundary_data(self) -> Data:
        connection = None

        try:
            selector_data = self._unwrap(self.selector_data)
            policy_data = self._unwrap(self.policy_data)

            if not selector_data or selector_data.get("error"):
                raise ValueError(f"Invalid selector data: {selector_data}")

            if not policy_data or policy_data.get("error"):
                raise ValueError(f"Invalid policy data: {policy_data}")

            graph_config = selector_data.get("graph") or selector_data
            url = graph_config["gremlin_server_url"]
            source = graph_config["traversal_source"]

            target_mode = selector_data.get("target_mode", "selected_equipment")
            equipment_tags = selector_data.get("equipment_tags", [])
            max_depth = int(policy_data.get("max_traversal_depth", 3))
            context = dict(policy_data.get("context") or {})
            context.update(selector_data.get("context") or {})
            if graph_config.get("project_id") and not context.get("project_id"):
                context["project_id"] = graph_config.get("project_id")

            connection = DriverRemoteConnection(url, source)
            g = traversal().withRemote(connection)

            logger.info("Fetching equipment boundaries from %s / %s", url, source)

            if target_mode == "all_equipment":
                equipment_vertices = g.V().hasLabel("Equipment").valueMap(True).toList()
            else:
                equipment_vertices = []
                for tag in equipment_tags:
                    tag = tag.strip()
                    if not tag:
                        continue

                    # Match common tag fields. Keep exact match first for predictable behavior.
                    exact_matches = (
                        g.V()
                        .hasLabel("Equipment")
                        .or_(
                            __.has("tag", tag),
                            __.has("tag_number", tag),
                            __.has("Equipment Name", tag),
                            __.has("name", tag),
                        )
                        .valueMap(True)
                        .toList()
                    )

                    equipment_vertices.extend(exact_matches)

            equipment_vertices = [self._normalize_vertex(v) for v in equipment_vertices]

            # Deduplicate equipment by vertex id.
            seen_equipment_ids = set()
            unique_equipment = []
            for eq in equipment_vertices:
                eq_id = self._vertex_id(eq)
                if eq_id in seen_equipment_ids:
                    continue
                seen_equipment_ids.add(eq_id)
                unique_equipment.append(eq)

            equipment_results = []

            for equipment in unique_equipment:
                equipment_id = self._vertex_id(equipment)
                equipment_props = self._props_only(equipment)

                logger.info(
                    "Processing equipment %s (%s)",
                    equipment_props.get("tag") or equipment_props.get("tag_number") or equipment_id,
                    equipment_id,
                )

                # Direct components/nozzles physically owned by equipment.
                component_rows = (
                    g.V(equipment_id)
                    .out("PHYSICALLY_HAS_A")
                    .hasLabel("Component")
                    .valueMap(True)
                    .toList()
                )
                components = [self._normalize_vertex(v) for v in component_rows]

                # Direct graph neighbors around equipment.
                neighbor_rows = (
                    g.V(equipment_id)
                    .both()
                    .dedup()
                    .limit(100)
                    .valueMap(True)
                    .toList()
                )
                direct_neighbors = [self._normalize_vertex(v) for v in neighbor_rows]

                # Edge labels touching equipment.
                edge_labels = (
                    g.V(equipment_id)
                    .bothE()
                    .label()
                    .dedup()
                    .toList()
                )

                component_boundaries = []

                for component in components:
                    component_id = self._vertex_id(component)

                    component_edge_labels = (
                        g.V(component_id)
                        .bothE()
                        .label()
                        .dedup()
                        .toList()
                    )

                    component_neighbors = (
                        g.V(component_id)
                        .both()
                        .dedup()
                        .limit(100)
                        .valueMap(True)
                        .toList()
                    )
                    component_neighbors = [
                        self._normalize_vertex(v) for v in component_neighbors
                    ]

                    # Collect each depth separately so candidate ranking can prefer nearby valves.
                    traversal_by_id = {}
                    for depth in range(1, max_depth + 1):
                        traversal_rows = (
                            g.V(component_id)
                            .repeat(__.both().simplePath())
                            .times(depth)
                            .dedup()
                            .limit(200)
                            .valueMap(True)
                            .toList()
                        )

                        for row in traversal_rows:
                            vertex = self._normalize_vertex(row)
                            vertex_id = self._vertex_id(vertex)
                            if vertex_id in traversal_by_id:
                                continue
                            traversal_by_id[vertex_id] = {
                                "id": vertex_id,
                                "label": self._vertex_label(vertex),
                                "properties": self._props_only(vertex),
                                "traversal_depth": depth,
                            }

                    component_boundaries.append(
                        {
                            "component": {
                                "id": component_id,
                                "label": self._vertex_label(component),
                                "properties": self._props_only(component),
                            },
                            "edge_labels": component_edge_labels,
                            "direct_neighbors": [
                                {
                                    "id": self._vertex_id(v),
                                    "label": self._vertex_label(v),
                                    "properties": self._props_only(v),
                                    "traversal_depth": 1,
                                }
                                for v in component_neighbors
                            ],
                            "traversal_sample": list(traversal_by_id.values())[:200],
                        }
                    )

                equipment_results.append(
                    {
                        "equipment": {
                            "id": equipment_id,
                            "label": self._vertex_label(equipment),
                            "properties": equipment_props,
                        },
                        "edge_labels": edge_labels,
                        "components": [
                            {
                                "id": self._vertex_id(v),
                                "label": self._vertex_label(v),
                                "properties": self._props_only(v),
                            }
                            for v in components
                        ],
                        "direct_neighbors": [
                            {
                                "id": self._vertex_id(v),
                                "label": self._vertex_label(v),
                                "properties": self._props_only(v),
                            }
                            for v in direct_neighbors
                        ],
                        "component_boundaries": component_boundaries,
                    }
                )

            result = {
                "target_mode": target_mode,
                "requested_equipment_tags": equipment_tags,
                "matched_equipment_count": len(equipment_results),
                "max_traversal_depth": max_depth,
                "equipment_boundaries": equipment_results,
                "context": context,
                "configurations": {
                    "selector": selector_data,
                    "policy": policy_data,
                    "graph": graph_config,
                },
            }

            logger.info(
                "Fetched boundary data for %s equipment item(s)",
                len(equipment_results),
            )

            return Data(value=result)

        except Exception as exc:
            logger.exception("Equipment boundary fetch failed")

            return Data(
                value={
                    "error": True,
                    "message": f"Equipment boundary fetch failed: {str(exc)}",
                }
            )

        finally:
            if connection:
                connection.close()
