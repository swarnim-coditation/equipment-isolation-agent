import httpx
from pydantic.v1 import SecretStr

from langflow.custom import Component
from langflow.io import BoolInput, DataInput, MessageTextInput, Output, SecretStrInput
from langflow.schema import Data, Message
import json


RESOLVER_CODE_VERSION = "07A_visual_resolver_2026-06-24_stlm_first_simplified_v5"
API_TIMEOUT_SECONDS = 20


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _norm(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _number(value):
    try:
        return float(value)
    except Exception:
        return None


def _looks_like_uuid(value):
    text = str(value or "").strip()
    parts = text.split("-")
    return len(parts) == 5 and [len(part) for part in parts] == [8, 4, 4, 4, 12]


def _leading_int_text(value):
    text = str(value or "").strip()
    digits = []
    for char in text:
        if not char.isdigit():
            break
        digits.append(char)
    return "".join(digits)


class IsolationBBoxResolver(Component):
    display_name = "Isolation BBox Resolver"
    description = "Resolves candidate isolation point bounding boxes from HILT graph data"
    icon = "scan"
    name = "IsolationBBoxResolver"

    inputs = [
        DataInput(name="candidate_data", display_name="Isolation Candidates"),
        MessageTextInput(
            name="api_base_url",
            display_name="API Base URL",
            value="https://api.plant360.ai:8080",
        ),
        SecretStrInput(
            name="auth_token",
            display_name="Authentication Token",
            info="Bearer token for Plant360 API access",
            required=True,
            value="",
        ),
        MessageTextInput(
            name="job_id",
            display_name="P&ID Job ID",
            info="Job id used by /jobs/get_job_hilt_graph/{job_id} and the nested job graph endpoint",
            value="274",
        ),
        MessageTextInput(
            name="project_id",
            display_name="Project ID",
            info="Optional project id for /projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph",
            value="",
        ),
        MessageTextInput(
            name="collection_id",
            display_name="Collection ID",
            info="Optional collection id for /projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph",
            value="",
        ),
        MessageTextInput(
            name="max_position_distance",
            display_name="Max Position Match Distance",
            info="Reject nearest HILT matches farther than this distance before transform fallback",
            value="250",
        ),
        MessageTextInput(
            name="fallback_bbox_size",
            display_name="Fallback BBox Size",
            info="Box size used when graph coordinates are transformed but exact HILT symbol size is unknown",
            value="56",
        ),
        MessageTextInput(
            name="graph_x_scale",
            display_name="Graph X Scale",
            info="Fallback transform: image_math_x = graph_x * scale + offset",
            value="6",
        ),
        MessageTextInput(
            name="graph_x_offset",
            display_name="Graph X Offset",
            info="Fallback transform: image_math_x = graph_x * scale + offset",
            value="700",
        ),
        MessageTextInput(
            name="graph_y_scale",
            display_name="Graph Y Scale",
            info="Fallback transform: image_math_y = graph_y * scale + offset",
            value="1",
        ),
        MessageTextInput(
            name="graph_y_offset",
            display_name="Graph Y Offset",
            info="Fallback transform: image_math_y = graph_y * scale + offset",
            value="3080",
        ),
        BoolInput(
            name="verify_ssl",
            display_name="Verify SSL",
            value=False,
        ),
        BoolInput(
            name="enable_approximate_fallbacks",
            display_name="Enable Approximate Fallbacks",
            info="Allow nearest-position and graph transform bbox matching when exact STLM/HILT UUID matching fails",
            value=False,
        ),
    ]

    outputs = [
        Output(display_name="Candidates With BBox", name="candidates_with_bbox", method="resolve_bboxes"),
        Output(display_name="Debug Summary", name="debug_summary", method="build_debug_summary"),
    ]

    def _resolve_payload(self):
        if hasattr(self, "_resolved_payload_cache"):
            return self._resolved_payload_cache

        data = _unwrap_data(self.candidate_data) or {}
        if data.get("error"):
            return data

        try:
            hilt_graph = self._fetch_hilt_graph()
            image_size = hilt_graph.get("imageSize", {}) or {}
            image_height = image_size.get("height")
            nodes = hilt_graph.get("nodes", []) or []
            stlm_symbols = {}
            stlm_error = None
            job_graph_nodes = []
            job_graph_error = None
            job_graph_context = {}

            try:
                job_graph_nodes, job_graph_context = self._fetch_job_graph_nodes()
                if job_graph_context:
                    data.setdefault("context", {}).update(
                        {key: value for key, value in job_graph_context.items() if value not in (None, "", [])}
                    )
            except Exception as exc:
                job_graph_error = str(exc)

            try:
                stlm_symbols = self._fetch_stlm_symbols()
            except Exception as exc:
                stlm_error = str(exc)

            context = data.setdefault("context", {})
            if not context.get("job_name"):
                job_id_for_context = _leading_int_text(self._context_value(context, "job_id") or getattr(self, "job_id", "") or "")
                if job_id_for_context:
                    try:
                        fetched_job_context = self._fetch_job_context(job_id_for_context)
                        context.update({key: value for key, value in fetched_job_context.items() if value not in (None, "", [])})
                    except Exception as exc:
                        job_graph_error = job_graph_error or f"job_context_fetch_failed: {exc}"

            if not image_height or not nodes:
                raise ValueError("HILT graph missing imageSize.height or nodes")

            token_to_node = {}
            for node in nodes:
                for token in self._node_tokens(node):
                    token_to_node.setdefault(token, node)
            cnvrt_id_to_node = self._build_hilt_cnvrt_index(nodes)
            stlm_uuid_to_symbol = self._build_stlm_uuid_index(stlm_symbols)

            transform, control_points = self._derive_transform(data, token_to_node)
            max_position_distance = _number(getattr(self, "max_position_distance", 250)) or 250
            enable_approximate_fallbacks = bool(getattr(self, "enable_approximate_fallbacks", False))

            resolved_count = 0
            stlm_resolved_count = 0
            job_graph_resolved_count = 0
            token_resolved_count = 0
            position_resolved_count = 0
            transform_resolved_count = 0
            manual_transform_resolved_count = 0
            cnvrt_resolved_count = 0
            tag_resolved_count = 0
            tag_match_debug = []
            rejected_bbox_debug = []
            unresolved = []
            match_debug = []
            candidates = data.get("candidates", []) or []
            context_warnings = self._validate_candidate_contexts(candidates, data.get("context") or {})

            for candidate in candidates:
                if candidate.get("bbox"):
                    resolved_count += 1
                    continue

                if candidate.get("context_validation_warning"):
                    unresolved.append(candidate.get("candidate_id"))
                    continue

                matched_stlm_uuid, matched_stlm_symbol = self._match_stlm_symbol(candidate, stlm_uuid_to_symbol)
                if matched_stlm_symbol:
                    bbox = self._bbox_from_stlm_symbol(matched_stlm_symbol)
                    if bbox and self._bbox_in_image(bbox, image_size):
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_stlm_uuid
                        candidate["visual_node_id"] = matched_stlm_uuid
                        candidate["visual_source"] = "stlm_symbol_json"
                        candidate["bbox_match_method"] = "stlm_uuid"
                        tag_value = self._apply_stlm_tag(candidate, matched_stlm_symbol)
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "tag_number": tag_value,
                                    "source": "stlm_symbol_json",
                                    "node_id": matched_stlm_uuid,
                                }
                            )
                        resolved_count += 1
                        stlm_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "visual_node_id": matched_stlm_uuid,
                                "bbox_match_method": "stlm_uuid",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue
                    rejected_bbox_debug.append(
                        {
                            "candidate_id": candidate.get("candidate_id"),
                            "method": "stlm_uuid",
                            "visual_node_id": matched_stlm_uuid,
                            "bbox": bbox,
                        }
                    )

                matched_node = self._match_hilt_node_by_cnvrt_id(candidate, cnvrt_id_to_node)
                if matched_node:
                    bbox = self._bbox_from_node(matched_node, float(image_height))
                    if bbox and self._bbox_in_image(bbox, image_size):
                        matched_cnvrt_id = self._first_cnvrt_id_from_node(matched_node)
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_node.get("id")
                        candidate["matched_cnvrt_id"] = matched_cnvrt_id
                        candidate["bbox_match_method"] = "cnvrt_id"
                        tag_value = self._apply_real_tag(candidate, matched_node, "hilt")
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "cnvrt_id": candidate.get("cnvrt_id"),
                                    "tag_number": tag_value,
                                    "source": "hilt",
                                    "node_id": matched_node.get("id"),
                                }
                            )
                        resolved_count += 1
                        cnvrt_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "matched_cnvrt_id": matched_cnvrt_id,
                                "hilt_node_id": matched_node.get("id"),
                                "bbox_match_method": "cnvrt_id",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue
                    rejected_bbox_debug.append(
                        {
                            "candidate_id": candidate.get("candidate_id"),
                            "cnvrt_id": candidate.get("cnvrt_id"),
                            "method": "cnvrt_id",
                            "hilt_node_id": matched_node.get("id"),
                            "bbox": bbox,
                        }
                    )

                matched_node = self._match_hilt_node_by_source_id(candidate, nodes)
                if matched_node:
                    bbox = self._bbox_from_node(matched_node, float(image_height))
                    if bbox and self._bbox_in_image(bbox, image_size):
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_node.get("id")
                        candidate["visual_node_id"] = matched_node.get("id")
                        candidate["visual_source"] = "hilt_graph"
                        candidate["bbox_match_method"] = "hilt_source_id"
                        tag_value = self._apply_real_tag(candidate, matched_node, "hilt")
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "tag_number": tag_value,
                                    "source": "hilt",
                                    "node_id": matched_node.get("id"),
                                }
                            )
                        resolved_count += 1
                        token_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "hilt_node_id": matched_node.get("id"),
                                "bbox_match_method": "hilt_source_id",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue

                matched_job_graph_node = self._match_job_graph_node(candidate, job_graph_nodes)
                if matched_job_graph_node:
                    bbox = self._bbox_from_job_graph_node(matched_job_graph_node)
                    if bbox:
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_job_graph_node.get("id")
                        candidate["bbox_match_method"] = "job_graph_token"
                        tag_value = self._apply_real_tag(candidate, matched_job_graph_node, "job_graph")
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "tag_number": tag_value,
                                    "source": "job_graph",
                                    "node_id": matched_job_graph_node.get("id"),
                                }
                            )
                        resolved_count += 1
                        job_graph_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "job_graph_node_id": matched_job_graph_node.get("id"),
                                "bbox_match_method": "job_graph_token",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue

                matched_node = None
                for token in self._candidate_tokens(candidate):
                    matched_node = token_to_node.get(token)
                    if matched_node:
                        break

                if matched_node:
                    bbox = self._bbox_from_node(matched_node, float(image_height))
                    if bbox:
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_node.get("id")
                        candidate["bbox_match_method"] = "token"
                        tag_value = self._apply_real_tag(candidate, matched_node, "hilt")
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "tag_number": tag_value,
                                    "source": "hilt",
                                    "node_id": matched_node.get("id"),
                                }
                            )
                        resolved_count += 1
                        token_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "hilt_node_id": matched_node.get("id"),
                                "bbox_match_method": "token",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue

                if not enable_approximate_fallbacks:
                    unresolved.append(candidate.get("candidate_id"))
                    continue

                matched_node, distance = self._nearest_node_by_position(candidate, nodes)
                if matched_node and distance is not None and distance <= max_position_distance:
                    bbox = self._bbox_from_node(matched_node, float(image_height))
                    if bbox:
                        candidate["bbox"] = bbox
                        candidate["hilt_node_id"] = matched_node.get("id")
                        candidate["bbox_match_method"] = "nearest_position"
                        candidate["bbox_match_distance"] = distance
                        tag_value = self._apply_real_tag(candidate, matched_node, "hilt")
                        if tag_value:
                            tag_resolved_count += 1
                            tag_match_debug.append(
                                {
                                    "candidate_id": candidate.get("candidate_id"),
                                    "tag_number": tag_value,
                                    "source": "hilt",
                                    "node_id": matched_node.get("id"),
                                }
                            )
                        resolved_count += 1
                        position_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "candidate_center": self._candidate_center(candidate),
                                "hilt_node_id": matched_node.get("id"),
                                "hilt_center": self._hilt_center(matched_node),
                                "bbox_match_method": "nearest_position",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "distance": distance,
                                "bbox": bbox,
                            }
                        )
                        continue

                transformed_center = self._apply_transform(self._candidate_center(candidate), transform) if transform and self._candidate_center(candidate) else None
                if transformed_center:
                    bbox = self._bbox_from_center(transformed_center, float(image_height))
                    if not self._bbox_in_image(bbox, image_size):
                        rejected_bbox_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "method": "graph_transform",
                                "bbox": bbox,
                            }
                        )
                    else:
                        candidate["bbox"] = bbox
                        candidate["bbox_match_method"] = "graph_transform"
                        candidate["bbox_transformed_center"] = transformed_center
                        resolved_count += 1
                        transform_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "candidate_center": self._candidate_center(candidate),
                                "transformed_center": transformed_center,
                                "bbox_match_method": "graph_transform",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue

                candidate_center = self._candidate_center(candidate)
                if candidate_center:
                    manual_transform = self._manual_transform()
                    manual_center = self._apply_transform(candidate_center, manual_transform)
                    bbox = self._bbox_from_center(manual_center, float(image_height))
                    if not self._bbox_in_image(bbox, image_size):
                        rejected_bbox_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "method": "manual_graph_transform",
                                "bbox": bbox,
                            }
                        )
                    else:
                        candidate["bbox"] = bbox
                        candidate["bbox_match_method"] = "manual_graph_transform"
                        candidate["bbox_transformed_center"] = manual_center
                        resolved_count += 1
                        manual_transform_resolved_count += 1
                        match_debug.append(
                            {
                                "candidate_id": candidate.get("candidate_id"),
                                "cnvrt_id": candidate.get("cnvrt_id"),
                                "candidate_center": candidate_center,
                                "manual_transform": manual_transform,
                                "transformed_center": manual_center,
                                "bbox_match_method": "manual_graph_transform",
                                "source_component_tag": candidate.get("source_component_tag"),
                                "source_component_id": candidate.get("source_component_id"),
                                "traversal_depth": candidate.get("traversal_depth"),
                                "bbox": bbox,
                            }
                        )
                        continue

                unresolved.append(candidate.get("candidate_id"))

            debug = data.setdefault("debug", {})
            debug["bbox_resolver_code_version"] = RESOLVER_CODE_VERSION
            debug["visual_resolver_code_version"] = RESOLVER_CODE_VERSION
            debug["bbox_resolved_count"] = resolved_count
            debug["bbox_stlm_resolved_count"] = stlm_resolved_count
            debug["bbox_job_graph_resolved_count"] = job_graph_resolved_count
            debug["bbox_cnvrt_id_resolved_count"] = cnvrt_resolved_count
            debug["bbox_token_resolved_count"] = token_resolved_count
            debug["bbox_position_resolved_count"] = position_resolved_count
            debug["bbox_transform_resolved_count"] = transform_resolved_count
            debug["bbox_manual_transform_resolved_count"] = manual_transform_resolved_count
            debug["bbox_rejected_count"] = len(rejected_bbox_debug)
            debug["bbox_rejected_samples"] = rejected_bbox_debug[:10]
            debug["tag_number_resolved_count"] = tag_resolved_count
            debug["tag_number_match_samples"] = tag_match_debug[:10]
            debug["bbox_unresolved_candidate_ids"] = unresolved[:20]
            debug["bbox_context_warnings"] = context_warnings[:25]
            debug["bbox_image_size"] = image_size
            debug["bbox_hilt_node_count"] = len(nodes)
            debug["bbox_hilt_cnvrt_id_count"] = len(cnvrt_id_to_node)
            debug["bbox_stlm_symbol_count"] = len(stlm_uuid_to_symbol)
            debug["bbox_stlm_error"] = stlm_error
            debug["bbox_job_graph_node_count"] = len(job_graph_nodes)
            debug["bbox_job_graph_error"] = job_graph_error
            debug["bbox_job_graph_context"] = job_graph_context
            debug["bbox_transform"] = transform
            debug["bbox_manual_transform"] = self._manual_transform()
            debug["bbox_transform_control_points"] = control_points[:20]
            debug["bbox_match_samples"] = match_debug[:10]
            debug["bbox_hilt_node_samples"] = self._node_debug_sample(nodes)
            debug["bbox_stlm_symbol_samples"] = self._stlm_symbol_debug_sample(stlm_uuid_to_symbol)
            debug["bbox_job_graph_node_samples"] = self._job_graph_node_debug_sample(job_graph_nodes)

            self._resolved_payload_cache = data
            return data

        except Exception as exc:
            debug = data.setdefault("debug", {})
            debug["bbox_resolver_code_version"] = RESOLVER_CODE_VERSION
            debug["bbox_error"] = str(exc)
            self._resolved_payload_cache = data
            return data

    def _fetch_hilt_graph(self):
        token = SecretStr(self.auth_token).get_secret_value()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        context = self._candidate_context()
        job_id = _leading_int_text(self._context_value(context, "job_id") or self.job_id)
        url = f"{self.api_base_url.rstrip('/')}/jobs/get_job_hilt_graph/{job_id}"
        with httpx.Client(verify=bool(self.verify_ssl), timeout=API_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
        return body.get("hilt_graph") or body

    def _fetch_job_graph_nodes(self):
        context = self._candidate_context()
        job_id = _leading_int_text(self._context_value(context, "job_id") or getattr(self, "job_id", "") or "")
        project_id = _leading_int_text(self._context_value(context, "project_id") or getattr(self, "project_id", "") or "")
        collection_id = _leading_int_text(self._context_value(context, "collection_id") or getattr(self, "collection_id", "") or "")
        job_context = {}

        if job_id and (not project_id or not collection_id or collection_id == "0"):
            job_context = self._fetch_job_context(job_id)
            project_id = _leading_int_text(project_id or job_context.get("project_id") or "")
            collection_id = _leading_int_text(job_context.get("collection_id") or collection_id or "")

        if not project_id or not collection_id or not job_id:
            legacy_nodes, legacy_context = self._fetch_legacy_job_graph_nodes(job_id) if job_id else ([], {})
            job_context.update(legacy_context)
            return legacy_nodes, job_context

        token = SecretStr(self.auth_token).get_secret_value()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        url = f"{self.api_base_url.rstrip('/')}/projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph"
        with httpx.Client(verify=bool(self.verify_ssl), timeout=API_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()

        pid_graph = body.get("pid_graph") if isinstance(body, dict) else None
        if not isinstance(pid_graph, dict):
            legacy_nodes, legacy_context = self._fetch_legacy_job_graph_nodes(job_id)
            job_context.update(legacy_context)
            return legacy_nodes, job_context
        job_context["job_graph_source"] = "nested_job_graph_endpoint"
        return pid_graph.get("nodes", []) or [], job_context

    def _fetch_stlm_symbols(self):
        token = SecretStr(self.auth_token).get_secret_value()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        context = self._candidate_context()
        job_id = _leading_int_text(self._context_value(context, "job_id") or getattr(self, "job_id", "") or "")
        if not job_id:
            return {}

        url = f"{self.api_base_url.rstrip('/')}/symbol_text_line_master/get_stl_master_by_job_id/{job_id}"
        with httpx.Client(verify=bool(self.verify_ssl), timeout=API_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()

        symbols = body.get("symbol_json") if isinstance(body, dict) else None
        return symbols if isinstance(symbols, dict) else {}

    def _fetch_legacy_job_graph_nodes(self, job_id):
        token = SecretStr(self.auth_token).get_secret_value()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        url = f"{self.api_base_url.rstrip('/')}/jobs/{job_id}"
        with httpx.Client(verify=bool(self.verify_ssl), timeout=API_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()

        if not isinstance(body, dict):
            return [], {"job_id": job_id, "job_graph_source": "legacy_job_detail_unavailable"}

        context = {
            "job_id": body.get("id") or job_id,
            "job_name": body.get("name"),
            "project_id": body.get("project") or body.get("project_id"),
            "job_graph_source": "legacy_job_detail",
        }

        graph_json = body.get("graph_json") or {}
        if isinstance(graph_json, dict):
            pid_graph = graph_json.get("pid_graph") or graph_json.get("graph") or graph_json
            if isinstance(pid_graph, dict) and isinstance(pid_graph.get("nodes"), list):
                return pid_graph.get("nodes") or [], context

        return [], context

    def _fetch_job_context(self, job_id):
        token = SecretStr(self.auth_token).get_secret_value()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        url = f"{self.api_base_url.rstrip('/')}/jobs/get_job_details/{job_id}"
        with httpx.Client(verify=bool(self.verify_ssl), timeout=API_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()

        if not isinstance(body, dict):
            return {"job_id": job_id}

        return {
            "job_id": body.get("id") or job_id,
            "job_name": body.get("name"),
            "project_id": body.get("project") or body.get("project_id"),
            "collection_id": body.get("collection_id") or body.get("collection"),
            "collection_name": body.get("collection_name"),
        }

    def _candidate_context(self):
        data = _unwrap_data(getattr(self, "candidate_data", None)) or {}
        if not isinstance(data, dict):
            return {}
        return data.get("context") or {}

    def _context_value(self, context, key):
        value = context.get(key) if isinstance(context, dict) else None
        return value if value not in (None, "", []) else None

    def _clean_id(self, value):
        text = str(value or "").strip()
        return text if text else None

    def _candidate_cnvrt_id(self, candidate):
        props = candidate.get("properties", {}) or {}
        for value in (
            candidate.get("cnvrt_id"),
            props.get("cnvrt_id"),
            props.get("cnvrtId"),
            props.get("cnvrtID"),
            props.get("CNVRT ID"),
            props.get("CNVRT_ID"),
            props.get("source_cnvrt_id"),
            props.get("visual_cnvrt_id"),
        ):
            cleaned = self._clean_id(value)
            if cleaned:
                return cleaned
        return None

    def _candidate_visual_ids(self, candidate):
        props = candidate.get("properties", {}) or {}
        values = [
            candidate.get("cnvrt_id"),
            candidate.get("visual_node_id"),
            candidate.get("hilt_node_id"),
            candidate.get("uuid"),
            props.get("cnvrt_id"),
            props.get("node_id"),
            props.get("source_id"),
            props.get("uuid"),
            props.get("id"),
            props.get("name"),
        ]
        cleaned = []
        for value in values:
            text = self._clean_id(value)
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    def _cnvrt_ids_from_node(self, node):
        payload = node.get("payload", {}) or {}
        values = []

        for source in (node, payload):
            for key in ("cnvrt_id", "cnvrtId", "cnvrtID", "CNVRT ID", "CNVRT_ID", "source_cnvrt_id", "visual_cnvrt_id"):
                values.append(source.get(key))

        for attr in payload.get("attributes", []) or []:
            if not isinstance(attr, dict):
                continue
            name = str(attr.get("name") or "").strip().lower().replace(" ", "_")
            if name in {"cnvrt_id", "cnvrtid", "cnvrt_id_", "source_cnvrt_id", "visual_cnvrt_id"}:
                values.append(attr.get("value"))

        return {self._clean_id(value) for value in values if self._clean_id(value)}

    def _first_cnvrt_id_from_node(self, node):
        ids = sorted(self._cnvrt_ids_from_node(node))
        return ids[0] if ids else None

    def _build_hilt_cnvrt_index(self, nodes):
        index = {}
        for node in nodes or []:
            for cnvrt_id in self._cnvrt_ids_from_node(node):
                index.setdefault(_norm(cnvrt_id), node)
        return index

    def _match_hilt_node_by_cnvrt_id(self, candidate, cnvrt_id_to_node):
        cnvrt_id = self._candidate_cnvrt_id(candidate)
        if not cnvrt_id:
            return None
        candidate["cnvrt_id"] = cnvrt_id
        return cnvrt_id_to_node.get(_norm(cnvrt_id))

    def _build_stlm_uuid_index(self, symbols):
        index = {}
        for key, payload in (symbols or {}).items():
            if not isinstance(payload, dict):
                continue
            for value in (key, payload.get("uuid"), payload.get("id"), payload.get("source_id"), payload.get("name")):
                cleaned = self._clean_id(value)
                if cleaned:
                    index.setdefault(_norm(cleaned), (str(key), payload))
        return index

    def _match_stlm_symbol(self, candidate, stlm_uuid_to_symbol):
        for visual_id in self._candidate_visual_ids(candidate):
            matched = stlm_uuid_to_symbol.get(_norm(visual_id))
            if matched:
                return matched
        return None, None

    def _bbox_from_stlm_symbol(self, symbol):
        bbox = (symbol or {}).get("bbox")
        if isinstance(bbox, list) and len(bbox) >= 4:
            nums = [_number(value) for value in bbox[:4]]
            if all(value is not None for value in nums):
                return [int(round(value)) for value in nums]
        return []

    def _stlm_attr_value(self, symbol, attr_names):
        wanted = {str(name).strip().lower() for name in attr_names}
        for field in ("attributes", "segment_attributes"):
            for attr in (symbol or {}).get(field, []) or []:
                if not isinstance(attr, dict):
                    continue
                name = str(attr.get("name") or "").strip().lower()
                value = self._clean_real_tag(attr.get("value"))
                if name in wanted and value:
                    return value
        return None

    def _apply_stlm_tag(self, candidate, symbol):
        tag = self._stlm_attr_value(symbol, ("tag", "tag_number", "tag number"))
        if not tag:
            return None
        candidate["tag_number"] = tag
        candidate["tag_number_source"] = "stlm_symbol_json"
        return tag

    def _match_hilt_node_by_source_id(self, candidate, nodes):
        ids = {_norm(value) for value in self._candidate_visual_ids(candidate)}
        ids = {value for value in ids if value}
        if not ids:
            return None

        for node in nodes or []:
            payload = node.get("payload", {}) or {}
            node_ids = {
                _norm(node.get("id")),
                _norm(payload.get("id")),
                _norm(payload.get("source_id")),
                _norm(payload.get("name")),
            }
            if ids & {value for value in node_ids if value}:
                return node
        return None

    def _unit_matches_job_name(self, unit_name, job_name):
        unit_text = _norm(unit_name)
        job_text = _norm(job_name)
        if not unit_text or not job_text:
            return True
        return unit_text == job_text or unit_text in job_text or job_text in unit_text

    def _validate_candidate_contexts(self, candidates, context):
        job_name = self._context_value(context, "job_name")
        warnings = []
        if not job_name:
            return warnings

        for candidate in candidates or []:
            unit_name = candidate.get("unit_name") or (candidate.get("properties", {}) or {}).get("unit_name")
            if not unit_name or self._unit_matches_job_name(unit_name, job_name):
                continue

            warning = {
                "candidate_id": candidate.get("candidate_id"),
                "cnvrt_id": candidate.get("cnvrt_id"),
                "unit_name": unit_name,
                "job_name": job_name,
                "source_component_tag": candidate.get("source_component_tag"),
                "message": "Candidate unit_name does not match selected job_name; bbox resolution skipped to avoid wrong P&ID marker.",
            }
            candidate["context_validation_warning"] = warning["message"]
            candidate["bbox_match_method"] = "context_mismatch_skipped"
            warnings.append(warning)
        return warnings

    def _bbox_from_job_graph_node(self, node):
        x = _number(node.get("orig_x"))
        y = _number(node.get("orig_y"))
        width = _number(node.get("orig_bbox_width"))
        height = _number(node.get("orig_bbox_height"))
        if x is None or y is None or not width or not height:
            return []
        return [int(x), int(y), int(width), int(height)]

    def _match_job_graph_node(self, candidate, nodes):
        if not nodes:
            return None

        candidate_tokens = self._candidate_tokens(candidate)
        for node in nodes:
            if candidate_tokens & self._job_graph_node_tokens(node):
                return node
        return None

    def _job_graph_node_tokens(self, node):
        tokens = {
            _norm(node.get("id")),
            _norm(node.get("tag")),
            _norm(node.get("node_classification")),
        }

        for attr in node.get("attributes", []) or []:
            if isinstance(attr, dict):
                tokens.add(_norm(attr.get("name")))
                tokens.add(_norm(attr.get("value")))
        return {token for token in tokens if token}

    def _clean_real_tag(self, value):
        text = str(value or "").strip()
        if not text or _looks_like_uuid(text):
            return None
        return text

    def _tag_from_hilt_node(self, node):
        payload = node.get("payload", {}) or {}
        for key in ("tag", "tag_number", "label"):
            tag = self._clean_real_tag(payload.get(key))
            if tag:
                return tag

        for attr in payload.get("attributes", []) or []:
            if not isinstance(attr, dict):
                continue
            name = str(attr.get("name") or "").strip().lower()
            if name in {"tag", "tag_number", "tag number"}:
                tag = self._clean_real_tag(attr.get("value"))
                if tag:
                    return tag
        return None

    def _tag_from_job_graph_node(self, node):
        tag = self._clean_real_tag(node.get("tag"))
        if tag:
            return tag

        for attr in node.get("attributes", []) or []:
            if not isinstance(attr, dict):
                continue
            name = str(attr.get("name") or "").strip().lower()
            if name in {"tag", "tag_number", "tag number"}:
                tag = self._clean_real_tag(attr.get("value"))
                if tag:
                    return tag
        return None

    def _apply_real_tag(self, candidate, node, source):
        tag = self._tag_from_job_graph_node(node) if source == "job_graph" else self._tag_from_hilt_node(node)
        if not tag:
            return None
        candidate["tag_number"] = tag
        candidate["tag_number_source"] = source
        return tag

    def _job_graph_node_debug_sample(self, nodes, limit=10):
        samples = []
        for node in nodes:
            bbox = self._bbox_from_job_graph_node(node)
            if not bbox:
                continue
            samples.append(
                {
                    "id": node.get("id"),
                    "tag": node.get("tag"),
                    "node_classification": node.get("node_classification"),
                    "bbox": bbox,
                    "tokens": sorted(list(self._job_graph_node_tokens(node)))[:8],
                }
            )
            if len(samples) >= limit:
                break
        return samples

    def _bbox_from_node(self, node, image_height):
        payload = node.get("payload", {}) or {}
        location = payload.get("bounding_box_location") or {}
        width = payload.get("bounding_box_width")
        height = payload.get("bounding_box_height")

        if not location or width is None or height is None:
            return []

        try:
            width = float(width)
            height = float(height)
            center_x = float(location.get("x"))
            center_y = float(location.get("y"))
        except Exception:
            return []

        x = int(center_x - width / 2)
        y = int(image_height - (center_y + height / 2))
        return [x, y, int(width), int(height)]

    def _hilt_center(self, node):
        payload = node.get("payload", {}) or {}
        location = payload.get("bounding_box_location") or {}
        if not isinstance(location, dict):
            return None

        x = _number(location.get("x"))
        y = _number(location.get("y"))
        if x is None or y is None:
            return None
        return x, y

    def _candidate_center(self, candidate):
        props = candidate.get("properties", {}) or {}
        return self._graph_center_from_properties(props)

    def _graph_center_from_properties(self, props):
        x = _number(props.get("x_pos") or props.get("x"))
        y = _number(props.get("y_pos") or props.get("y"))
        if x is None or y is None:
            return None
        return x, y

    def _bbox_from_center(self, center, image_height, width=None, height=None):
        size = _number(getattr(self, "fallback_bbox_size", 56)) or 56
        width = float(width or size)
        height = float(height or size)
        center_x, center_y = center
        x = int(center_x - width / 2)
        y = int(float(image_height) - (center_y + height / 2))
        return [x, y, int(width), int(height)]

    def _bbox_in_image(self, bbox, image_size):
        if not bbox or len(bbox) != 4:
            return False

        image_width = _number((image_size or {}).get("width"))
        image_height = _number((image_size or {}).get("height"))
        if not image_width or not image_height:
            return False

        x, y, width, height = [_number(value) for value in bbox]
        if x is None or y is None or not width or not height:
            return False

        return x >= 0 and y >= 0 and x + width <= image_width and y + height <= image_height

    def _entity_class(self, node):
        payload = node.get("payload", {}) or {}
        entity_class = payload.get("entity_class") or payload.get("entityClass")
        if entity_class:
            return str(entity_class)

        for attr in payload.get("attributes", []) or []:
            if isinstance(attr, dict) and attr.get("name") in {"entity_class", "entityClass", "class"}:
                return str(attr.get("value") or "")
        return ""

    def _is_compatible_node(self, candidate, node):
        candidate_class = _norm((candidate.get("properties", {}) or {}).get("entity_class"))
        node_class = _norm(self._entity_class(node))
        if not candidate_class:
            return True
        if candidate_class in node_class or node_class in candidate_class:
            return True
        if "valve" in candidate_class and "valve" in node_class:
            return True
        return False

    def _nearest_node_by_position(self, candidate, nodes):
        candidate_center = self._candidate_center(candidate)
        if not candidate_center:
            return None, None

        cx, cy = candidate_center
        best_node = None
        best_distance = None

        for node in nodes:
            if not self._is_compatible_node(candidate, node):
                continue
            center = self._hilt_center(node)
            if not center:
                continue
            nx, ny = center
            distance = ((cx - nx) ** 2 + (cy - ny) ** 2) ** 0.5
            if best_distance is None or distance < best_distance:
                best_node = node
                best_distance = distance

        return best_node, best_distance

    def _node_debug_sample(self, nodes, limit=10):
        samples = []
        for node in nodes:
            payload = node.get("payload", {}) or {}
            center = self._hilt_center(node)
            if not center:
                continue
            samples.append(
                {
                    "id": node.get("id"),
                    "entity_class": self._entity_class(node),
                    "center": center,
                    "width": payload.get("bounding_box_width"),
                    "height": payload.get("bounding_box_height"),
                    "tokens": sorted(list(self._node_tokens(node)))[:5],
                }
            )
            if len(samples) >= limit:
                break
        return samples

    def _stlm_symbol_debug_sample(self, stlm_uuid_to_symbol, limit=10):
        samples = []
        seen = set()
        for normalized_id, (symbol_id, symbol) in (stlm_uuid_to_symbol or {}).items():
            if symbol_id in seen:
                continue
            seen.add(symbol_id)
            bbox = self._bbox_from_stlm_symbol(symbol)
            if not bbox:
                continue
            samples.append(
                {
                    "id": symbol_id,
                    "uuid": symbol.get("uuid"),
                    "entity_class": symbol.get("entity_class"),
                    "entity_type": symbol.get("entity_type"),
                    "bbox": bbox,
                    "tag": self._stlm_attr_value(symbol, ("tag", "tag_number", "tag number")),
                    "segment_id": symbol.get("segment_id"),
                    "system_id": symbol.get("system_id"),
                }
            )
            if len(samples) >= limit:
                break
        return samples

    def _node_tokens(self, node):
        payload = node.get("payload", {}) or {}
        tokens = {_norm(node.get("id")), _norm(payload.get("id")), _norm(payload.get("node_id"))}

        for key in ("name", "tag", "tag_number", "label"):
            tokens.add(_norm(payload.get(key)))

        for attr in payload.get("attributes", []) or []:
            if isinstance(attr, dict):
                tokens.add(_norm(attr.get("value")))

        return {token for token in tokens if token}

    def _candidate_tokens(self, candidate):
        props = candidate.get("properties", {}) or {}
        return self._property_tokens(props) | {_norm(candidate.get("candidate_id")), _norm(candidate.get("tag_number"))}

    def _property_tokens(self, props):
        tokens = {
            _norm(props.get("node_id")),
            _norm(props.get("name")),
            _norm(props.get("tag")),
            _norm(props.get("tag_number")),
            _norm(props.get("Equipment Name")),
        }
        return {token for token in tokens if token}

    def _linear_fit(self, pairs):
        n = len(pairs)
        if n < 2:
            return None
        sum_x = sum(pair[0] for pair in pairs)
        sum_y = sum(pair[1] for pair in pairs)
        sum_xx = sum(pair[0] * pair[0] for pair in pairs)
        sum_xy = sum(pair[0] * pair[1] for pair in pairs)
        denominator = n * sum_xx - sum_x * sum_x
        if abs(denominator) < 1e-9:
            return None
        scale = (n * sum_xy - sum_x * sum_y) / denominator
        offset = (sum_y - scale * sum_x) / n
        return scale, offset

    def _build_graph_records(self, data):
        records = []

        for candidate in data.get("candidates", []) or []:
            props = candidate.get("properties", {}) or {}
            center = self._graph_center_from_properties(props)
            if center:
                records.append({"source": "candidate", "props": props, "center": center, "tokens": self._property_tokens(props)})

        debug = data.get("debug", {}) or {}
        for sample in debug.get("skipped_samples", []) or []:
            props = sample.get("properties", {}) or {}
            center = self._graph_center_from_properties(props)
            if center:
                records.append({"source": "skipped", "props": props, "center": center, "tokens": self._property_tokens(props)})

        return records

    def _derive_transform(self, data, token_to_node):
        control_points = []
        seen = set()

        for record in self._build_graph_records(data):
            matched_node = None
            matched_token = None
            for token in record["tokens"]:
                matched_node = token_to_node.get(token)
                if matched_node:
                    matched_token = token
                    break
            if not matched_node:
                continue

            hilt_center = self._hilt_center(matched_node)
            if not hilt_center:
                continue

            key = (record["center"], hilt_center)
            if key in seen:
                continue
            seen.add(key)
            control_points.append(
                {
                    "token": matched_token,
                    "source": record["source"],
                    "graph_center": record["center"],
                    "hilt_center": hilt_center,
                    "hilt_node_id": matched_node.get("id"),
                }
            )

        x_fit = self._linear_fit([(point["graph_center"][0], point["hilt_center"][0]) for point in control_points])
        y_fit = self._linear_fit([(point["graph_center"][1], point["hilt_center"][1]) for point in control_points])
        if not x_fit or not y_fit:
            return None, control_points

        return {"x_scale": x_fit[0], "x_offset": x_fit[1], "y_scale": y_fit[0], "y_offset": y_fit[1]}, control_points

    def _apply_transform(self, center, transform):
        x, y = center
        return (
            transform["x_scale"] * x + transform["x_offset"],
            transform["y_scale"] * y + transform["y_offset"],
        )

    def _manual_transform(self):
        return {
            "x_scale": _number(getattr(self, "graph_x_scale", 6)) or 6,
            "x_offset": _number(getattr(self, "graph_x_offset", 700)) or 700,
            "y_scale": _number(getattr(self, "graph_y_scale", 1)) or 1,
            "y_offset": _number(getattr(self, "graph_y_offset", 3080)) or 3080,
        }

    def resolve_bboxes(self) -> Data:
        return Data(value=self._resolve_payload())

    def build_debug_summary(self) -> Message:
        data = self._resolve_payload()
        debug = data.get("debug", {}) or {}
        candidates = data.get("candidates", []) or []
        summary = {
            "bbox_resolved_count": debug.get("bbox_resolved_count"),
            "bbox_resolver_code_version": debug.get("bbox_resolver_code_version"),
            "visual_resolver_code_version": debug.get("visual_resolver_code_version"),
            "bbox_stlm_resolved_count": debug.get("bbox_stlm_resolved_count"),
            "bbox_job_graph_resolved_count": debug.get("bbox_job_graph_resolved_count"),
            "bbox_cnvrt_id_resolved_count": debug.get("bbox_cnvrt_id_resolved_count"),
            "bbox_token_resolved_count": debug.get("bbox_token_resolved_count"),
            "bbox_position_resolved_count": debug.get("bbox_position_resolved_count"),
            "bbox_transform_resolved_count": debug.get("bbox_transform_resolved_count"),
            "bbox_manual_transform_resolved_count": debug.get("bbox_manual_transform_resolved_count"),
            "bbox_context_warnings": debug.get("bbox_context_warnings"),
            "tag_number_resolved_count": debug.get("tag_number_resolved_count"),
            "tag_number_match_samples": debug.get("tag_number_match_samples"),
            "bbox_unresolved_candidate_ids": debug.get("bbox_unresolved_candidate_ids"),
            "bbox_image_size": debug.get("bbox_image_size"),
            "bbox_hilt_node_count": debug.get("bbox_hilt_node_count"),
            "bbox_hilt_cnvrt_id_count": debug.get("bbox_hilt_cnvrt_id_count"),
            "bbox_stlm_symbol_count": debug.get("bbox_stlm_symbol_count"),
            "bbox_stlm_error": debug.get("bbox_stlm_error"),
            "bbox_job_graph_node_count": debug.get("bbox_job_graph_node_count"),
            "bbox_job_graph_error": debug.get("bbox_job_graph_error"),
            "bbox_job_graph_context": debug.get("bbox_job_graph_context"),
            "bbox_transform": debug.get("bbox_transform"),
            "bbox_manual_transform": debug.get("bbox_manual_transform"),
            "bbox_transform_control_points": debug.get("bbox_transform_control_points"),
            "bbox_match_samples": debug.get("bbox_match_samples"),
            "bbox_stlm_symbol_samples": debug.get("bbox_stlm_symbol_samples"),
            "bbox_error": debug.get("bbox_error"),
            "candidate_bboxes": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "cnvrt_id": candidate.get("cnvrt_id"),
                    "bbox": candidate.get("bbox"),
                    "method": candidate.get("bbox_match_method"),
                    "hilt_node_id": candidate.get("hilt_node_id"),
                    "visual_node_id": candidate.get("visual_node_id"),
                    "visual_source": candidate.get("visual_source"),
                    "source_component_tag": candidate.get("source_component_tag"),
                    "traversal_depth": candidate.get("traversal_depth"),
                }
                for candidate in candidates[:10]
            ],
        }
        return Message(text="Isolation BBox Resolver Debug:\n" + json.dumps(summary, indent=2))
