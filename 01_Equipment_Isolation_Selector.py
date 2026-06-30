from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.traversal import T

from langflow.custom import Component
from langflow.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output
from langflow.schema import Data


JOB_IDS_BY_NAME = {
    "pnid_1_bio_final": "2099",
    "pnid_2_bio_final": "2100",
    "pnid_3_bio_final": "2102",
    "pnid_5_bio_final": "2103",
    "pnid_7_bio_final": "2104",
    "pnid_4_bio_final": "2105",
    "pnid_6_bio_final": "2106",
}


class EquipmentIsolationSelector(Component):
    display_name = "Equipment Isolation Selector"
    description = "Selects target equipment and builds graph/job context for isolation"
    icon = "crosshair"
    name = "EquipmentIsolationSelector"

    inputs = [
        MessageTextInput(
            name="host",
            display_name="JanusGraph Host",
            value="44.217.77.13",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="port",
            display_name="JanusGraph Port",
            value="8182",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="project_id",
            display_name="Project ID",
            value="274",
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="collection_id",
            display_name="Collection ID",
            value="196",
        ),
        MessageTextInput(
            name="collection_name",
            display_name="Collection Name",
            value="Unit",
        ),
        BoolInput(
            name="refresh_equipment_list",
            display_name="Refresh Equipment List",
            info="Toggle after changing connection fields to reload equipment options",
            value=False,
            real_time_refresh=True,
        ),
        IntInput(
            name="max_equipment_options",
            display_name="Max Equipment Options",
            info="Maximum number of Equipment vertices to fetch for the dropdown",
            value=500,
            real_time_refresh=True,
        ),
        DropdownInput(
            name="equipment_tag",
            display_name="Equipment To Isolate",
            info="Equipment tag fetched from Unigraph Equipment vertices",
            options=[],
            value="",
        ),
        MessageTextInput(
            name="target_mode",
            display_name="Target Mode",
            info="Use 'selected_equipment' or 'all_equipment'",
            value="selected_equipment",
        ),
    ]

    outputs = [
        Output(display_name="Selector Data", name="selector_data", method="build_selector"),
    ]

    def _graph_url(self):
        return f"ws://{str(self.host).strip()}:{str(self.port).strip()}/gremlin"

    def _traversal_source(self):
        return f"graph{str(self.project_id).strip()}_traversal"

    def _normalize_vertex(self, vertex):
        normalized = {}
        for key, value in vertex.items():
            if key in [T.id, T.label]:
                normalized[str(key)] = value
            elif isinstance(value, list) and len(value) == 1:
                normalized[str(key)] = value[0]
            else:
                normalized[str(key)] = value
        return normalized

    def _equipment_display_tag(self, vertex):
        for key in ("tag", "tag_number", "Equipment Name", "name"):
            value = vertex.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return str(vertex.get(str(T.id)) or vertex.get("id") or "").strip()

    def _equipment_unit_name(self, vertex):
        for key in ("unit_name", "unit", "drawing_name", "pid_name"):
            value = vertex.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _equipment_option(self, vertex):
        tag = self._equipment_display_tag(vertex)
        name = str(vertex.get("Equipment Name") or vertex.get("name") or "").strip()
        entity_class = str(vertex.get("entity_class") or vertex.get("class") or "").strip()
        unit_name = self._equipment_unit_name(vertex)
        graph_id = str(vertex.get(str(T.id)) or vertex.get("id") or "").strip()

        details = []
        if name and name != tag:
            details.append(name)
        if entity_class:
            details.append(entity_class)
        if unit_name:
            details.append(unit_name)
        if graph_id:
            details.append(f"id:{graph_id}")
        return tag if not details else f"{tag} | {' | '.join(details)}"

    def _selected_equipment_tag(self):
        return str(self.equipment_tag or "").split("|", 1)[0].strip()

    def _selected_unit_name(self):
        for part in str(self.equipment_tag or "").split("|")[1:]:
            value = part.strip()
            if value in JOB_IDS_BY_NAME:
                return value
        return ""

    def _max_equipment_options(self):
        try:
            max_options = int(self.max_equipment_options)
        except Exception:
            max_options = 500
        return max_options if max_options > 0 else 500

    def _fetch_equipment_tags(self):
        connection = None
        try:
            connection = DriverRemoteConnection(self._graph_url(), self._traversal_source())
            g = traversal().withRemote(connection)
            rows = (
                g.V()
                .hasLabel("Equipment")
                .valueMap(True)
                .limit(self._max_equipment_options())
                .toList()
            )
            options = []
            seen = set()
            for row in rows:
                vertex = self._normalize_vertex(row)
                tag = self._equipment_display_tag(vertex)
                if not tag or tag in seen:
                    continue
                seen.add(tag)
                options.append(self._equipment_option(vertex))
            return sorted(options)
        finally:
            if connection:
                connection.close()

    def _job_name_from_vertex(self, vertex):
        for key in ("unit_name", "unit", "drawing_name", "pid_name"):
            value = vertex.get(key)
            if value not in (None, ""):
                return str(value).strip()
        parent_pnsg = str(vertex.get("parent_pnsg") or "").strip()
        if "-pns" in parent_pnsg:
            return parent_pnsg.split("-pns", 1)[0]
        return ""

    def _infer_selected_job_name(self, equipment_tag):
        if not equipment_tag:
            return ""

        connection = None
        try:
            connection = DriverRemoteConnection(self._graph_url(), self._traversal_source())
            g = traversal().withRemote(connection)
            rows = (
                g.V()
                .hasLabel("Equipment")
                .has("tag", equipment_tag)
                .repeat(__.both().simplePath())
                .times(3)
                .emit()
                .dedup()
                .valueMap(True)
                .limit(200)
                .toList()
            )

            counts = {}
            for row in rows:
                vertex = self._normalize_vertex(row)
                job_name = self._job_name_from_vertex(vertex)
                if job_name:
                    counts[job_name] = counts.get(job_name, 0) + 1
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        except Exception:
            return ""
        finally:
            if connection:
                connection.close()

    def update_build_config(self, build_config, field_value, field_name=None):
        if field_name in {
            None,
            "host",
            "port",
            "project_id",
            "refresh_equipment_list",
            "max_equipment_options",
        }:
            try:
                tags = self._fetch_equipment_tags()
                build_config["equipment_tag"]["options"] = tags
                if tags:
                    build_config["equipment_tag"]["info"] = (
                        f"Fetched {len(tags)} equipment option(s) from Unigraph"
                    )
                else:
                    build_config["equipment_tag"]["info"] = (
                        "No Equipment vertices were returned for the current connection/project"
                    )
                current_value = build_config["equipment_tag"].get("value")
                if tags and current_value not in tags:
                    build_config["equipment_tag"]["value"] = tags[0]
            except Exception as exc:
                build_config["equipment_tag"]["options"] = []
                build_config["equipment_tag"]["info"] = f"Failed to fetch equipment list: {exc}"
        return build_config

    def build_selector(self) -> Data:
        target_mode = str(self.target_mode or "selected_equipment").strip()
        if target_mode not in {"selected_equipment", "all_equipment"}:
            target_mode = "selected_equipment"

        equipment_tags = [] if target_mode == "all_equipment" else [self._selected_equipment_tag()]
        context = {
            "project_id": str(self.project_id).strip(),
            "collection_id": str(self.collection_id).strip(),
            "collection_name": str(self.collection_name).strip(),
        }

        selected_unit_name = self._selected_unit_name() or self._infer_selected_job_name(
            equipment_tags[0] if equipment_tags else ""
        )
        if selected_unit_name:
            context["job_name"] = selected_unit_name
            context["job_id"] = JOB_IDS_BY_NAME.get(selected_unit_name)

        graph = {
            "gremlin_server_url": self._graph_url(),
            "traversal_source": self._traversal_source(),
            "host": str(self.host).strip(),
            "port": str(self.port).strip(),
            "project_id": str(self.project_id).strip(),
        }

        return Data(
            value={
                "target_mode": target_mode,
                "equipment_tags": [tag for tag in equipment_tags if tag],
                "context": context,
                "graph": graph,
                # Backward-compatible top-level graph fields for existing nodes.
                **graph,
            }
        )
