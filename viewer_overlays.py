"""Payload -> overlay collection. Pure: dict in, list[Overlay] out, no HTML.

The nine ``_collect_*`` functions turn a UI payload into the typed overlay list
the renderer draws. This is the pure/impure seam of the viewer -- everything here
is testable without rendering a page.

NOTE: ``_valid_bbox`` here deliberately has NO width/height positivity check,
unlike the shared domain.hilt_geometry.valid_bbox. Tightening it would silently
drop zero-area overlays from rendered output. tests/test_geometry_helpers.py pins
the difference.
"""
from __future__ import annotations

from domain.display import device_display_label
from domain.isolation_actions import manual_candidate_label
from domain.enums import ImpactSeverity, OverlayKind
from domain.models import BBox, Overlay
from secondary_context import context_display_label, context_source_label


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collect_overlays(data):
    seq_by_uuid = _isolation_sequence(data.get("loto_procedure"))
    overlays = []
    overlays.extend(_collect_target_overlays(data))
    overlays.extend(_collect_isolation_overlays(data, seq_by_uuid))
    overlays.extend(_collect_scheme_device_overlays(data, seq_by_uuid))
    overlays.extend(_collect_impact_overlays(data))
    overlays.extend(_collect_obligation_manual_overlays(data))
    overlays.extend(_collect_manual_check_overlays(data))
    overlays.extend(_collect_context_overlays(data))
    overlays.extend(_collect_instrument_overlays(data))
    overlays.extend(_collect_relief_overlays(data))
    return overlays


def _collect_target_overlays(data):
    overlays = []
    for item in data.get("selected_equipment_overlays", []) or []:
        bbox = _valid_bbox(item.get("bbox"))
        if not bbox:
            continue
        label = "Target"
        title = f"Selected equipment of interest | {item.get('tag')} | class={item.get('entity_class')} | bbox={bbox}"
        overlays.append(
            Overlay(
                kind=OverlayKind.TARGET,
                bbox=BBox.from_any(bbox),
                label=label,
                title=title,
                css_class="target-box",
                label_class="target-label",
                summary_seq="target",
                summary_uuid=str(item.get("uuid") or item.get("equipment_id") or ""),
                summary_reason=str(item.get("reason") or title),
            )
        )
    return overlays


def _collect_isolation_overlays(data, seq_by_uuid):
    overlays = []
    for point in data.get("isolation_points", []) or []:
        bbox = _valid_bbox(point.get("bbox"))
        if not bbox:
            continue
        seq = seq_by_uuid.get(str(point.get("uuid")))
        label = device_display_label(point, fallback=str(point.get("uuid") or "isolation device"))
        if seq:
            label = f"#{seq}  {label}"
        title = f"{label} | uuid={point.get('uuid')} | bbox={bbox}"
        overlays.append(
            Overlay(
                kind=OverlayKind.ISOLATION,
                bbox=BBox.from_any(bbox),
                label=label,
                title=title,
                css_class="box",
                label_class="label",
                summary_seq=str(seq or ""),
                summary_uuid=str(point.get("uuid") or ""),
                summary_reason=str(point.get("reason") or ""),
                badge=str(seq or ""),
            )
        )
    return overlays


def _collect_scheme_device_overlays(data, seq_by_uuid):
    overlays = []
    selected_ids = {str(point.get("uuid")) for point in data.get("isolation_points", []) or []}
    seen = set()
    for scheme in ((data.get("detected_isolation_schemes") or {}).get("items") or []):
        for device in scheme.get("devices") or []:
            device_id = str(device.get("id") or "")
            if not device_id or device_id in selected_ids or device_id in seen:
                continue
            bbox = _valid_bbox(device.get("bbox"))
            if not bbox:
                continue
            seen.add(device_id)
            label = _scheme_device_label(device, scheme)
            title = (
                f"Detected scheme device | scheme={scheme.get('scheme_type')} | "
                f"label={label} | uuid={device_id} | bbox={bbox}"
            )
            overlays.append(
                Overlay(
                    kind=OverlayKind.SCHEME,
                    bbox=BBox.from_any(bbox),
                    label=label,
                    title=title,
                    css_class="box scheme-box",
                    label_class="scheme-label",
                    summary_seq="scheme",
                    summary_uuid=device_id,
                    summary_reason=title,
                    badge="",
                )
            )
    return overlays


def _collect_impact_overlays(data):
    overlays = []
    warnings = ((data.get("downstream_impact") or {}).get("warnings") or [])
    for index, warning_item in enumerate([item for item in warnings if _valid_bbox(item.get("affected_bbox"))], start=1):
        bbox = _minimum_display_bbox(_valid_bbox(warning_item.get("affected_bbox")), min_width=86, min_height=30)
        severity = ImpactSeverity(str(warning_item.get("severity") or ImpactSeverity.POSSIBLE.value))
        raw_label = warning_item.get("affected_tag") or warning_item.get("affected_id") or "downstream impact"
        source = warning_item.get("source_tag") or warning_item.get("source_candidate_tag") or "selected barrier"
        wording = "likely affects" if severity == ImpactSeverity.LIKELY else "may affect"
        title = (
            f"Downstream impact | {source} {wording} {raw_label} | "
            f"class={warning_item.get('affected_class')} | original_bbox={warning_item.get('affected_bbox')}"
        )
        overlays.append(
            Overlay(
                kind=OverlayKind.IMPACT,
                bbox=BBox.from_any(bbox),
                label=_impact_overlay_label(warning_item, index),
                title=title,
                css_class=f"impact-box impact-{severity.value}",
                label_class="impact-label",
                summary_seq="",
                summary_uuid=str(warning_item.get("affected_id") or ""),
                summary_reason=title,
                severity=severity.value,
            )
        )
    return overlays


def _collect_manual_check_overlays(data):
    overlays = []
    for check in data.get("manual_visual_isolation_checks", []) or []:
        bbox = _valid_bbox(check.get("bbox"))
        if not bbox:
            continue
        title = f"{check.get('entity_class')} | uuid={check.get('uuid')} | bbox={bbox}"
        overlays.append(
            Overlay(
                kind=OverlayKind.MANUAL,
                bbox=BBox.from_any(bbox),
                label="manual check",
                title=title,
                css_class="manual-box",
                label_class="manual-label",
                summary_seq="",
                summary_uuid=str(check.get("uuid") or ""),
                summary_reason=str(check.get("reason") or ""),
            )
        )
    return overlays


def _collect_obligation_manual_overlays(data):
    overlays = []
    seen = set()
    obligations = (data.get("isolation_obligations") or {}).get("items") or []
    for obligation in obligations:
        source = obligation.get("source_component_tag") or obligation.get("source_component") or "source"
        for candidate in obligation.get("manual_candidates") or []:
            bbox = _valid_bbox(candidate.get("bbox"))
            if not bbox:
                continue
            key = (str(candidate.get("uuid") or ""), tuple(bbox))
            if key in seen:
                continue
            seen.add(key)
            tag = device_display_label(candidate, fallback="candidate")
            label = manual_candidate_label(candidate.get("entity_class"), obligation.get("source_type"))
            title = (
                f"Manual isolation candidate | source={source} | candidate={tag} | "
                f"uuid={candidate.get('uuid')} | bbox={bbox}"
            )
            overlays.append(
                Overlay(
                    kind=OverlayKind.OBLIGATION_MANUAL,
                    bbox=BBox.from_any(bbox),
                    label=label,
                    title=title,
                    css_class="manual-box",
                    label_class="manual-label",
                    summary_seq="manual",
                    summary_uuid=str(candidate.get("uuid") or ""),
                    summary_reason=str(candidate.get("reason") or title),
                )
            )
    return overlays


def _collect_context_overlays(data):
    overlays = []
    context_items = data.get("boundary_context_sources", []) or data.get("context_instruments", []) or []
    for context in context_items:
        bbox = _valid_bbox(context.get("source_bbox"))
        if not bbox:
            continue
        label = context_display_label(context)
        title = (
            f"secondary/context source | source={context_source_label(context)} | "
            f"class={context.get('classification')} | bbox={bbox}"
        )
        overlays.append(
            Overlay(
                kind=OverlayKind.CONTEXT,
                bbox=BBox.from_any(bbox),
                label=label,
                title=title,
                css_class="context-box",
                label_class="context-label",
                summary_seq="",
                summary_uuid=str(context.get("source_component") or ""),
                summary_reason=str(context.get("reason") or ""),
            )
        )
    return overlays


def _collect_instrument_overlays(data):
    overlays = []
    for instrument in ((data.get("instrument_context") or {}).get("instruments") or []):
        bbox = _valid_bbox(instrument.get("bbox"))
        if not bbox:
            continue
        label = str(instrument.get("tag") or instrument.get("name") or "instrument")
        title = (
            f"instrument context | {label} | variable={instrument.get('measured_variable')} | "
            f"type={instrument.get('instrument_type')} | bbox={bbox}"
        )
        overlays.append(
            Overlay(
                kind=OverlayKind.INSTRUMENT,
                bbox=BBox.from_any(bbox),
                label=label,
                title=title,
                css_class="instrument-box",
                label_class="instrument-label",
                summary_seq="instrument",
                summary_uuid=str(instrument.get("id") or ""),
                summary_reason=str(instrument.get("verification_note") or title),
            )
        )
    return overlays


def _collect_relief_overlays(data):
    overlays = []
    for item in ((data.get("relief_candidates") or {}).get("items") or []):
        if item.get("relief_type") not in {"vent", "drain", "bleed"}:
            continue
        bbox = _valid_bbox(item.get("bbox"))
        if not bbox:
            continue
        label = str(item.get("relief_type") or "relief").title()
        title = (
            f"relief candidate | {label} | tag={item.get('tag')} | "
            f"class={item.get('entity_class')} | classified_by={item.get('classified_by')} | bbox={bbox}"
        )
        overlays.append(
            Overlay(
                kind=OverlayKind.RELIEF,
                bbox=BBox.from_any(bbox),
                label=label,
                title=title,
                css_class="relief-box",
                label_class="relief-label",
                summary_seq="relief",
                summary_uuid=str(item.get("id") or ""),
                summary_reason=str(item.get("basis") or title),
            )
        )
    return overlays


def _valid_bbox(bbox):
    if not isinstance(bbox, list) and not isinstance(bbox, tuple):
        return []
    if len(bbox) != 4:
        return []
    try:
        return [int(value) for value in bbox]
    except Exception:
        return []


def _source_warning_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label and label != "unlabeled graph-only source":
        return label
    raw = str(item.get("source_component_tag_raw") or "").strip()
    if raw:
        return raw
    if item.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return str(item.get("source_component") or "unknown")


def _obligation_source_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label and label != "unlabeled graph-only source":
        return label
    raw = str(item.get("source_component_tag_raw") or "").strip()
    if raw:
        return raw
    return str(item.get("source_component") or "unknown source")


def _minimum_display_bbox(bbox, min_width=1, min_height=1):
    x, y, w, h = [int(value) for value in bbox]
    display_w = max(w, int(min_width))
    display_h = max(h, int(min_height))
    display_x = x - max((display_w - w) // 2, 0)
    display_y = y - max((display_h - h) // 2, 0)
    return [display_x, display_y, display_w, display_h]


def _impact_overlay_label(warning_item, index):
    affected_type = str(warning_item.get("affected_type") or "").replace("_", " ").strip()
    affected_class = str(warning_item.get("affected_class") or "").replace("_", " ").strip()
    label_type = affected_type or affected_class or "node"
    if label_type == "endpoint":
        label_type = "endpoint"
    elif "instrument" in label_type or "control" in label_type:
        label_type = "instrument"
    elif label_type == "relief context":
        label_type = "relief"
    elif label_type == "equipment":
        label_type = "equipment"
    return f"Impact {index} {label_type}"


def _scheme_device_label(device, scheme):
    label = device_display_label(device, fallback="device")
    scheme_type = str(scheme.get("scheme_type") or "").lower()
    if "double block" in scheme_type:
        return f"second block: {label}"
    if "positive" in scheme_type:
        return f"positive isolation: {label}"
    return f"scheme device: {label}"


def _isolation_sequence(procedure):
    if not procedure:
        return {}
    seq_by_uuid = {}
    seq = 0
    for step in procedure.get("ordered_steps") or []:
        if step.get("phase") == 3 and step.get("device_uuid"):
            seq += 1
            seq_by_uuid[str(step["device_uuid"])] = seq
    return seq_by_uuid
