from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data


ISOLATION_KEYWORDS = {
    "valve",
    "gate_valve",
    "ball_valve",
    "globe_valve",
    "check_valve",
    "control_valve",
    "blind",
    "spade",
    "flange",
    "breaker",
    "disconnect",
}

VALVE_KEYWORDS = {
    "valve",
    "gate_valve",
    "ball_valve",
    "globe_valve",
    "check_valve",
    "control_valve",
}
ELECTRICAL_KEYWORDS = {"breaker", "disconnect"}
BLIND_KEYWORDS = {"blind", "spade"}
CONDITIONAL_KEYWORDS = {"check_valve", "control_valve"}
MAX_CANDIDATES_PER_SOURCE = 1
MAX_TOTAL_CANDIDATES = 20


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _norm(value):
    return str(value or "").strip().lower()


def _compact_norm(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _looks_like_uuid(value):
    text = str(value or "").strip()
    parts = text.split("-")
    return len(parts) == 5 and [len(part) for part in parts] == [8, 4, 4, 4, 12]


def _tag(properties):
    for key in (
        "tag_number",
        "tag",
        "name",
        "label",
        "equipment_number",
        "Equipment Name",
    ):
        value = properties.get(key)
        if value and not _looks_like_uuid(value):
            return str(value)
    return None


def _first_property(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", []):
            return value
    return None


def _cnvrt_id(properties):
    value = _first_property(
        properties,
        (
            "cnvrt_id",
            "cnvrtId",
            "cnvrtID",
            "CNVRT ID",
            "CNVRT_ID",
            "source_cnvrt_id",
            "visual_cnvrt_id",
        ),
    )
    return str(value).strip() if value not in (None, "", []) else None


def _visual_id(properties, candidate_id=None):
    value = _first_property(
        properties,
        (
            "cnvrt_id",
            "cnvrtId",
            "cnvrtID",
            "node_id",
            "source_id",
            "uuid",
            "name",
        ),
    )
    if value not in (None, "", []):
        return str(value).strip()
    return str(candidate_id).strip() if candidate_id not in (None, "", []) else None


def _property_preview(properties):
    preview = {}
    for key in sorted(properties.keys())[:12]:
        value = properties.get(key)
        if value is not None:
            preview[key] = value
    return preview


def _path_trace(candidate):
    return {
        "source_component_tag": candidate.get("source_component_tag"),
        "source_component_id": candidate.get("source_component_id"),
        "source_name": candidate.get("source_name"),
        "traversal_depth": candidate.get("traversal_depth"),
        "reason": candidate.get("reason"),
    }


def _merge_candidate(existing, candidate):
    existing_paths = existing.setdefault("source_paths", [])
    new_path = _path_trace(candidate)
    path_key = (
        new_path.get("source_component_tag"),
        new_path.get("source_component_id"),
        new_path.get("source_name"),
        new_path.get("traversal_depth"),
    )
    seen_path_keys = {
        (
            path.get("source_component_tag"),
            path.get("source_component_id"),
            path.get("source_name"),
            path.get("traversal_depth"),
        )
        for path in existing_paths
    }
    if path_key not in seen_path_keys:
        existing_paths.append(new_path)

    candidate_depth = candidate.get("traversal_depth", 99)
    existing_depth = existing.get("traversal_depth", 99)
    candidate_confidence = candidate.get("confidence", 0)
    existing_confidence = existing.get("confidence", 0)

    if (candidate_depth, -candidate_confidence) < (
        existing_depth,
        -existing_confidence,
    ):
        preserved_paths = existing_paths
        existing.update(candidate)
        existing["source_paths"] = preserved_paths

    existing["source_path_count"] = len(existing_paths)
    return existing


def _candidate_sort_key(candidate):
    return (
        candidate.get("traversal_depth", 99),
        -candidate.get("confidence", 0),
        str(candidate.get("tag_number") or candidate.get("candidate_id")),
    )


def _select_boundary_candidates(candidates):
    by_source = {}
    for candidate in candidates:
        source_key = (
            candidate.get("equipment_tag"),
            candidate.get("source_component_id"),
            candidate.get("source_component_tag"),
        )
        by_source.setdefault(source_key, []).append(candidate)

    selected = []
    source_selection_samples = []
    for source_key, source_candidates in sorted(
        by_source.items(), key=lambda item: str(item[0])
    ):
        ordered = sorted(source_candidates, key=_candidate_sort_key)
        chosen = ordered[:MAX_CANDIDATES_PER_SOURCE]
        selected.extend(chosen)
        source_selection_samples.append(
            {
                "equipment_tag": source_key[0],
                "source_component_id": source_key[1],
                "source_component_tag": source_key[2],
                "candidate_count": len(source_candidates),
                "selected_candidate_ids": [
                    candidate.get("candidate_id") for candidate in chosen
                ],
                "selected_depths": [
                    candidate.get("traversal_depth") for candidate in chosen
                ],
            }
        )

    return selected, source_selection_samples


def _context_job_name(data, config):
    for source in (data.get("context") or {}, (config or {}).get("context") or {}):
        value = (
            source.get("job_name") or source.get("jobName") or source.get("unit_name")
        )
        if value not in (None, "", []):
            return str(value).strip()
    return None


def _unit_matches_job_name(unit_name, job_name):
    unit = _compact_norm(unit_name)
    job = _compact_norm(job_name)
    if not unit or not job:
        return True
    return unit == job or unit in job or job in unit


def _filter_candidates_for_context(candidates, job_name):
    if not job_name:
        return candidates, []

    kept = []
    filtered = []
    for candidate in candidates:
        unit_name = candidate.get("unit_name") or (
            candidate.get("properties", {}) or {}
        ).get("unit_name")
        if not unit_name or _unit_matches_job_name(unit_name, job_name):
            kept.append(candidate)
            continue
        filtered.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "visual_id": candidate.get("visual_id"),
                "unit_name": unit_name,
                "job_name": job_name,
                "source_component_tag": candidate.get("source_component_tag"),
                "traversal_depth": candidate.get("traversal_depth"),
                "reason": "candidate unit_name does not match selected job_name",
            }
        )
    return kept, filtered


def _infer_job_name_from_candidates(candidates):
    scored = {}
    for candidate in candidates:
        unit_name = candidate.get("unit_name") or (
            candidate.get("properties", {}) or {}
        ).get("unit_name")
        if unit_name in (None, "", []):
            continue

        score = 1
        if candidate.get("source_name") == "component direct neighbor":
            score += 8
        traversal_depth = candidate.get("traversal_depth", 99)
        if traversal_depth == 1:
            score += 5
        elif traversal_depth == 2:
            score += 2

        key = str(unit_name).strip()
        scored[key] = scored.get(key, 0) + score

    if not scored:
        return None
    return sorted(scored.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _number(value):
    try:
        return float(value)
    except Exception:
        return None


def _bbox_from_properties(properties):
    for key in ("bbox", "bounding_box", "boundingBox"):
        value = properties.get(key)
        if isinstance(value, list) and len(value) >= 4:
            return [int(float(item)) for item in value[:4]]
        if isinstance(value, str):
            parts = [
                part.strip()
                for part in value.replace("[", "").replace("]", "").split(",")
            ]
            if len(parts) >= 4:
                nums = [_number(part) for part in parts[:4]]
                if all(num is not None for num in nums):
                    return [int(num) for num in nums]

    location = properties.get("bounding_box_location") or properties.get(
        "boundingBoxLocation"
    )
    width = _number(
        properties.get("bounding_box_width")
        or properties.get("boundingBoxWidth")
        or properties.get("width")
    )
    height = _number(
        properties.get("bounding_box_height")
        or properties.get("boundingBoxHeight")
        or properties.get("height")
    )

    if isinstance(location, dict) and width and height:
        x = _number(location.get("x"))
        y = _number(location.get("y"))
        if x is not None and y is not None:
            return [int(x - width / 2), int(y - height / 2), int(width), int(height)]

    x = _number(
        properties.get("x")
        or properties.get("bbox_x")
        or properties.get("bounding_box_x")
    )
    y = _number(
        properties.get("y")
        or properties.get("bbox_y")
        or properties.get("bounding_box_y")
    )
    if x is not None and y is not None and width and height:
        return [int(x), int(y), int(width), int(height)]

    return []


def _keywords_for(vertex):
    properties = vertex.get("properties", {}) or {}
    text = (
        " ".join(
            [
                _norm(vertex.get("label")),
                _norm(vertex.get("type")),
                _norm(vertex.get("entity_class")),
                _norm(properties.get("type")),
                _norm(properties.get("class")),
                _norm(properties.get("entity_class")),
                _norm(properties.get("component_class")),
                _norm(properties.get("description")),
                _norm(_tag(properties)),
            ]
        )
        .replace("-", "_")
        .replace(" ", "_")
    )

    return sorted(keyword for keyword in ISOLATION_KEYWORDS if keyword in text)


def _split_policy_list(value, default):
    if value in (None, "", []):
        return set(default)
    if isinstance(value, str):
        return {
            item.strip().lower().replace(" ", "_")
            for item in value.split(",")
            if item.strip()
        }
    if isinstance(value, list):
        return {
            str(item).strip().lower().replace(" ", "_")
            for item in value
            if str(item).strip()
        }
    return set(default)


def _bool(value, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _policy_from_config(config):
    config = _unwrap_data(config) or {}
    return {
        "eligible_classes": _split_policy_list(
            config.get("eligible_classes"), ISOLATION_KEYWORDS
        ),
        "excluded_classes": _split_policy_list(
            config.get("excluded_classes"),
            {"equipment", "pump", "tank", "vessel", "line", "pipe"},
        ),
        "conditional_classes": _split_policy_list(
            config.get("conditional_classes"), CONDITIONAL_KEYWORDS
        ),
        "max_traversal_depth": int(config.get("max_traversal_depth", 3) or 3),
        "prefer_positive_isolation": _bool(
            config.get("prefer_positive_isolation"), True
        ),
        "include_conditional_candidates": _bool(
            config.get("include_conditional_candidates"), False
        ),
    }


def _candidate_class_text(vertex):
    properties = vertex.get("properties", {}) or {}
    return (
        " ".join(
            [
                _norm(vertex.get("label")),
                _norm(properties.get("entity_class")),
                _norm(properties.get("class")),
                _norm(properties.get("type")),
                _norm(properties.get("component_class")),
            ]
        )
        .replace("-", "_")
        .replace(" ", "_")
    )


def _passes_policy(vertex, keywords, policy):
    text = _candidate_class_text(vertex)
    if any(excluded and excluded in text for excluded in policy["excluded_classes"]):
        return False, "excluded class"
    if not any(keyword in policy["eligible_classes"] for keyword in keywords):
        return False, "not in eligible classes"
    if (
        any(keyword in policy["conditional_classes"] for keyword in keywords)
        and not policy["include_conditional_candidates"]
    ):
        return False, "conditional class excluded by policy"
    traversal_depth = int(vertex.get("traversal_depth") or 99)
    if traversal_depth > policy["max_traversal_depth"]:
        return False, "beyond max traversal depth"
    return True, None


def _energy_type(keywords):
    if any(keyword in ELECTRICAL_KEYWORDS for keyword in keywords):
        return ["electrical"]
    if any(
        keyword in VALVE_KEYWORDS or keyword in BLIND_KEYWORDS or keyword == "flange"
        for keyword in keywords
    ):
        return ["process"]
    return []


def _method(keywords):
    if any(keyword in ELECTRICAL_KEYWORDS for keyword in keywords):
        return "open breaker/disconnect and lock out"
    if any(keyword in BLIND_KEYWORDS for keyword in keywords):
        return "install/verify positive isolation blind"
    if "flange" in keywords:
        return "verify flange break/positive isolation point"
    if any(keyword in VALVE_KEYWORDS for keyword in keywords):
        return "close and lock valve"
    return None


def _candidate_from_vertex(
    equipment_tag,
    source_component_tag,
    source_component_id,
    vertex,
    source_name,
    policy,
):
    properties = vertex.get("properties", {}) or {}
    keywords = _keywords_for(vertex)
    if not keywords:
        return None

    passes_policy, policy_reason = _passes_policy(vertex, keywords, policy)
    if not passes_policy:
        return None

    traversal_depth = int(vertex.get("traversal_depth") or 99)
    tag_number = _tag(properties)
    confidence = 100 - (traversal_depth * 10)
    if any(keyword in VALVE_KEYWORDS for keyword in keywords):
        confidence += 15
    if any(keyword in BLIND_KEYWORDS for keyword in keywords):
        confidence += 20
    if policy["prefer_positive_isolation"] and any(
        keyword in BLIND_KEYWORDS for keyword in keywords
    ):
        confidence += 10
    if source_name == "component direct neighbor":
        confidence += 10

    return {
        "equipment_tag": equipment_tag,
        "source_component_tag": source_component_tag,
        "source_component_id": source_component_id,
        "candidate_id": vertex.get("id"),
        "visual_id": _visual_id(properties, vertex.get("id")),
        "cnvrt_id": _cnvrt_id(properties),
        "unit_name": _first_property(
            properties, ("unit_name", "unit", "drawing_name", "pid_name")
        ),
        "tag_number": tag_number,
        "candidate_label": vertex.get("label"),
        "tag_type": "symbol" if tag_number else "line",
        "energy_type": _energy_type(keywords),
        "isolation_method": _method(keywords),
        "matched_keywords": keywords,
        "policy_match": {
            "eligible": True,
            "reason": policy_reason,
            "prefer_positive_isolation": policy["prefer_positive_isolation"],
        },
        "traversal_depth": traversal_depth,
        "source_name": source_name,
        "confidence": confidence,
        "reason": f"Matched {', '.join(keywords)} at depth {traversal_depth} in {source_name} near {source_component_tag}",
        "properties": properties,
        "property_preview": _property_preview(properties),
        "bbox": _bbox_from_properties(properties),
    }


class IsolationCandidateFinder(Component):
    display_name = "Isolation Candidate Finder"
    description = (
        "Finds deterministic candidate isolation points from equipment boundary data"
    )
    icon = "search"
    name = "IsolationCandidateFinder"

    inputs = [
        DataInput(name="boundary_data", display_name="Equipment Boundary Data"),
        DataInput(
            name="policy_data", display_name="Isolation Policy Data", required=False
        ),
    ]

    outputs = [
        Output(display_name="Candidates", name="candidates", method="find_candidates"),
    ]

    def _boundary_debug(self, data, policy_data):
        equipment_boundaries = data.get("equipment_boundaries", []) or []
        component_boundary_count = 0
        component_count = 0
        equipment_samples = []

        for boundary in equipment_boundaries:
            if not isinstance(boundary, dict):
                continue
            component_boundaries = boundary.get("component_boundaries", []) or []
            components = boundary.get("components", []) or []
            component_boundary_count += len(component_boundaries)
            component_count += len(components)

            equipment = boundary.get("equipment", {}) or {}
            properties = equipment.get("properties", {}) or {}
            equipment_samples.append(
                {
                    "id": equipment.get("id"),
                    "tag": equipment.get("tag") or _tag(properties),
                    "entity_class": properties.get("entity_class"),
                    "component_count": len(components),
                    "component_boundary_count": len(component_boundaries),
                }
            )

        return {
            "boundary_target_mode": data.get("target_mode"),
            "boundary_requested_equipment_tags": data.get("requested_equipment_tags"),
            "boundary_matched_equipment_count": data.get("matched_equipment_count"),
            "boundary_equipment_boundary_count": len(equipment_boundaries),
            "boundary_component_count": component_count,
            "boundary_component_boundary_count": component_boundary_count,
            "boundary_context": data.get("context") or {},
            "boundary_equipment_samples": equipment_samples[:10],
            "policy_max_traversal_depth": policy_data.get("max_traversal_depth"),
            "policy_eligible_classes": policy_data.get("eligible_classes"),
        }

    def find_candidates(self) -> Data:
        data = _unwrap_data(self.boundary_data) or {}
        if data.get("error"):
            return Data(
                value={"error": True, "message": data.get("message"), "candidates": []}
            )

        policy_data = _unwrap_data(getattr(self, "policy_data", None)) or {}
        policy = _policy_from_config(policy_data)
        explicit_job_name = _context_job_name(data, policy_data)

        raw_candidates = []
        skipped = []

        for boundary in data.get("equipment_boundaries", []):
            equipment = boundary.get("equipment", {}) or {}
            equipment_props = equipment.get("properties", {}) or {}
            equipment_tag = (
                equipment.get("tag")
                or _tag(equipment_props)
                or str(equipment.get("id"))
            )

            for component_boundary in boundary.get("component_boundaries", []):
                component = component_boundary.get("component", {}) or {}
                component_props = component.get("properties", {}) or {}
                source_component_id = component.get("id")
                source_component_tag = (
                    component.get("tag")
                    or _tag(component_props)
                    or str(component.get("id"))
                )

                sources = []
                sources.extend(
                    ("component direct neighbor", vertex)
                    for vertex in _as_list(component_boundary.get("direct_neighbors"))
                )
                sources.extend(
                    ("component traversal sample", vertex)
                    for vertex in _as_list(component_boundary.get("traversal_sample"))
                )

                for source_name, vertex in sources:
                    if not isinstance(vertex, dict):
                        skipped.append(
                            {
                                "source_component_tag": source_component_tag,
                                "reason": "not a dict",
                                "value": str(vertex)[:200],
                            }
                        )
                        continue
                    candidate = _candidate_from_vertex(
                        equipment_tag,
                        source_component_tag,
                        source_component_id,
                        vertex,
                        source_name,
                        policy,
                    )
                    if candidate:
                        raw_candidates.append(candidate)
                    else:
                        skipped.append(
                            {
                                "source_component_tag": source_component_tag,
                                "reason": "no isolation keyword",
                                "label": vertex.get("label"),
                                "tag": _tag(vertex.get("properties", {}) or {}),
                                "properties": vertex.get("properties", {}) or {},
                            }
                        )

        inferred_job_name = (
            _infer_job_name_from_candidates(raw_candidates)
            if not explicit_job_name
            else None
        )
        job_name = explicit_job_name or inferred_job_name
        context_filtered_candidates, context_filtered = _filter_candidates_for_context(
            raw_candidates, job_name
        )

        boundary_selected_candidates, source_selection_samples = _select_boundary_candidates(
            context_filtered_candidates
        )

        merged_by_identity = {}
        for candidate in boundary_selected_candidates:
            identity = (
                candidate.get("equipment_tag"),
                _norm(
                    candidate.get("visual_id")
                    or candidate.get("cnvrt_id")
                    or candidate.get("candidate_id")
                ),
            )
            if identity in merged_by_identity:
                _merge_candidate(merged_by_identity[identity], candidate)
                continue
            candidate["source_paths"] = [_path_trace(candidate)]
            candidate["source_path_count"] = 1
            merged_by_identity[identity] = candidate

        deduped = list(merged_by_identity.values())

        deduped.sort(key=_candidate_sort_key)

        ranked = []
        for rank, candidate in enumerate(deduped[:MAX_TOTAL_CANDIDATES], start=1):
            candidate["path_selection"] = {
                "mode": "nearest_isolation_candidate_per_source_component",
                "primary_source_component_tag": candidate.get("source_component_tag"),
                "primary_source_component_id": candidate.get("source_component_id"),
                "selected_depth": candidate.get("traversal_depth"),
                "rank": rank,
                "source_path_count": candidate.get("source_path_count", 1),
            }
            ranked.append(candidate)

        boundary_debug = self._boundary_debug(data, policy_data)

        return Data(
            value={
                "error": False,
                "total_candidates": len(ranked),
                "all_candidates_before_ranking": len(deduped),
                "candidates": ranked,
                "context": data.get("context") or {},
                "debug": {
                    "candidate_finder_mode": "nearest_boundary_candidate_per_source_component",
                    **boundary_debug,
                    "isolation_policy": {
                        "eligible_classes": sorted(policy["eligible_classes"]),
                        "excluded_classes": sorted(policy["excluded_classes"]),
                        "conditional_classes": sorted(policy["conditional_classes"]),
                        "max_traversal_depth": policy["max_traversal_depth"],
                        "prefer_positive_isolation": policy[
                            "prefer_positive_isolation"
                        ],
                        "include_conditional_candidates": policy[
                            "include_conditional_candidates"
                        ],
                    },
                    "job_name_context": job_name,
                    "job_name_context_source": "explicit"
                    if explicit_job_name
                    else "inferred_from_candidate_unit_name"
                    if inferred_job_name
                    else None,
                    "explicit_job_name_context": explicit_job_name,
                    "inferred_job_name_context": inferred_job_name,
                    "raw_candidate_count_before_context_filter": len(raw_candidates),
                    "context_filtered_candidate_count": len(context_filtered),
                    "context_filtered_candidate_samples": context_filtered[:25],
                    "raw_candidate_count_before_dedupe": len(
                        context_filtered_candidates
                    ),
                    "source_component_selection_mode": "nearest_valid_isolation_candidate_per_nozzle",
                    "source_component_count_with_candidates": len(
                        source_selection_samples
                    ),
                    "source_component_selection_samples": source_selection_samples[:25],
                    "boundary_selected_candidate_count_before_dedupe": len(
                        boundary_selected_candidates
                    ),
                    "deduped_candidate_count": len(deduped),
                    "merged_duplicate_candidate_count": len(boundary_selected_candidates)
                    - len(deduped),
                    "returned_candidate_count": len(ranked),
                    "source_path_trace_samples": [
                        {
                            "candidate_id": candidate.get("candidate_id"),
                            "visual_id": candidate.get("visual_id"),
                            "source_path_count": candidate.get("source_path_count"),
                            "source_paths": candidate.get("source_paths", [])[:8],
                        }
                        for candidate in ranked[:10]
                    ],
                    "skipped_count": len(skipped),
                    "skipped_samples": skipped[:25],
                },
            }
        )
