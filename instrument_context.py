"""Deterministic instrument semantics for isolation procedures.

This module classifies P&ID instruments into SOP context. It is deliberately
advisory: instrument readings can add preparation, stored-energy, verification,
and restoration steps, but they do not upgrade ``assurance_status``.
"""
from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

from api_client import Plant360Client
from domain.hilt_geometry import extract_symbols as _extract_symbols
from domain.hilt_geometry import symbol_attr as _symbol_attr
from domain.hilt_geometry import symbol_bbox as _symbol_bbox
from domain.topology import PROCESS_LINE_CLASSES, normalize_tag


CATALOG_PATH = Path(__file__).with_name("instrument_catalog.json")
MAX_RELEVANCE_HOPS = 10
INSTRUMENT_LINE_CLASSES = {
    "piping_to_instrument_line",
    "instrument_signal_line",
    "electrical_signal_line",
    "companion_line",
}
RELEVANCE_LINE_CLASSES = PROCESS_LINE_CLASSES | INSTRUMENT_LINE_CLASSES


def analyze_instrument_context(validation_data: dict, config, hilt_payload: dict | None = None, stlm_payload: dict | None = None) -> dict:
    """Return deterministic instrument context for the selected equipment.

    The default policy is advisory-only. Failures are returned as structured
    unavailable results so validation and payload writing continue.
    """
    catalog = load_instrument_catalog()
    hilt_payload = hilt_payload if hilt_payload is not None else validation_data.get("_hilt_payload")
    stlm_payload = stlm_payload if stlm_payload is not None else validation_data.get("_stlm_payload")
    job_id = config.resolved_job_id
    if not job_id and (hilt_payload is None and stlm_payload is None):
        return _unavailable("missing_job_id")

    if hilt_payload is None or stlm_payload is None:
        client = Plant360Client(config.api)
        try:
            if hilt_payload is None:
                hilt_payload = client.hilt_graph(job_id)
            if stlm_payload is None:
                stlm_payload = client.stlm_symbols(job_id)
        except Exception as exc:
            return _unavailable(str(exc))

    result = analyze_hilt_instrument_context(
        hilt_payload or {},
        stlm_payload or {},
        equipment_tag=config.equipment_tag,
        validation_data=validation_data,
        catalog=catalog,
    )
    return result


def analyze_hilt_instrument_context(
    hilt_payload: dict,
    stlm_payload: dict | None,
    equipment_tag: str,
    validation_data: dict | None = None,
    catalog: dict | None = None,
) -> dict:
    catalog = catalog or load_instrument_catalog()
    graph = (hilt_payload.get("hilt_graph") or {}) if isinstance(hilt_payload, dict) else {}
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    if not isinstance(graph, dict) or not nodes:
        return _unavailable("missing_hilt_graph")

    stlm_instruments = _stlm_instruments(stlm_payload or {}, catalog)
    stlm_by_id = _stlm_instruments_by_id(stlm_instruments)
    node_by_id = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id:
            node_by_id[node_id] = node

    target_nodes = _target_node_ids(nodes, equipment_tag)
    selected_barriers = {
        str(value)
        for candidate in ((validation_data or {}).get("candidates") or [])
        for value in (candidate.get("candidate_id"), candidate.get("visual_id"))
        if value
    }
    target_nodes.update(node_id for node_id in selected_barriers if node_id in node_by_id)
    if not target_nodes:
        return _completed([], [], {"reason": "no_target_nodes", "hilt_node_count": len(nodes), "hilt_link_count": len(links)})

    adjacency = _adjacency(links)
    distances = _distances(target_nodes, adjacency, max_hops=MAX_RELEVANCE_HOPS)
    instruments = []
    seen = set()
    for node_id, hops in sorted(distances.items(), key=lambda item: (item[1], item[0])):
        node = node_by_id.get(node_id)
        if not node:
            continue
        parsed = _instrument_from_hilt_node(node, catalog, stlm_by_id)
        if not parsed:
            continue
        key = parsed["id"]
        if key in seen:
            continue
        seen.add(key)
        parsed["relevance"] = "hilt_connected"
        parsed["relevance_basis"] = "reachable from selected equipment or selected isolation section through HILT topology"
        parsed["path_hops"] = hops
        instruments.append(parsed)
    for parsed in _target_adjacent_stlm_instruments(stlm_instruments, validation_data or {}, seen):
        seen.add(parsed["id"])
        instruments.append(parsed)

    checks = _build_checks(instruments)
    return _completed(
        instruments,
        checks,
        {
            "hilt_node_count": len(nodes),
            "hilt_link_count": len(links),
            "target_node_count": len(target_nodes),
            "instrument_count": len(instruments),
            "evidence_policy": (catalog.get("defaults") or {}).get("evidence_policy") or "advisory_only",
            "relevance_scope": (catalog.get("defaults") or {}).get("relevance_scope") or "hilt_connected",
        },
    )


def load_instrument_catalog(path: str | Path | None = None) -> dict:
    source = Path(path) if path else CATALOG_PATH
    return json.loads(source.read_text(encoding="utf-8"))


def parse_instrument_tag(value: str, catalog: dict | None = None) -> dict:
    catalog = catalog or load_instrument_catalog()
    raw = str(value or "").strip()
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    prefixes = sorted((catalog.get("prefixes") or {}).keys(), key=len, reverse=True)
    for prefix in prefixes:
        if compact.startswith(prefix):
            entry = catalog["prefixes"][prefix]
            return {
                "raw_tag": raw,
                "normalized_tag": raw.upper(),
                "prefix": prefix,
                "loop": compact[len(prefix):],
                "name": entry.get("name"),
                "measured_variable": entry.get("measured_variable"),
                "instrument_type": entry.get("instrument_type"),
                "sop_uses": list(entry.get("sop_uses") or []),
                "verification_note": entry.get("verification_note"),
            }
    return {}


def _completed(instruments, checks, debug):
    return {
        "status": "completed",
        "policy": "advisory_only",
        "instruments": instruments,
        "checks": checks,
        "debug": debug,
    }


def _unavailable(error):
    return {
        "status": "unavailable",
        "policy": "advisory_only",
        "error": str(error),
        "instruments": [],
        "checks": {},
        "debug": {"instrument_count": 0},
    }


def _build_checks(instruments):
    checks = {
        "before_isolation": [],
        "stored_energy_relief": [],
        "verification_before_work": [],
        "restoration_reenergization": [],
        "control_state": [],
        "alarm_context": [],
    }
    for instrument in instruments:
        tag = instrument.get("tag") or instrument.get("id")
        variable = instrument.get("measured_variable")
        uses = set(instrument.get("sop_uses") or [])
        if "pre_isolation_reading" in uses:
            checks["before_isolation"].append(
                _check(
                    instrument,
                    "pre_isolation_reading",
                    f"Record baseline {variable} reading at {tag} before shutdown/isolation.",
                )
            )
        if "stored_energy_monitoring" in uses:
            if variable == "pressure":
                action = f"Monitor pressure trend at {tag} while bleeding/venting."
            elif variable == "level":
                action = f"Monitor level trend at {tag} while draining/emptying."
            elif variable == "temperature":
                action = f"Monitor temperature trend at {tag} during cooldown."
            else:
                action = f"Monitor {tag} during stored-energy relief."
            checks["stored_energy_relief"].append(_check(instrument, "stored_energy_monitoring", action))
        if "verification_support" in uses:
            checks["verification_before_work"].append(
                _check(
                    instrument,
                    "verification_support",
                    f"Use {tag} as supporting indication only; field-verify zero energy by an approved method.",
                )
            )
        if "restoration_monitoring" in uses:
            checks["restoration_reenergization"].append(
                _check(
                    instrument,
                    "restoration_monitoring",
                    f"Before lock removal and after controlled re-energization, compare {tag} against expected safe operating range.",
                )
            )
        if "control_state" in uses:
            checks["control_state"].append(
                _check(
                    instrument,
                    "control_state",
                    f"Place associated controller {tag} in safe/neutral/manual state per site procedure before isolation changes.",
                )
            )
        if "alarm_context" in uses:
            checks["alarm_context"].append(
                _check(instrument, "alarm_context", f"Confirm alarm {tag} is understood and monitored during restoration.")
            )
    return {key: value for key, value in checks.items() if value}


def _check(instrument, use, action):
    semantics = _semantic_details(instrument, use)
    return {
        "instrument_id": instrument.get("id"),
        "tag": instrument.get("tag"),
        "prefix": instrument.get("prefix"),
        "measured_variable": instrument.get("measured_variable"),
        "instrument_type": instrument.get("instrument_type"),
        "use": use,
        "action": action,
        **semantics,
        "basis": "configured instrument semantics; advisory-only",
    }


def _semantic_details(instrument, use):
    variable = instrument.get("measured_variable")
    instrument_type = instrument.get("instrument_type")
    if use == "control_state":
        return {
            "purpose": "Prevent automatic control action from fighting the isolation or re-energizing a controlled path.",
            "interpretation": "Controller indication/output shows the control loop state, not physical energy isolation.",
            "acceptance_criteria": "Controller is in the site-required safe/manual/neutral mode and its output is not commanding unsafe valve, pump, or process movement.",
            "limitation": "A controller is not an energy-isolating device under OSHA 1910.147.",
        }
    if use == "alarm_context":
        return {
            "purpose": "Use alarms as abnormal-condition awareness during isolation and restoration.",
            "interpretation": "Alarm state indicates an alarm condition or cleared alarm, not isolation.",
            "acceptance_criteria": "Alarm state is understood, expected, and monitored per site procedure before restoration.",
            "limitation": "Alarm state is supporting context only.",
        }
    if variable == "pressure":
        if use == "stored_energy_monitoring":
            return {
                "purpose": "Confirm pressure is being relieved and detect pressure reaccumulation.",
                "interpretation": "A falling pressure trend supports depressurization; stable zero gauge pressure or a site-defined safe pressure limit supports a depressurized state.",
                "acceptance_criteria": "Reading is at zero gauge pressure or the configured safe threshold, remains stable for the required hold period, and no reaccumulation is observed.",
                "limitation": _instrument_limitation(instrument_type, "Pressure indication supports depressurization but must be confirmed by an approved field verification method."),
            }
        if use == "verification_support":
            return {
                "purpose": "Support zero-energy verification for pressure energy.",
                "interpretation": "Zero gauge pressure or pressure below the site-defined safe limit supports, but does not alone prove, zero pressure energy.",
                "acceptance_criteria": "Use only with an approved bleed/test point or site-approved field verification method.",
                "limitation": _instrument_limitation(instrument_type, "Pressure indication alone does not prove isolation."),
            }
        if use == "restoration_monitoring":
            return {
                "purpose": "Detect abnormal pressure during controlled re-energization.",
                "interpretation": "Pressure should rise only as expected for the approved startup/restoration sequence.",
                "acceptance_criteria": "Pressure remains within the site-defined safe operating band with no unexpected rise or alarm.",
                "limitation": "Operating limits are site-specific and are not derivable from the P&ID.",
            }
        return {
            "purpose": "Establish initial pressure condition before isolation.",
            "interpretation": "Baseline pressure helps compare depressurization trend after shutdown and relief.",
            "acceptance_criteria": "Record value, units, timestamp, and local/remote source before changing isolation state.",
            "limitation": "Baseline reading is context, not proof of isolation.",
        }
    if variable == "level":
        if use == "stored_energy_monitoring":
            return {
                "purpose": "Confirm inventory is draining/emptying and detect unexpected refill.",
                "interpretation": "A falling level trend supports drain-down; low/empty level supports reduced liquid inventory.",
                "acceptance_criteria": "Reading reaches the site-defined empty/low-safe level and remains stable with no unexpected refill.",
                "limitation": _instrument_limitation(instrument_type, "Level indication does not prove zero pressure or complete energy isolation."),
            }
        if use == "verification_support":
            return {
                "purpose": "Support inventory/removal verification before work.",
                "interpretation": "Low or empty level supports that liquid inventory has been removed from the measured section.",
                "acceptance_criteria": "Use as supporting context with approved field verification for zero energy and trapped pressure.",
                "limitation": "A level reading cannot prove depressurization; trapped pressure may still exist.",
            }
        if use == "restoration_monitoring":
            return {
                "purpose": "Detect abnormal filling, draining, or level response during restoration.",
                "interpretation": "Level should change only as expected for the approved restoration/startup sequence.",
                "acceptance_criteria": "Level remains within the site-defined safe operating band with no unexpected rise, drop, or alarm.",
                "limitation": "Operating limits are site-specific and are not derivable from the P&ID.",
            }
        return {
            "purpose": "Establish initial liquid inventory condition before isolation.",
            "interpretation": "Baseline level helps compare drain-down trend after shutdown and draining.",
            "acceptance_criteria": "Record value, units/percent if available, timestamp, and local/remote source before changing isolation state.",
            "limitation": "Baseline reading is context, not proof of isolation.",
        }
    if variable == "temperature":
        if use == "stored_energy_monitoring":
            return {
                "purpose": "Confirm thermal energy is reducing and detect unsafe heat/cold conditions.",
                "interpretation": "A temperature trend toward ambient or a site-defined safe temperature supports cooldown/warmup.",
                "acceptance_criteria": "Temperature reaches the configured safe work limit and remains stable.",
                "limitation": _instrument_limitation(instrument_type, "Temperature indication does not prove process isolation."),
            }
        return {
            "purpose": "Track thermal condition before and after isolation.",
            "interpretation": "Temperature provides thermal-energy context for work and restoration.",
            "acceptance_criteria": "Reading is within site-defined safe limits for the planned work/restoration state.",
            "limitation": "Safe temperature limits are site-specific.",
        }
    if variable == "flow":
        if use == "verification_support":
            return {
                "purpose": "Support no-flow confirmation.",
                "interpretation": "No-flow indication supports that flow has stopped in the measured line.",
                "acceptance_criteria": "Reading is zero/no-flow and confirmed by approved field verification where required.",
                "limitation": _instrument_limitation(instrument_type, "No-flow indication does not prove all energy sources are isolated."),
            }
        return {
            "purpose": "Track flow condition before isolation/restoration.",
            "interpretation": "Flow trend shows whether process movement is changing as expected.",
            "acceptance_criteria": "Flow is within the expected state for the current procedure step.",
            "limitation": "Flow indication is supporting context only.",
        }
    return {
        "purpose": "Provide supporting procedure context.",
        "interpretation": "Instrument indication helps compare the process state before, during, and after isolation.",
        "acceptance_criteria": "Use site-defined acceptance criteria and field verification.",
        "limitation": "Instrument context does not prove isolation by itself.",
    }


def _instrument_limitation(instrument_type, default):
    if instrument_type == "transmitter":
        return f"Remote transmitter readings are supporting trends only unless site procedure accepts them. {default}"
    if instrument_type == "local_indicator":
        return f"Local indication is stronger field context, but still advisory in this tool. {default}"
    return default


def _instrument_from_hilt_node(node, catalog, stlm_by_id):
    payload = node.get("payload") or {}
    node_id = _node_id(node)
    stlm = stlm_by_id.get(_norm(node_id)) or {}
    tag = (
        _attr(payload.get("attributes"), "tag")
        or _hilt_text_value(payload.get("text"))
        or stlm.get("tag")
        or _attr(payload.get("attributes"), "name")
    )
    parsed = parse_instrument_tag(tag or "", catalog)
    if not parsed:
        return {}
    bbox = stlm.get("bbox") or _hilt_bbox(payload) or []
    return {
        "id": str(node_id),
        "tag": parsed.get("normalized_tag") or tag,
        "prefix": parsed.get("prefix"),
        "name": parsed.get("name"),
        "measured_variable": parsed.get("measured_variable"),
        "instrument_type": parsed.get("instrument_type"),
        "sop_uses": parsed.get("sop_uses") or [],
        "verification_note": parsed.get("verification_note"),
        "entity_class": payload.get("entity_class") or stlm.get("entity_class"),
        "entity_type": payload.get("entity_type") or stlm.get("entity_type"),
        "bbox": bbox,
    }


def _stlm_instruments(stlm_payload, catalog):
    result = []
    for symbol in _extract_symbols(stlm_payload):
        tag = str(symbol.get("tag") or _symbol_attr(symbol, "tag") or "").strip()
        function_name = str(_symbol_attr(symbol, "FunctionName") or "").strip()
        function_number = str(_symbol_attr(symbol, "FunctionNumber") or "").strip()
        display_tag = tag or (f"{function_name}-{function_number}" if function_name and function_number else function_name)
        parsed = parse_instrument_tag(display_tag, catalog)
        if not parsed:
            continue
        ids = [str(value) for value in (symbol.get("uuid"), symbol.get("id"), symbol.get("source_id")) if value]
        result.append(
            {
            "ids": ids,
            "id": ids[0] if ids else str(display_tag),
            "tag": parsed.get("normalized_tag") or display_tag,
            "prefix": parsed.get("prefix"),
            "name": parsed.get("name"),
            "measured_variable": parsed.get("measured_variable"),
            "instrument_type": parsed.get("instrument_type"),
            "sop_uses": parsed.get("sop_uses") or [],
            "verification_note": parsed.get("verification_note"),
            "entity_class": symbol.get("entity_class"),
            "entity_type": symbol.get("entity_type"),
            "bbox": _symbol_bbox(symbol),
            }
        )
    return result


def _stlm_instruments_by_id(instruments):
    result = {}
    for summary in instruments:
        for value in summary.get("ids") or []:
            result[_norm(value)] = summary
    return result


def _target_adjacent_stlm_instruments(stlm_instruments, validation_data, seen_ids):
    target_bboxes = [
        item.get("bbox")
        for item in validation_data.get("selected_equipment_overlays") or []
        if _valid_bbox(item.get("bbox"))
    ]
    if not target_bboxes:
        return []
    result = []
    for instrument in stlm_instruments:
        instrument_id = str(instrument.get("id") or "")
        if instrument_id in seen_ids:
            continue
        bbox = _valid_bbox(instrument.get("bbox"))
        if not bbox:
            continue
        if not any(_bbox_near(bbox, target_bbox, padding=260) for target_bbox in target_bboxes):
            continue
        item = {
            key: value
            for key, value in instrument.items()
            if key not in {"ids"}
        }
        item["relevance"] = "stlm_target_adjacent"
        item["relevance_basis"] = "recognized STLM instrument visually adjacent to selected equipment"
        item["path_hops"] = None
        result.append(item)
    result.sort(key=lambda item: (str(item.get("prefix") or ""), str(item.get("tag") or "")))
    return result


def _target_node_ids(nodes, equipment_tag):
    eq_norm = _tag_norm(equipment_tag)
    targets = set()
    for node in nodes:
        payload = node.get("payload") or {}
        node_id = _node_id(node)
        if not node_id:
            continue
        tag = _attr(payload.get("attributes"), "tag") or _hilt_text_value(payload.get("text"))
        cls = str(payload.get("entity_class") or "").lower()
        if _tag_norm(tag) == eq_norm:
            targets.add(node_id)
        if cls == "equipment_nozzle" and _tag_norm(tag).endswith("_" + eq_norm):
            targets.add(node_id)
    return targets


def _adjacency(links):
    adj = {}
    for link in links:
        payload = link.get("payload") or {}
        entity_class = str(payload.get("entity_class") or "").strip()
        if entity_class not in RELEVANCE_LINE_CLASSES:
            continue
        source = str(link.get("source") or payload.get("from") or "").strip()
        target = str(link.get("target") or payload.get("to") or "").strip()
        if not source or not target:
            continue
        adj.setdefault(source, set()).add(target)
        adj.setdefault(target, set()).add(source)
    return adj


def _distances(starts, adjacency, max_hops):
    seen = {str(start): 0 for start in starts}
    queue = deque((str(start), 0) for start in starts)
    while queue:
        node, hops = queue.popleft()
        if hops >= max_hops:
            continue
        for nbr in adjacency.get(node, ()):
            if nbr in seen:
                continue
            seen[nbr] = hops + 1
            queue.append((nbr, hops + 1))
    return seen


def _node_id(node):
    value = node.get("id") or (node.get("payload") or {}).get("id") or (node.get("payload") or {}).get("source_id")
    return str(value) if value not in (None, "") else ""


def _hilt_bbox(payload):
    location = payload.get("bounding_box_location") or {}
    width = payload.get("bounding_box_width")
    height = payload.get("bounding_box_height")
    if location.get("x") is None or location.get("y") is None or width is None or height is None:
        return []
    cx = float(location.get("x"))
    cy = float(location.get("y"))
    w = float(width)
    h = float(height)
    return [int(round(cx - w / 2.0)), int(round(cy - h / 2.0)), int(round(w)), int(round(h))]


def _valid_bbox(bbox):
    if not isinstance(bbox, list) and not isinstance(bbox, tuple):
        return []
    if len(bbox) != 4:
        return []
    try:
        values = [int(value) for value in bbox]
    except Exception:
        return []
    return values if values[2] > 0 and values[3] > 0 else []


def _bbox_near(inner_bbox, outer_bbox, padding=0):
    inner = _valid_bbox(inner_bbox)
    outer = _valid_bbox(outer_bbox)
    if not inner or not outer:
        return False
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    cx = ix + iw / 2.0
    cy = iy + ih / 2.0
    return ox - padding <= cx <= ox + ow + padding and oy - padding <= cy <= oy + oh + padding


def _hilt_text_value(items):
    values = []
    for item in items or []:
        if isinstance(item, dict) and item.get("value") not in (None, "", []):
            values.append(str(item.get("value")))
    return ", ".join(values) if values else ""


def _attr(attributes, name):
    target = str(name or "").strip().lower()
    for attr in attributes or []:
        if isinstance(attr, dict) and str(attr.get("name") or "").strip().lower() == target:
            value = attr.get("value")
            if value not in (None, "", []):
                return str(value)
    return ""


def _norm(value):
    return normalize_tag(value)


def _tag_norm(value):
    return re.sub(r"[^A-Z0-9_]", "", str(value or "").upper())
