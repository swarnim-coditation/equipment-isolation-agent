"""Downstream impact analysis over the HILT process-line graph.

The analyzer is deterministic: it uses only parsed HILT topology and flow
direction, treats selected isolation barriers as closed, and reports structured
warnings for reachable downstream equipment, instruments, endpoints, and relief
context devices. LLMs may summarize this result, but must not invent reachability.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from api_client import Plant360Client
from domain.enums import FlowRole, ImpactSeverity
from domain.models import BBox, DownstreamImpactWarning


PROCESS_LINE_CLASSES = {
    "primary_process_line",
    "secondary_process_line",
    "main_process_line",
    "process_line",
}

MAX_IMPACT_HOPS = 24

LIKELY = ImpactSeverity.LIKELY.value
POSSIBLE = ImpactSeverity.POSSIBLE.value

EQUIPMENT_CLASSES = {
    "vessel",
    "centrifugal_pump",
    "pump",
    "tank",
    "heat_exchanger",
    "equipment",
}
ENDPOINT_CLASSES = {"off_or_on_page_connector", "sta"}
RELIEF_CONTEXT_CLASSES = {"open_vent"}
INSTRUMENT_PREFIXES = {
    "FIC",
    "FI",
    "LIC",
    "LI",
    "PIC",
    "PI",
    "TC",
    "TI",
    "LAH",
    "LAL",
}


@dataclass(frozen=True)
class ProcessGraph:
    node_by_id: dict[str, dict]
    tag_to_ids: dict[str, set[str]]
    undirected: dict[str, set[str]]
    likely: dict[str, set[str]]


def analyze_downstream_impact(validation_data: dict, config, max_hops: int = MAX_IMPACT_HOPS) -> dict:
    """Fetch HILT for the resolved job and analyze selected isolation barriers.

    API/job failures are represented as ``status: unavailable`` rather than
    raising, so downstream impact does not change isolation assurance.
    """
    job_id = getattr(config, "resolved_job_id", "") or ""
    if not job_id:
        return {
            "status": "unavailable",
            "warnings": [],
            "error": "missing_job_id",
            "debug": {"start_count": 0, "warning_count": 0, "unknown_flow_path_count": 0},
        }
    client = Plant360Client(config.api)
    try:
        hilt_payload = client.hilt_graph(job_id)
    except Exception as exc:
        return {
            "status": "unavailable",
            "warnings": [],
            "error": str(exc),
            "debug": {"start_count": 0, "warning_count": 0, "unknown_flow_path_count": 0},
        }
    y_flip = None
    try:
        stlm_payload = client.stlm_symbols(job_id)
        y_flip = _calibrate_hilt_yflip(
            ((hilt_payload.get("hilt_graph") or {}) if isinstance(hilt_payload, dict) else {}).get("nodes") or [],
            _extract_symbols(stlm_payload),
        )
    except Exception:
        y_flip = None
    return analyze_hilt_downstream_impact(
        hilt_payload,
        validation_data,
        config.equipment_tag,
        max_hops=max_hops,
        y_flip=y_flip,
    )


def analyze_hilt_downstream_impact(
    hilt_payload: dict,
    validation_data: dict,
    equipment_tag: str = "",
    max_hops: int = MAX_IMPACT_HOPS,
    y_flip: float | None = None,
) -> dict:
    graph = _build_process_graph(hilt_payload)
    if not graph.node_by_id:
        return {
            "status": "unavailable",
            "warnings": [],
            "error": "missing_hilt_graph",
            "debug": {"start_count": 0, "warning_count": 0, "unknown_flow_path_count": 0},
        }

    barrier_candidates = _selected_barrier_candidates(validation_data)
    closed_barriers = _candidate_node_ids(barrier_candidates, graph)
    warnings: list[dict] = []
    start_count = 0

    for candidate in barrier_candidates:
        candidate_node = _candidate_node_id(candidate, graph)
        if not candidate_node:
            continue
        start_nodes = _downstream_starts(candidate, candidate_node, graph)
        if not start_nodes:
            continue
        for start_node, start_severity in start_nodes:
            start_count += 1
            found = _traverse_from_start(
                start_node,
                start_severity,
                graph,
                closed_barriers,
                equipment_tag=equipment_tag,
                max_hops=max_hops,
            )
            for item in found:
                warnings.append(_warning(candidate, item, y_flip=y_flip))

    warnings = _dedupe_warnings(warnings)
    unknown_flow_path_count = sum(1 for item in warnings if item.get("severity") == POSSIBLE)
    warnings.sort(
        key=lambda item: (
            0 if item.get("severity") == LIKELY else 1,
            int(item.get("path_hops") or 999),
            str(item.get("source_tag") or ""),
            str(item.get("affected_tag") or ""),
        )
    )
    return {
        "status": "completed",
        "warnings": warnings,
        "debug": {
            "start_count": start_count,
            "warning_count": len(warnings),
            "unknown_flow_path_count": unknown_flow_path_count,
            "closed_barrier_count": len(closed_barriers),
            "max_hops": max_hops,
            "hilt_y_flip_calibrated": y_flip,
        },
    }


def _build_process_graph(hilt_payload: dict) -> ProcessGraph:
    hilt_graph = hilt_payload.get("hilt_graph") if isinstance(hilt_payload, dict) else None
    if not isinstance(hilt_graph, dict):
        return ProcessGraph({}, {}, {}, {})
    nodes = hilt_graph.get("nodes") or []
    links = hilt_graph.get("links") or []

    node_by_id: dict[str, dict] = {}
    tag_to_ids: dict[str, set[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        payload = node.get("payload") or {}
        node_id = str(node.get("id") or payload.get("id") or payload.get("source_id") or "")
        if not node_id:
            continue
        node_by_id[node_id] = node
        for value in (node_id, payload.get("id"), payload.get("source_id"), _node_tag(node)):
            key = _norm(value)
            if key:
                tag_to_ids.setdefault(key, set()).add(node_id)

    undirected: dict[str, set[str]] = {}
    likely: dict[str, set[str]] = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        payload = link.get("payload") or {}
        if _norm(payload.get("entity_class")) not in PROCESS_LINE_CLASSES:
            continue
        source = str(link.get("source") or payload.get("from") or "")
        target = str(link.get("target") or payload.get("to") or "")
        if not source or not target:
            continue
        undirected.setdefault(source, set()).add(target)
        undirected.setdefault(target, set()).add(source)

        if str(payload.get("flow") or "").upper() == "ONE_WAY":
            arrow_edges = []
            for arrow in payload.get("arrow") or []:
                if not isinstance(arrow, dict):
                    continue
                from_id = str(arrow.get("from_id") or "")
                to_id = str(arrow.get("to_id") or "")
                if from_id and to_id:
                    arrow_edges.append((from_id, to_id))
            if not arrow_edges:
                arrow_edges = [(source, target)]
            for from_id, to_id in arrow_edges:
                likely.setdefault(from_id, set()).add(to_id)

    return ProcessGraph(node_by_id=node_by_id, tag_to_ids=tag_to_ids, undirected=undirected, likely=likely)


def _selected_barrier_candidates(validation_data: dict) -> list[dict]:
    candidates = validation_data.get("candidates", []) or []
    validation = validation_data.get("isolation_validation") or {}
    barrier_ids = {str(value) for value in validation.get("barrier_candidate_ids") or []}
    if barrier_ids:
        return [candidate for candidate in candidates if str(candidate.get("candidate_id")) in barrier_ids]
    return list(candidates)


def _candidate_node_ids(candidates: list[dict], graph: ProcessGraph) -> set[str]:
    return {node_id for candidate in candidates for node_id in [_candidate_node_id(candidate, graph)] if node_id}


def _candidate_node_id(candidate: dict, graph: ProcessGraph) -> str | None:
    for value in (
        candidate.get("candidate_id"),
        candidate.get("visual_id"),
        candidate.get("visual_node_id"),
        candidate.get("cnvrt_id"),
        candidate.get("tag_number"),
    ):
        match = _single_graph_id(value, graph)
        if match:
            return match
    return None


def _source_node_ids(candidate: dict, graph: ProcessGraph) -> list[str]:
    values = [
        candidate.get("source_component_id"),
        candidate.get("source_visual_id"),
        candidate.get("source_visual_node_id"),
        candidate.get("source_component_tag"),
    ]
    for path in candidate.get("source_paths") or []:
        values.extend((path.get("source_component_id"), path.get("source_component_tag")))
    result = []
    seen = set()
    for value in values:
        for node_id in graph.tag_to_ids.get(_norm(value), set()):
            if node_id not in seen:
                seen.add(node_id)
                result.append(node_id)
    return result


def _downstream_starts(candidate: dict, candidate_node: str, graph: ProcessGraph) -> list[tuple[str, str]]:
    source_neighbors = _source_side_neighbors(candidate, candidate_node, graph)
    role = str(candidate.get("source_flow_role") or "").lower()
    starts = []
    for neighbor in graph.undirected.get(candidate_node, set()):
        if neighbor in graph.likely.get(candidate_node, set()):
            starts.append((neighbor, LIKELY))
        elif role == FlowRole.INLET.value and neighbor in source_neighbors:
            starts.append((neighbor, POSSIBLE))
        elif neighbor not in source_neighbors:
            starts.append((neighbor, POSSIBLE))
    if not starts:
        if role == FlowRole.INLET.value and source_neighbors:
            starts.extend((neighbor, POSSIBLE) for neighbor in source_neighbors)
        else:
            external = [neighbor for neighbor in graph.undirected.get(candidate_node, set()) if neighbor not in source_neighbors]
            starts.extend((neighbor, POSSIBLE) for neighbor in (external or list(graph.undirected.get(candidate_node, set()))))

    return _dedupe_starts(starts)


def _source_side_neighbors(candidate: dict, candidate_node: str, graph: ProcessGraph) -> set[str]:
    neighbors = set()
    for source_node in _source_node_ids(candidate, graph):
        path = _shortest_path(source_node, candidate_node, graph.undirected, max_hops=12)
        if path and len(path) >= 2:
            neighbors.add(path[-2])
    return neighbors


def _shortest_path(start: str, target: str, adj: dict[str, set[str]], max_hops: int) -> list[str]:
    if start == target:
        return [start]
    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if len(path) > max_hops + 1:
            continue
        for neighbor in adj.get(node, set()):
            if neighbor in seen:
                continue
            new_path = path + [neighbor]
            if neighbor == target:
                return new_path
            seen.add(neighbor)
            queue.append((neighbor, new_path))
    return []


def _traverse_from_start(
    start_node: str,
    start_severity: str,
    graph: ProcessGraph,
    closed_barriers: set[str],
    *,
    equipment_tag: str,
    max_hops: int,
) -> list[dict]:
    found = []
    queue = deque([(start_node, 1, start_severity)])
    seen: dict[str, str] = {start_node: start_severity}
    while queue:
        node_id, hops, severity = queue.popleft()
        if node_id in closed_barriers:
            continue
        node = graph.node_by_id.get(node_id)
        classification = _impact_classification(node)
        if classification and _norm(_node_tag(node)) != _norm(equipment_tag):
            found.append(
                {
                    "node_id": node_id,
                    "node": node,
                    "severity": severity,
                    "path_hops": hops,
                    **classification,
                }
            )
            if classification["affected_type"] in {"equipment", "endpoint"}:
                continue
        if hops >= max_hops:
            continue
        for neighbor in graph.undirected.get(node_id, set()):
            if neighbor in closed_barriers:
                continue
            next_severity = severity if neighbor in graph.likely.get(node_id, set()) else POSSIBLE
            if _seen_with_equal_or_better_confidence(seen, neighbor, next_severity):
                continue
            seen[neighbor] = next_severity
            queue.append((neighbor, hops + 1, next_severity))
    return found


def _impact_classification(node: dict | None) -> dict | None:
    if not node:
        return None
    payload = node.get("payload") or {}
    entity_class = _norm(payload.get("entity_class"))
    entity_type = _norm(payload.get("entity_type"))
    tag = _node_tag(node)
    prefix = _tag_prefix(tag)

    if entity_class in ENDPOINT_CLASSES:
        return {
            "affected_class": payload.get("entity_class"),
            "affected_type": "endpoint",
            "impact_type": "off_page_or_terminal_path_affected",
        }
    if entity_class in RELIEF_CONTEXT_CLASSES:
        return {
            "affected_class": payload.get("entity_class"),
            "affected_type": "relief_context",
            "impact_type": "relief_or_vent_context_affected",
        }
    if entity_type == "equipment" or entity_class in EQUIPMENT_CLASSES:
        return {
            "affected_class": payload.get("entity_class") or payload.get("entity_type"),
            "affected_type": "equipment",
            "impact_type": "loss_of_feed_or_pressure",
        }
    if prefix in INSTRUMENT_PREFIXES or "instrument" in entity_class or entity_type in {"loop", "instrument"}:
        return {
            "affected_class": payload.get("entity_class") or prefix or payload.get("entity_type"),
            "affected_type": "instrument_or_control_loop",
            "impact_type": "instrument_reading_or_control_affected",
        }
    return None


def _warning(candidate: dict, item: dict, y_flip: float | None = None) -> dict:
    node = item.get("node") or {}
    affected_tag = _node_tag(node) or item.get("node_id")
    severity = ImpactSeverity(str(item["severity"]))
    warning = DownstreamImpactWarning(
        severity=severity,
        source_candidate_id=str(candidate.get("candidate_id") or ""),
        source_tag=_source_tag(candidate),
        affected_tag=str(affected_tag or ""),
        affected_id=str(item.get("node_id") or ""),
        affected_bbox=BBox.from_any(_node_bbox(node, y_flip)),
        affected_class=str(item.get("affected_class") or ""),
        affected_type=str(item.get("affected_type") or ""),
        impact_type=str(item.get("impact_type") or ""),
        basis=(
            "reachable via one-way/arrow-grounded HILT process-line graph"
            if severity == ImpactSeverity.LIKELY
            else "reachable via HILT process-line graph with unknown or weak flow direction"
        ),
        path_hops=int(item.get("path_hops") or 0),
    ).to_dict()
    warning["source_candidate_tag"] = candidate.get("tag_number")
    return warning


def _source_tag(candidate: dict) -> str:
    if candidate.get("source_component_tag"):
        return str(candidate.get("source_component_tag"))
    for path in candidate.get("source_paths") or []:
        if path.get("source_component_tag"):
            return str(path.get("source_component_tag"))
    return ""


def _dedupe_starts(starts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged = {}
    for node_id, severity in starts:
        if node_id not in merged or _severity_rank(severity) < _severity_rank(merged[node_id]):
            merged[node_id] = severity
    return list(merged.items())


def _dedupe_warnings(warnings: list[dict]) -> list[dict]:
    merged = {}
    for warning in warnings:
        key = (
            warning.get("source_candidate_id"),
            _norm(warning.get("affected_tag")),
            warning.get("affected_type"),
        )
        if key not in merged:
            merged[key] = warning
            continue
        existing = merged[key]
        if (
            _severity_rank(warning.get("severity")) < _severity_rank(existing.get("severity"))
            or int(warning.get("path_hops") or 999) < int(existing.get("path_hops") or 999)
        ):
            merged[key] = warning
    return list(merged.values())


def _seen_with_equal_or_better_confidence(seen: dict[str, str], node_id: str, severity: str) -> bool:
    previous = seen.get(node_id)
    if previous is None:
        return False
    return _severity_rank(previous) <= _severity_rank(severity)


def _severity_rank(severity: str | None) -> int:
    return 0 if severity == LIKELY else 1


def _single_graph_id(value: Any, graph: ProcessGraph) -> str | None:
    if value is None:
        return None
    key = str(value)
    if key in graph.node_by_id:
        return key
    matches = graph.tag_to_ids.get(_norm(value)) or set()
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _impact_tag_from_payload(payload: dict) -> str | None:
    return _hilt_text_value(payload.get("text")) or _attr(payload.get("attributes"), "tag")


def _node_tag(node: dict | None) -> str:
    if not node:
        return ""
    payload = node.get("payload") or {}
    tag = _impact_tag_from_payload(payload)
    return str(tag or payload.get("tag") or node.get("id") or "").strip()


def _node_bbox(node: dict | None, y_flip: float | None = None) -> list:
    if not node:
        return []
    payload = node.get("payload") or {}
    location = payload.get("bounding_box_location") or {}
    width = payload.get("bounding_box_width")
    height = payload.get("bounding_box_height")
    if location.get("x") is None or location.get("y") is None or width is None or height is None:
        return []
    cx = float(location.get("x"))
    cy = float(location.get("y"))
    w = float(width)
    h = float(height)
    x = cx - w / 2.0
    y = (y_flip - cy - h / 2.0) if y_flip is not None else (cy - h / 2.0)
    return [int(round(x)), int(round(y)), int(round(w)), int(round(h))]


def _attr(attributes, name: str) -> str | None:
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        if str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return None


def _hilt_text_value(items) -> str | None:
    values = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value not in (None, "", []):
            values.append(str(value))
    return ", ".join(values) if values else None


def _extract_symbols(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "symbols", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_symbols(value)
            if nested:
                return nested
    symbol_json = payload.get("symbol_json")
    if isinstance(symbol_json, list):
        return symbol_json
    if isinstance(symbol_json, dict):
        symbols = []
        for key, value in symbol_json.items():
            if isinstance(value, dict):
                symbol = dict(value)
                symbol.setdefault("uuid", key)
                symbols.append(symbol)
        return symbols
    return []


def _symbol_attr(symbol, name):
    target = str(name or "").strip().lower().replace("_", " ")
    for attr in symbol.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        attr_name = str(attr.get("name") or "").strip().lower().replace("_", " ")
        if attr_name == target:
            return attr.get("value")
    return None


def _symbol_bbox(symbol):
    if isinstance(symbol.get("bbox"), list) and len(symbol["bbox"]) == 4:
        return [int(round(float(value))) for value in symbol["bbox"]]
    keys = ("orig_x", "orig_y", "orig_bbox_width", "orig_bbox_height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    keys = ("x", "y", "width", "height")
    if all(symbol.get(key) is not None for key in keys):
        return [int(round(float(symbol[key]))) for key in keys]
    return []


def _calibrate_hilt_yflip(hilt_nodes, symbols):
    if not symbols:
        return None
    stlm_by_id = {}
    for sym in symbols:
        for key in ("source_id", "uuid", "id"):
            value = sym.get(key)
            if value:
                stlm_by_id[str(value).lower()] = sym
    stlm_by_tag = {}
    for sym in symbols:
        tag = sym.get("tag") or _symbol_attr(sym, "tag")
        if tag:
            stlm_by_tag[_norm(tag)] = sym

    heights = []
    for node in hilt_nodes:
        payload = node.get("payload") or {}
        loc = payload.get("bounding_box_location") or {}
        if loc.get("y") is None:
            continue
        sym = stlm_by_id.get(str(node.get("id") or "").lower())
        if sym is None:
            tag = _symbol_attr(payload, "tag")
            sym = stlm_by_tag.get(_norm(tag)) if tag else None
        if sym is None:
            continue
        sb = _symbol_bbox(sym)
        if not sb:
            continue
        stlm_center_y = sb[1] + sb[3] / 2.0
        heights.append(stlm_center_y + float(loc.get("y")))
    if not heights:
        return None
    return sum(heights) / len(heights)


def _tag_prefix(value: str) -> str:
    result = []
    for char in str(value or "").strip().upper():
        if char.isalpha():
            result.append(char)
            continue
        break
    return "".join(result)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")
