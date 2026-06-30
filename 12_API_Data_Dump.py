import json

import httpx
from pydantic.v1 import SecretStr

from langflow.custom import Component
from langflow.io import BoolInput, DataInput, IntInput, MessageTextInput, Output, SecretStrInput
from langflow.schema import Data, Message


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _norm(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _number(value):
    try:
        return float(value)
    except Exception:
        return None


class IsolationAPIDataDump(Component):
    display_name = "Isolation API Data Dump"
    description = "Dumps candidate, HILT, and job API data needed to debug bbox matching"
    icon = "database-zap"
    name = "IsolationAPIDataDump"

    inputs = [
        DataInput(name="candidate_data", display_name="Isolation Candidates"),
        MessageTextInput(name="api_base_url", display_name="API Base URL", value="https://api.plant360.ai:8080"),
        SecretStrInput(name="auth_token", display_name="Authentication Token", required=True, value=""),
        MessageTextInput(name="job_id", display_name="P&ID Job ID", value="2100"),
        MessageTextInput(name="project_id", display_name="Project ID", value="274"),
        MessageTextInput(name="collection_id", display_name="Collection ID", value="196"),
        IntInput(name="sample_limit", display_name="Sample Limit", value=20),
        BoolInput(name="verify_ssl", display_name="Verify SSL", value=False),
    ]

    outputs = [
        Output(display_name="Dump Data", name="dump_data", method="build_data"),
        Output(display_name="Dump Summary", name="dump_summary", method="build_summary"),
    ]

    def _headers(self):
        token = SecretStr(self.auth_token).get_secret_value()
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _get(self, client, path):
        url = f"{self.api_base_url.rstrip('/')}/{path.lstrip('/')}"
        response = client.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def _candidate_tokens(self, candidate):
        props = candidate.get("properties", {}) or {}
        values = [
            candidate.get("candidate_id"),
            candidate.get("tag_number"),
            candidate.get("candidate_label"),
            props.get("node_id"),
            props.get("tag"),
            props.get("tag_number"),
            props.get("name"),
            props.get("parent_pns"),
            props.get("parent_pnsg"),
            props.get("pnsgid"),
            props.get("unit_name"),
        ]
        return {token for token in (_norm(value) for value in values) if token}

    def _hilt_tokens(self, node):
        payload = node.get("payload", {}) or {}
        values = [node.get("id"), payload.get("id"), payload.get("node_id"), payload.get("tag"), payload.get("tag_number"), payload.get("label"), payload.get("name")]
        for attr in payload.get("attributes", []) or []:
            if isinstance(attr, dict):
                values.extend([attr.get("name"), attr.get("value")])
        return {token for token in (_norm(value) for value in values) if token}

    def _center_from_candidate(self, candidate):
        props = candidate.get("properties", {}) or {}
        x = _number(props.get("x_pos") or props.get("x"))
        y = _number(props.get("y_pos") or props.get("y"))
        return [x, y] if x is not None and y is not None else None

    def _center_from_hilt(self, node):
        payload = node.get("payload", {}) or {}
        location = payload.get("bounding_box_location") or {}
        x = _number(location.get("x"))
        y = _number(location.get("y"))
        return [x, y] if x is not None and y is not None else None

    def _entity_class(self, node):
        payload = node.get("payload", {}) or {}
        value = payload.get("entity_class") or payload.get("entityClass")
        if value:
            return str(value)
        for attr in payload.get("attributes", []) or []:
            if isinstance(attr, dict) and str(attr.get("name") or "").lower() in {"entity_class", "entityclass", "class"}:
                return str(attr.get("value") or "")
        return ""

    def _bbox_from_hilt(self, node, image_height):
        payload = node.get("payload", {}) or {}
        center = self._center_from_hilt(node)
        width = _number(payload.get("bounding_box_width"))
        height = _number(payload.get("bounding_box_height"))
        if not center or not width or not height or not image_height:
            return []
        return [int(center[0] - width / 2), int(float(image_height) - (center[1] + height / 2)), int(width), int(height)]

    def _nearest_hilt(self, candidate, hilt_nodes, limit=5):
        center = self._center_from_candidate(candidate)
        if not center:
            return []
        cx, cy = center
        matches = []
        for node in hilt_nodes:
            hcenter = self._center_from_hilt(node)
            if not hcenter:
                continue
            hx, hy = hcenter
            distance = ((cx - hx) ** 2 + (cy - hy) ** 2) ** 0.5
            matches.append((distance, node, hcenter))
        matches.sort(key=lambda item: item[0])
        return [
            {
                "distance": distance,
                "hilt_id": node.get("id"),
                "entity_class": self._entity_class(node),
                "center": hcenter,
                "tokens": sorted(list(self._hilt_tokens(node)))[:12],
            }
            for distance, node, hcenter in matches[:limit]
        ]

    def _build_payload(self):
        candidate_data = _unwrap_data(self.candidate_data) or {}
        candidates = candidate_data.get("candidates", []) or []
        job_id = str(self.job_id).strip()
        project_id = str(self.project_id).strip()
        collection_id = str(self.collection_id).strip()
        sample_limit = int(self.sample_limit or 20)

        payload = {
            "error": False,
            "job_id": job_id,
            "project_id": project_id,
            "collection_id": collection_id,
            "candidate_count": len(candidates),
            "candidate_context": candidate_data.get("context") or {},
            "candidate_samples": [],
            "hilt": {},
            "job_details": None,
            "nested_job_graph": {},
            "endpoint_errors": [],
        }

        with httpx.Client(verify=bool(self.verify_ssl), timeout=60) as client:
            try:
                hilt_body = self._get(client, f"jobs/get_job_hilt_graph/{job_id}")
                hilt_graph = hilt_body.get("hilt_graph") if isinstance(hilt_body, dict) else None
                if not isinstance(hilt_graph, dict):
                    hilt_graph = hilt_body if isinstance(hilt_body, dict) else {}
                hilt_nodes = hilt_graph.get("nodes", []) or []
                image_size = hilt_graph.get("imageSize", {}) or {}
                image_height = image_size.get("height")
                payload["hilt"] = {
                    "image_size": image_size,
                    "node_count": len(hilt_nodes),
                    "entity_class_counts": self._counts(self._entity_class(node) for node in hilt_nodes),
                    "node_samples": [self._hilt_sample(node, image_height) for node in hilt_nodes[:sample_limit]],
                }
            except Exception as exc:
                hilt_nodes = []
                payload["endpoint_errors"].append({"endpoint": f"/jobs/get_job_hilt_graph/{job_id}", "error": str(exc)})

            try:
                payload["job_details"] = self._get(client, f"jobs/get_job_details/{job_id}")
            except Exception as exc:
                payload["endpoint_errors"].append({"endpoint": f"/jobs/get_job_details/{job_id}", "error": str(exc)})

            try:
                graph_body = self._get(client, f"projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph")
                pid_graph = graph_body.get("pid_graph") if isinstance(graph_body, dict) else None
                graph_nodes = pid_graph.get("nodes", []) if isinstance(pid_graph, dict) else []
                payload["nested_job_graph"] = {"node_count": len(graph_nodes), "node_samples": graph_nodes[:sample_limit]}
            except Exception as exc:
                payload["endpoint_errors"].append({"endpoint": f"/projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph", "error": str(exc)})

        hilt_token_index = {}
        for node in hilt_nodes:
            for token in self._hilt_tokens(node):
                hilt_token_index.setdefault(token, []).append(node)

        for candidate in candidates[:sample_limit]:
            tokens = self._candidate_tokens(candidate)
            exact = []
            for token in tokens:
                for node in hilt_token_index.get(token, [])[:5]:
                    exact.append({"token": token, "hilt_id": node.get("id"), "entity_class": self._entity_class(node)})
            props = candidate.get("properties", {}) or {}
            payload["candidate_samples"].append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "equipment_tag": candidate.get("equipment_tag"),
                    "source_component_tag": candidate.get("source_component_tag"),
                    "candidate_label": candidate.get("candidate_label"),
                    "tag_number": candidate.get("tag_number"),
                    "entity_class": props.get("entity_class"),
                    "unit_name": props.get("unit_name"),
                    "parent_pns": props.get("parent_pns"),
                    "parent_pnsg": props.get("parent_pnsg"),
                    "node_id": props.get("node_id"),
                    "name": props.get("name"),
                    "tag": props.get("tag"),
                    "center": self._center_from_candidate(candidate),
                    "tokens": sorted(list(tokens)),
                    "exact_hilt_token_matches": exact[:10],
                    "nearest_hilt": self._nearest_hilt(candidate, hilt_nodes),
                }
            )

        return payload

    def _counts(self, values):
        counts = {}
        for value in values:
            key = value or "(blank)"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:30])

    def _hilt_sample(self, node, image_height):
        payload = node.get("payload", {}) or {}
        attrs = payload.get("attributes", []) or []
        return {
            "id": node.get("id"),
            "entity_class": self._entity_class(node),
            "center": self._center_from_hilt(node),
            "bbox": self._bbox_from_hilt(node, image_height),
            "width": payload.get("bounding_box_width"),
            "height": payload.get("bounding_box_height"),
            "direct_fields": {key: payload.get(key) for key in ("id", "node_id", "tag", "tag_number", "label", "name") if payload.get(key)},
            "attributes": attrs[:8],
            "tokens": sorted(list(self._hilt_tokens(node)))[:12],
        }

    def build_data(self) -> Data:
        try:
            return Data(value=self._build_payload())
        except Exception as exc:
            return Data(value={"error": True, "message": str(exc)})

    def build_summary(self) -> Message:
        data = self._build_payload()
        return Message(text="Isolation API Data Dump:\n" + json.dumps(data, indent=2))
