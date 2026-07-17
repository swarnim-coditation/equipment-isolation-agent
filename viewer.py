import html
from collections import Counter
from dataclasses import dataclass

from domain.display import device_display_label, device_display_name
from domain.enums import ImpactSeverity, ObligationStatus, OverlayKind, SourceType
from domain.models import BBox, Overlay

CANVAS_WIDTH = 5458
CANVAS_HEIGHT = 3109
VIEW_PADDING = 180

# (kinds, human label, swatch color, dashed?) — drives both the map legend and
# the color chips in the summary table so they always agree with the drawn boxes.
_LEGEND_ENTRIES = [
    ((OverlayKind.TARGET,), "Target equipment", "#eab308", False),
    ((OverlayKind.ISOLATION,), "Isolation point / barrier", "#2563eb", False),
    ((OverlayKind.SCHEME,), "Detected scheme device (unselected 2nd block)", "#1d4ed8", False),
    ((OverlayKind.RELIEF,), "Relief point (vent / drain / bleed)", "#0284c7", False),
    ((OverlayKind.MANUAL, OverlayKind.OBLIGATION_MANUAL), "Manual field check", "#f59e0b", True),
    ((OverlayKind.CONTEXT,), "Boundary context (non-process)", "#1d4ed8", False),
    ((OverlayKind.INSTRUMENT,), "Instrument (advisory)", "#0891b2", False),
    ((OverlayKind.IMPACT,), "Downstream impact", "#dc2626", False),
]


@dataclass(frozen=True)
class Viewport:
    scroll_x: int
    scroll_y: int
    offset_x: int
    offset_y: int
    canvas_width: int
    canvas_height: int


def render_viewer_html(payload, image_url=""):
    data = payload.get("data", [{}])[0]
    debug = payload.get("debug") or data.get("debug") or {}
    overlays = _collect_overlays(data)
    viewport = _compute_viewport(overlays, image_url)
    procedure_html = _render_isolation_procedure_panel(
        data.get("loto_procedure"),
        data,
        data.get("unselected_boundary_sources", []) or [],
        data.get("manual_visual_isolation_checks", []) or [],
        data.get("boundary_context_sources", []) or data.get("context_instruments", []) or [],
        data.get("downstream_impact") or {},
    )
    image = _render_image(image_url, viewport)
    return _render_document(
        data=data,
        image=image,
        overlays=overlays,
        viewport=viewport,
        procedure_html=procedure_html,
        summary_html=_render_summary_table(overlays),
        degraded_html=_render_degraded_banner(debug, data),
    )


def _render_degraded_banner(debug, data):
    """Prominent callout when the run fell back to partial/graph-only data, so a
    degraded result is never presented as if it were authoritative."""
    errors = []
    if debug.get("hilt_graph_error"):
        errors.append(f"P&ID topology (HILT) failed to load: {debug.get('hilt_graph_error')}")
    if debug.get("bbox_stlm_error"):
        errors.append(f"Symbols/labels (STLM) failed to load: {debug.get('bbox_stlm_error')}")
    zero_topology = not (debug.get("hilt_graph_node_count") or 0)
    if not errors and not zero_topology:
        return ""
    if zero_topology and not any("HILT" in e for e in errors):
        errors.append("No P&ID topology nodes were available; topology-based checks were skipped.")
    if not (data.get("selected_equipment_overlays") or []):
        errors.append("Target equipment could not be located on the drawing.")
    items = "".join(f"<li>{html.escape(str(reason))}</li>" for reason in errors)
    return (
        '<div class="degraded">'
        '<div class="degraded-title">⚠ Degraded / incomplete data — do not rely on this result as-is</div>'
        f"<ul>{items}</ul>"
        "<div>This run fell back to partial or graph-only data; drawing overlays, second-block detection, and "
        "relief analysis may be missing or incorrect. Re-run once the drawing/topology services are available.</div>"
        "</div>"
    )


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
            title = (
                f"Manual isolation candidate | source={source} | candidate={tag} | "
                f"uuid={candidate.get('uuid')} | bbox={bbox}"
            )
            overlays.append(
                Overlay(
                    kind=OverlayKind.OBLIGATION_MANUAL,
                    bbox=BBox.from_any(bbox),
                    label="manual isolation check",
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
        label = str(context.get("source_component_tag") or "boundary context")
        title = f"boundary context | source={label} | class={context.get('classification')} | bbox={bbox}"
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


def _compute_viewport(overlays, image_url):
    if overlays:
        min_x = min(item.bbox[0] for item in overlays)
        min_y = min(item.bbox[1] for item in overlays)
        max_x = max(item.bbox[0] + item.bbox[2] for item in overlays)
        max_y = max(item.bbox[1] + item.bbox[3] for item in overlays)
    else:
        min_x = min_y = max_x = max_y = 0

    scroll_x = max(min_x - VIEW_PADDING, 0)
    scroll_y = max(min_y - VIEW_PADDING, 0)
    if image_url or not overlays:
        return Viewport(
            scroll_x=scroll_x if overlays else 0,
            scroll_y=scroll_y if overlays else 0,
            offset_x=0,
            offset_y=0,
            canvas_width=CANVAS_WIDTH,
            canvas_height=CANVAS_HEIGHT,
        )
    return Viewport(
        scroll_x=scroll_x,
        scroll_y=scroll_y,
        offset_x=scroll_x,
        offset_y=scroll_y,
        canvas_width=max(max_x - scroll_x + VIEW_PADDING, 640),
        canvas_height=max(max_y - scroll_y + VIEW_PADDING, 420),
    )


def _render_overlay_divs(overlays, viewport):
    parts = []
    for item in overlays:
        x, y, w, h = item.bbox
        display_x = x - viewport.offset_x
        display_y = y - viewport.offset_y
        parts.append(
            f'<div class="{html.escape(item.css_class)}" '
            f'style="left:{display_x}px;top:{display_y}px;width:{w}px;height:{h}px;" '
            f'title="{html.escape(item.title)}"></div>'
        )
        parts.append(
            f'<div class="{html.escape(item.label_class)}" '
            f'style="left:{display_x}px;top:{max(display_y - 22, 0)}px;">'
            f'{html.escape(item.label)}</div>'
        )
        if item.badge:
            parts.append(
                f'<div class="seq-badge" style="left:{display_x - 17}px;top:{max(display_y - 17, 0)}px;" '
                f'title="Isolation step {html.escape(item.badge)}">{html.escape(item.badge)}</div>'
            )
    return "\n".join(parts)


def _kind_style(kind):
    for kinds, label, color, _dashed in _LEGEND_ENTRIES:
        if kind in kinds:
            return label, color
    return str(kind), "#6b7280"


def _render_hero(data, overlays):
    equipment = ", ".join(html.escape(str(item)) for item in data.get("selected_equipment") or [])
    pill_text, pill_css = _status_pill(data)
    title = f"Equipment {equipment}" if equipment else "Equipment isolation"
    return (
        '<div class="hero">'
        '<div class="hero-top">'
        f'<div class="hero-title">{title}</div>'
        f'<span class="pill {pill_css}">{html.escape(pill_text)}</span>'
        "</div>"
        f"{_render_stat_tiles(data, overlays)}"
        "</div>"
    )


# Single source of truth for how an assurance_status is displayed. Keyed on the
# exact strings validator.validate() emits (see domain.enums.AssuranceStatus).
# tone -> pill color + callout CSS; both the pill and the callout read this table
# so they can never disagree for the same status.
_STATUS_DISPLAY = {
    "not_isolated": ("bad", "Not isolated", "Not isolated with current evidence"),
    "provisional_unproven_isolation": ("warn", "Needs field confirmation", "Field confirmation required before work"),
    "complete_positive_isolation": ("good", "Isolated", "Isolation boundary complete in available data"),
    "complete_proven_isolation": ("good", "Isolated", "Isolation boundary complete in available data"),
    "insufficient_data": ("unknown", "Insufficient data", "Isolation status unknown"),
}
_STATUS_CALLOUT_CSS = {
    "bad": "status-not-isolated",
    "warn": "status-needs-confirmation",
    "good": "status-complete",
    "unknown": "status-unknown",
}


def _status_entry(data):
    status = str(data.get("assurance_status") or "").strip().lower()
    tone, pill_text, title = _STATUS_DISPLAY.get(status, ("unknown", status or "Unknown", "Isolation status unknown"))
    return status, tone, pill_text, title


def _status_pill(data):
    _status, tone, pill_text, _title = _status_entry(data)
    return pill_text, f"pill-{tone}"


def _render_stat_tiles(data, overlays):
    counts = Counter(item.kind for item in overlays)
    summary = (data.get("isolation_obligations") or {}).get("summary") or {}
    manual = counts[OverlayKind.MANUAL] + counts[OverlayKind.OBLIGATION_MANUAL]
    unresolved = int(summary.get("unresolved_count") or 0)
    impacts = counts[OverlayKind.IMPACT]
    tiles = [
        ("Isolation points", counts[OverlayKind.ISOLATION], ""),
        ("2nd-block (detected)", counts[OverlayKind.SCHEME], ""),
        ("Relief points", counts[OverlayKind.RELIEF], ""),
        ("Manual checks", manual, "warn" if manual else ""),
        ("Unresolved paths", unresolved, "warn" if unresolved else ""),
        ("Downstream impacts", impacts, "bad" if impacts else ""),
    ]
    cells = []
    for label, value, tone in tiles:
        tone_cls = f" {tone}" if tone else ""
        cells.append(
            f'<div class="stat{tone_cls}"><div class="num">{value}</div>'
            f'<div class="lbl">{html.escape(label)}</div></div>'
        )
    return f'<div class="stat-row">{"".join(cells)}</div>'


def _render_legend(overlays):
    if not overlays:
        return ""
    counts = Counter(item.kind for item in overlays)
    chips = []
    for kinds, label, color, dashed in _LEGEND_ENTRIES:
        total = sum(counts.get(kind, 0) for kind in kinds)
        if not total:
            continue
        border = f"2px {'dashed' if dashed else 'solid'} {color}"
        chips.append(
            f'<span class="legend-item">'
            f'<span class="swatch" style="border:{border};background:{color}22;"></span>'
            f"{html.escape(label)} <b>{total}</b></span>"
        )
    if not chips:
        return ""
    return '<div class="legend">' + "".join(chips) + "</div>"


def _render_summary_table(overlays):
    if not overlays:
        return ""
    rows = []
    for item in overlays:
        label, color = _kind_style(item.kind)
        chip = (
            f'<span class="type-chip"><span class="dot" style="background:{color};"></span>'
            f"{html.escape(label)}</span>"
        )
        rows.append(
            "<tr>"
            f"<td>{chip}</td>"
            f"<td>{html.escape(item.summary_seq)}</td>"
            f"<td>{html.escape(item.summary_uuid)}</td>"
            f"<td>{html.escape(item.label)}</td>"
            f"<td>{html.escape(str(item.bbox))}</td>"
            f"<td>{html.escape(item.summary_reason)}</td>"
            "</tr>"
        )
    return (
        '<h2 class="map-heading">All Overlays</h2>'
        '<table><thead><tr><th>Type</th><th>Seq</th><th>UUID</th><th>Label</th><th>BBox</th><th>Reason</th></tr></thead><tbody>'
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _render_image(image_url, viewport):
    if image_url:
        return f'<img src="{html.escape(image_url)}" />'
    return (
        f'<div class="blank" style="width:{viewport.canvas_width}px;height:{viewport.canvas_height}px;">'
        f"Focused no-image view. Original offset: x={viewport.offset_x}, y={viewport.offset_y}. "
        "Use --image-url for full P&amp;ID background.</div>"
    )


def _render_document(data, image, overlays, viewport, procedure_html, summary_html, degraded_html=""):
    return (
        """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
body { font-family: Arial, sans-serif; margin: 20px; color: #111827; }
h1 { margin: 0 0 8px; font-size: 20px; }
.meta { margin: 0 0 16px; color: #4b5563; }
.warning { margin: 0 0 16px; padding: 10px 12px; border: 1px solid #f59e0b; background: #fffbeb; color: #92400e; font-weight: 600; }
.degraded { margin: 0 0 18px; padding: 12px 14px; border: 2px solid #dc2626; background: #fef2f2; color: #7f1d1d; border-radius: 6px; }
.degraded-title { font-weight: 700; font-size: 15px; margin-bottom: 6px; }
.degraded ul { margin: 6px 0; padding-left: 20px; }
.hero { margin: 0 0 18px; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 18px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.hero-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.hero-title { font-size: 18px; font-weight: 700; }
.hero-title span { color: #6b7280; font-weight: 500; font-size: 14px; }
.pill { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 12px; font-weight: 700; letter-spacing: 0.02em; text-transform: uppercase; }
.pill-bad { background: #fef2f2; color: #991b1b; border: 1px solid #fca5a5; }
.pill-warn { background: #fffbeb; color: #92400e; border: 1px solid #fcd34d; }
.pill-good { background: #f0fdf4; color: #166534; border: 1px solid #86efac; }
.pill-unknown { background: #f8fafc; color: #334155; border: 1px solid #cbd5e1; }
.stat-row { display: flex; gap: 10px; flex-wrap: wrap; }
.stat { min-width: 96px; padding: 8px 12px; border-radius: 6px; background: #f9fafb; border: 1px solid #e5e7eb; }
.stat .num { font-size: 20px; font-weight: 700; line-height: 1; }
.stat .lbl { font-size: 11px; color: #6b7280; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.03em; }
.stat.warn { border-color: #fcd34d; background: #fffbeb; }
.stat.warn .num { color: #b45309; }
.stat.bad { border-color: #fca5a5; background: #fef2f2; }
.stat.bad .num { color: #b91c1c; }
.map-heading { font-size: 16px; margin: 4px 0 6px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 0 8px; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fbfbfb; font-size: 12px; align-items: center; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; color: #374151; }
.legend .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 3px; box-sizing: border-box; }
.type-chip { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.type-chip .dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.canvas-wrap { width: 100%; max-height: 78vh; overflow: auto; border: 1px solid #d1d5db; background: #f9fafb; }
.canvas { position: relative; display: inline-block; }
.canvas img { display: block; width: auto; height: auto; max-width: none; }
.blank { background: #fafafa; color: #555; display:flex; align-items:center; justify-content:center; }
.target-box { position: absolute; border: 4px solid #eab308; box-sizing: border-box; background: rgba(234,179,8,0.18); z-index: 3; }
.target-label { position: absolute; background: #a16207; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 7; }
.box { position: absolute; border: 3px solid #2563eb; box-sizing: border-box; background: rgba(37,99,235,0.12); }
.seq-badge { position: absolute; width: 26px; height: 26px; line-height: 26px; text-align: center; border-radius: 50%; background: #2563eb; color: #fff; font-weight: 700; font-size: 14px; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.5); z-index: 5; }
.label { position: absolute; background: #111827; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.scheme-box { border-color: #1d4ed8; border-style: dashed; background: rgba(37,99,235,0.08); }
.scheme-label { position: absolute; background: #1e40af; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.impact-box { position: absolute; border: 4px solid #dc2626; box-sizing: border-box; background: rgba(220,38,38,0.16); z-index: 4; }
.impact-possible { border-style: dashed; background: rgba(220,38,38,0.10); }
.impact-label { position: absolute; background: #991b1b; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 6; }
.manual-box { position: absolute; border: 3px dashed #f59e0b; box-sizing: border-box; background: rgba(245,158,11,0.18); }
.manual-label { position: absolute; background: #92400e; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.context-box { position: absolute; border: 2px solid #2563eb; box-sizing: border-box; background: rgba(37,99,235,0.14); }
.context-label { position: absolute; background: #1d4ed8; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.instrument-box { position: absolute; border: 3px solid #0891b2; box-sizing: border-box; background: rgba(8,145,178,0.12); z-index: 2; }
.instrument-label { position: absolute; background: #0e7490; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 6; }
.relief-box { position: absolute; border: 4px solid #0284c7; box-sizing: border-box; background: rgba(2,132,199,0.18); z-index: 4; }
.relief-label { position: absolute; background: #0369a1; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 6; }
.procedure { margin: 0 0 18px; max-width: 1200px; border: 1px solid #d1d5db; border-radius: 6px; padding: 14px 18px; background: #fff; }
.procedure h2 { margin: 0 0 4px; font-size: 17px; }
.procedure h3 { margin: 14px 0 6px; font-size: 13px; color: #111827; }
.procedure .meta { margin: 0 0 10px; }
.procedure ol, .procedure ul { margin: 6px 0 6px 18px; padding: 0; }
.procedure li { margin: 4px 0; font-size: 13px; line-height: 1.4; }
.procedure .phase { display:inline-block; min-width: 230px; color: #1d4ed8; font-weight: 600; font-size: 12px; }
.procedure .phase-group { margin: 10px 0 14px; padding: 0 0 0 10px; border-left: 3px solid #dbeafe; }
.procedure .phase-group h4 { margin: 0 0 6px; font-size: 13px; color: #1e3a8a; }
.procedure .phase-group h4 span { color: #64748b; font-weight: 500; }
.procedure .phase-steps { margin-left: 0; list-style: none; counter-reset: none; }
.procedure .phase-steps li { margin: 5px 0; }
.procedure .step-number { display: inline-block; min-width: 24px; color: #64748b; font-weight: 600; }
.procedure .field-gap { color: #b45309; font-weight: 600; }
.procedure .release { margin-top: 10px; font-size: 12px; color: #4b5563; border-top: 1px dashed #d1d5db; padding-top: 8px; }
.procedure .status-callout { margin-top: 12px; padding: 12px 14px; border-radius: 6px; border: 1px solid #d1d5db; background: #f9fafb; }
.procedure .status-callout h3 { margin: 0 0 4px; font-size: 15px; }
.procedure .status-callout p { margin: 0; font-size: 13px; line-height: 1.4; }
.procedure .status-not-isolated { border-color: #dc2626; background: #fef2f2; color: #7f1d1d; }
.procedure .status-needs-confirmation { border-color: #f59e0b; background: #fffbeb; color: #78350f; }
.procedure .status-complete { border-color: #16a34a; background: #f0fdf4; color: #14532d; }
.procedure .status-unknown { border-color: #94a3b8; background: #f8fafc; color: #334155; }
.procedure .alerts { margin-top: 12px; padding: 10px 12px; border: 1px solid #f59e0b; background: #fffbeb; color: #7c2d12; }
.procedure .alerts h3 { margin-top: 0; color: #7c2d12; }
.procedure .coverage { margin-top: 12px; padding: 10px 12px; border: 1px solid #d1d5db; background: #f9fafb; color: #111827; }
.procedure .coverage h3 { margin-top: 0; }
.procedure .step-detail { margin: 4px 0 8px 28px; color: #334155; }
.procedure .step-detail li { font-size: 12px; margin: 2px 0; }
.procedure .possible { color: #854d0e; }
.procedure .likely { color: #991b1b; font-weight: 600; }
table { border-collapse: collapse; margin-top: 16px; max-width: 1200px; font-size: 13px; }
th, td { border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
</style>
</head>
<body>
<h1>Equipment Isolation Overlay</h1>
"""
        + degraded_html
        + _render_hero(data, overlays)
        + procedure_html
        + '<h2 class="map-heading">P&amp;ID Overlay Map</h2>'
        + f'<p class="meta">Boxes: {len(overlays)}. Assurance: {html.escape(str(data.get("assurance_status")))}. '
        + "Scroll the image pane horizontally or vertically to inspect the full P&amp;ID.</p>\n"
        + _render_legend(overlays)
        + f'\n<div id="imagePane" class="canvas-wrap" data-scroll-x="{viewport.scroll_x}" data-scroll-y="{viewport.scroll_y}">\n'
        + '<div class="canvas">\n'
        + image
        + "\n"
        + _render_overlay_divs(overlays, viewport)
        + "\n</div>\n</div>\n"
        + summary_html
        + """
<script>
window.addEventListener('load', function () {
  var pane = document.getElementById('imagePane');
  if (!pane) return;
  pane.scrollLeft = Number(pane.dataset.scrollX || 0);
  pane.scrollTop = Number(pane.dataset.scrollY || 0);
});
</script>
</body>
</html>
"""
    )


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
    if label:
        return label
    if item.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return str(item.get("source_component") or "unknown")


def _obligation_source_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label:
        return label
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


def _render_isolation_procedure_panel(
    procedure,
    data,
    unselected_sources,
    manual_checks,
    context_instruments,
    downstream_impact,
):
    warning_html = _render_procedure_warnings(
        unselected_sources,
        manual_checks,
        context_instruments,
        downstream_impact,
        data.get("isolation_obligations") or {},
    )
    coverage_html = _render_isolation_coverage(data.get("isolation_obligations") or {})
    scheme_html = _render_detected_schemes(data.get("detected_isolation_schemes") or {})
    relief_html = _render_relief_candidates(data.get("relief_candidates") or {})
    steps = (procedure or {}).get("ordered_steps") or []
    if not steps and not warning_html and not data.get("assurance_status"):
        return ""

    standard = html.escape(str((procedure or {}).get("standard") or "29 CFR 1910.147"))
    release_ref = html.escape(str((procedure or {}).get("release_from_loto_ref") or "1910.147(e)"))
    release = html.escape(str((procedure or {}).get("release_note") or ""))
    order_source = (procedure or {}).get("within_phase_order_source") or "engine_candidate_order_not_proposed"
    if order_source == "agent_engineering_judgment":
        order_note = (
            "Within-phase device order is the agent's engineering judgment "
            "(OSHA does not prescribe which valve to close first; only the phase order is regulated)."
        )
    elif order_source == "flow_grounding_inlet_first_default":
        order_note = (
            "Within-phase device order is a flow-grounded default (isolate INLET/upstream first, then outlet), "
            "derived from the P&ID flow direction parsed by the HILT graph. "
            "OSHA does not prescribe which valve to close first; only the phase order is regulated."
        )
    else:
        order_note = (
            "Within-phase device order NOT yet proposed by the agent (shown in engine candidate order). "
            "OSHA prescribes only the phase order, not the within-phase device order."
        )
    steps_html = _render_grouped_ordered_steps(steps)

    assurance = html.escape(str(data.get("assurance_status") or "unknown"))
    selected = ", ".join(html.escape(str(item)) for item in data.get("selected_equipment") or [])
    status_callout = _render_status_callout(data)
    return (
        f'<div class="procedure"><h2>Isolation Procedure</h2>'
        f'<p class="meta">Equipment: {selected or "unknown"}. Assurance: {assurance}. '
        f'Procedure basis: OSHA {standard}(d).</p>'
        f'{status_callout}'
        f'{warning_html}'
        f'{coverage_html}'
        f'{scheme_html}'
        f'{relief_html}'
        f'<h3>Sequencing Basis</h3><p class="meta">{html.escape(order_note)}</p>'
        f'{steps_html}'
        f'{_render_release_from_isolation(release_ref, release, (procedure or {}).get("restoration_checks") or [])}'
        "</div>"
    )


def _render_grouped_ordered_steps(steps):
    if not steps:
        return '<h3>Ordered Isolation Steps</h3><p class="meta">No ordered procedure steps are present in this payload.</p>'

    groups = []
    current_key = None
    current = None
    for step in steps:
        key = (step.get("phase"), step.get("ref"), step.get("title"))
        if key != current_key:
            current = {"phase": step.get("phase"), "ref": step.get("ref"), "title": step.get("title"), "steps": []}
            groups.append(current)
            current_key = key
        current["steps"].append(step)

    sections = []
    for group in groups:
        heading = (
            f'Phase {html.escape(str(group.get("phase") or ""))}: '
            f'{html.escape(str(group.get("title") or ""))}'
        )
        ref = html.escape(str(group.get("ref") or ""))
        items = []
        for step in group["steps"]:
            cls = ' class="field-gap"' if step.get("field_gap") else ""
            items.append(
                f'<li{cls}><span class="step-number">{html.escape(str(step.get("step") or ""))}.</span> '
                f'{html.escape(str(step.get("action") or ""))}{_render_step_details(step)}</li>'
            )
        sections.append(
            '<section class="phase-group">'
            f'<h4>{heading} <span>{ref}</span></h4>'
            f'<ol class="phase-steps">{"".join(items)}</ol>'
            '</section>'
        )
    return '<h3>Ordered Isolation Steps</h3>' + "".join(sections)


def _render_detected_schemes(detected_schemes):
    if (detected_schemes or {}).get("status") != "completed":
        return ""
    items = detected_schemes.get("items") or []
    if not items:
        return ""
    rows = []
    for item in items:
        source = html.escape(str(item.get("source_component_tag") or item.get("source_component") or "source"))
        scheme = html.escape(str(item.get("scheme_type") or "unknown"))
        barriers = _scheme_device_list(item)
        relief = ", ".join(html.escape(str(value)) for value in item.get("relief_candidate_ids") or []) or "-"
        rows.append(f"<li><b>{source}</b>: {scheme}; barriers: {barriers}; relief: {relief}.</li>")
    return (
        '<div class="coverage"><h3>Detected Isolation Scheme</h3>'
        '<p class="meta">Detected from existing HILT topology only; no hazard-based scheme recommendation is made.</p>'
        f'<ul>{"".join(rows)}</ul></div>'
    )


def _scheme_device_list(scheme):
    devices = scheme.get("devices") or []
    if not devices:
        return ", ".join(html.escape(str(value)) for value in scheme.get("barrier_ids") or []) or "-"
    labels = []
    for device in devices:
        label = device_display_label(device, fallback=str(device.get("id") or "device"))
        device_id = str(device.get("id") or "")
        if device_id and device_id != label:
            labels.append(f"{html.escape(label)} <span class=\"meta\">({html.escape(device_id)})</span>")
        else:
            labels.append(html.escape(label))
    return ", ".join(labels) or "-"


def _render_relief_candidates(relief_candidates):
    if (relief_candidates or {}).get("status") != "completed":
        return ""
    items = relief_candidates.get("items") or []
    rows = []
    for item in items:
        relief_type = str(item.get("relief_type") or "")
        if relief_type not in {"vent", "drain", "bleed", "uncertain"}:
            continue
        label = html.escape(str(item.get("tag") or item.get("id") or "candidate"))
        classification = html.escape(str(item.get("classified_by") or "deterministic"))
        confidence = html.escape(str(item.get("classification_confidence") or ""))
        basis = html.escape(str(item.get("basis") or ""))
        rows.append(
            f"<li><b>{html.escape(relief_type)}</b>: {label}; classified by {classification}; confidence {confidence}. {basis}</li>"
        )
    if not rows:
        return ""
    return '<div class="coverage"><h3>Stored-Energy Relief Candidates</h3><ul>' + "".join(rows) + "</ul></div>"


def _render_status_callout(data):
    status, tone, _pill, title = _status_entry(data)
    css = _STATUS_CALLOUT_CSS[tone]
    summary = (data.get("isolation_obligations") or {}).get("summary") or {}
    unresolved = int(summary.get("unresolved_count") or 0)
    manual = int(summary.get("manual_candidate_count") or 0)

    if status == "not_isolated" and unresolved:
        detail = f"{unresolved} process path still needs a selected isolation point before this can be treated as isolated."
    elif status == "not_isolated":
        detail = "The available evidence does not show a complete isolation boundary for the selected equipment."
    elif status == "provisional_unproven_isolation" and manual:
        detail = f"The graph found an isolation boundary, but {manual} additional field/manual check must be resolved before work proceeds."
    elif status == "provisional_unproven_isolation":
        detail = "The graph found an isolation boundary, but required field verification or proof of zero energy is still missing."
    elif tone == "good":
        detail = "All detected process paths have selected isolation points in the available graph and drawing data."
    else:
        detail = "The payload did not include enough validation information to state the isolation status."

    return (
        f'<div class="status-callout {css}">'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(detail)}</p>"
        "</div>"
    )


def _render_step_details(row):
    details = []
    for label, key in (
        ("Purpose", "purpose"),
        ("Meaning", "interpretation"),
        ("Accept", "acceptance_criteria"),
        ("Limit", "limitation"),
    ):
        value = str(row.get(key) or "").strip()
        if value:
            details.append(f"<li><b>{html.escape(label)}:</b> {html.escape(value)}</li>")
    if not details:
        return ""
    return f'<ul class="step-detail">{"".join(details)}</ul>'


def _render_release_from_isolation(release_ref, release, restoration_checks):
    if not restoration_checks:
        return f'<p class="release"><b>Release from isolation ({release_ref}):</b> {release}</p>'
    items = []
    for check in restoration_checks:
        action = html.escape(str(check.get("action") or ""))
        items.append(f"<li>{action}{_render_step_details(check)}</li>")
    return (
        f'<div class="release"><b>Release from isolation ({release_ref}):</b> {release}'
        '<h3>Restoration / Re-Energization Checks</h3>'
        f'<ul>{"".join(items)}</ul></div>'
    )


def _render_procedure_warnings(unselected_sources, manual_checks, context_instruments, downstream_impact, obligations):
    rows = []
    unresolved_obligations = [
        item
        for item in (obligations.get("items") or [])
        if item.get("source_type") == SourceType.PROCESS.value and item.get("status") == ObligationStatus.UNRESOLVED.value
    ]
    manual_candidate_count = _unique_manual_candidate_count(obligations)
    if unresolved_obligations:
        labels = ", ".join(html.escape(_obligation_source_label(item)) for item in unresolved_obligations)
        rows.append(
            "Unresolved process isolation obligation: "
            f"{len(unresolved_obligations)} process source path(s) still require selected isolation: {labels}."
        )
    if manual_candidate_count:
        rows.append(
            "Manual bypass/parallel-route check required: "
            f"{manual_candidate_count} additional same-source isolation candidate(s) are highlighted in orange. "
            "Confirm whether each is a required bypass/parallel closure."
        )
    if unselected_sources and obligations.get("status") != "completed":
        source_labels = ", ".join(html.escape(_source_warning_label(item)) for item in unselected_sources)
        rows.append(
            "Incomplete isolation boundary: "
            f"{len(unselected_sources)} source path(s) have no selected drawable isolation candidate: {source_labels}. "
            "Manual field/UI resolution is required before this can be treated as isolated."
        )
    if manual_checks:
        rows.append(
            "Manual visual isolation check required: "
            f"{len(manual_checks)} possible unclassified parallel-branch valve(s) were detected visually/textually "
            "but not parsed as valve symbols. Confirm and close/lock these branches in the field/UI if applicable."
        )
    if context_instruments:
        rows.append(
            "Boundary context: "
            f"{len(context_instruments)} nozzle/source path(s) were classified as non-process context and not counted "
            "as process isolation boundaries."
        )

    downstream_rows = _render_downstream_impact_items(downstream_impact)
    if not rows and not downstream_rows:
        return ""
    warning_items = "".join(f"<li>{item}</li>" for item in rows)
    return (
        '<div class="alerts"><h3>Warnings and Required Field Holds</h3>'
        f"<ul>{warning_items}{downstream_rows}</ul></div>"
    )


def _render_isolation_coverage(obligations):
    if (obligations or {}).get("status") != "completed":
        return ""
    summary = obligations.get("summary") or {}
    items = obligations.get("items") or []
    rows = []
    for item in items:
        status = html.escape(str(item.get("status") or "unknown"))
        source = html.escape(_obligation_source_label(item))
        source_type = html.escape(str(item.get("source_type") or "unknown").replace("_", " "))
        selected = ", ".join(html.escape(str(value)) for value in item.get("selected_candidate_ids") or []) or "-"
        manual_count = len(item.get("manual_candidates") or [])
        manual = f"; {manual_count} manual candidate(s)" if manual_count else ""
        rows.append(
            f"<li><b>{source}</b>: {status} ({source_type}); selected: {selected}{manual}.</li>"
        )
    if not rows:
        return ""
    meta = (
        f"{summary.get('isolated_count', 0)} isolated / "
        f"{summary.get('process_obligation_count', 0)} process obligation(s); "
        f"{summary.get('unresolved_count', 0)} unresolved."
    )
    return (
        '<div class="coverage"><h3>Isolation Coverage</h3>'
        f'<p class="meta">{html.escape(meta)}</p><ul>'
        + "\n".join(rows)
        + "</ul></div>"
    )


def _unique_manual_candidate_count(obligations):
    seen = set()
    for item in (obligations or {}).get("items") or []:
        for candidate in item.get("manual_candidates") or []:
            bbox = _valid_bbox(candidate.get("bbox"))
            if not bbox:
                continue
            seen.add((str(candidate.get("uuid") or ""), tuple(bbox)))
    return len(seen)


def _render_downstream_impact_items(downstream_impact):
    if not downstream_impact:
        return ""
    status = downstream_impact.get("status")
    if status == "unavailable":
        error = html.escape(str(downstream_impact.get("error") or "HILT impact analysis unavailable"))
        return f"<li>Downstream impact unavailable: {error}.</li>"
    warnings = downstream_impact.get("warnings") or []
    if status != "completed" or not warnings:
        return ""

    groups = [
        (ImpactSeverity.LIKELY.value, "Likely affects"),
        (ImpactSeverity.POSSIBLE.value, "May affect"),
    ]
    rows = []
    for severity, title in groups:
        items = [item for item in warnings if item.get("severity") == severity]
        if not items:
            continue
        for item in items:
            source = html.escape(str(item.get("source_tag") or item.get("source_candidate_tag") or "selected barrier"))
            affected = html.escape(str(item.get("affected_tag") or "unknown"))
            affected_class = html.escape(str(item.get("affected_class") or item.get("affected_type") or "component"))
            hops = html.escape(str(item.get("path_hops") or ""))
            wording = "likely affects" if severity == ImpactSeverity.LIKELY.value else "may affect"
            rows.append(
                f'<li class="{severity}">Downstream impact ({html.escape(title)}): '
                f"{source} {wording} {affected} ({affected_class}); HILT path hops: {hops}.</li>"
            )
    return "".join(rows)
