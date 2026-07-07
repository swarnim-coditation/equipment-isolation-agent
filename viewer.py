import html
from dataclasses import dataclass


CANVAS_WIDTH = 5458
CANVAS_HEIGHT = 3109
VIEW_PADDING = 180


@dataclass(frozen=True)
class Overlay:
    kind: str
    bbox: list[int]
    label: str
    title: str
    css_class: str
    label_class: str
    summary_seq: str
    summary_uuid: str
    summary_reason: str
    severity: str = ""
    badge: str = ""


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
    )


def _collect_overlays(data):
    seq_by_uuid = _isolation_sequence(data.get("loto_procedure"))
    overlays = []
    overlays.extend(_collect_target_overlays(data))
    overlays.extend(_collect_isolation_overlays(data, seq_by_uuid))
    overlays.extend(_collect_impact_overlays(data))
    overlays.extend(_collect_obligation_manual_overlays(data))
    overlays.extend(_collect_manual_check_overlays(data))
    overlays.extend(_collect_context_overlays(data))
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
                kind="target",
                bbox=bbox,
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
        label = str(point.get("tag_number") or point.get("entity_class") or point.get("uuid") or "")
        if seq:
            label = f"#{seq}  {label}"
        title = f"{label} | uuid={point.get('uuid')} | bbox={bbox}"
        overlays.append(
            Overlay(
                kind="isolation",
                bbox=bbox,
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


def _collect_impact_overlays(data):
    overlays = []
    warnings = ((data.get("downstream_impact") or {}).get("warnings") or [])
    for index, warning_item in enumerate([item for item in warnings if _valid_bbox(item.get("affected_bbox"))], start=1):
        bbox = _minimum_display_bbox(_valid_bbox(warning_item.get("affected_bbox")), min_width=86, min_height=30)
        severity = str(warning_item.get("severity") or "possible")
        raw_label = warning_item.get("affected_tag") or warning_item.get("affected_id") or "downstream impact"
        source = warning_item.get("source_tag") or warning_item.get("source_candidate_tag") or "selected barrier"
        wording = "likely affects" if severity == "likely" else "may affect"
        title = (
            f"Downstream impact | {source} {wording} {raw_label} | "
            f"class={warning_item.get('affected_class')} | original_bbox={warning_item.get('affected_bbox')}"
        )
        overlays.append(
            Overlay(
                kind="impact",
                bbox=bbox,
                label=_impact_overlay_label(warning_item, index),
                title=title,
                css_class=f"impact-box impact-{severity}",
                label_class="impact-label",
                summary_seq="",
                summary_uuid=str(warning_item.get("affected_id") or ""),
                summary_reason=title,
                severity=severity,
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
                kind="manual",
                bbox=bbox,
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
            tag = str(candidate.get("tag_number") or candidate.get("entity_class") or "candidate")
            title = (
                f"Manual isolation candidate | source={source} | candidate={tag} | "
                f"uuid={candidate.get('uuid')} | bbox={bbox}"
            )
            overlays.append(
                Overlay(
                    kind="obligation_manual",
                    bbox=bbox,
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
                kind="context",
                bbox=bbox,
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


def _render_summary_table(overlays):
    if not overlays:
        return ""
    rows = []
    for item in overlays:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.summary_seq)}</td>"
            f"<td>{html.escape(item.summary_uuid)}</td>"
            f"<td>{html.escape(item.label)}</td>"
            f"<td>{html.escape(str(item.bbox))}</td>"
            f"<td>{html.escape(item.summary_reason)}</td>"
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Seq</th><th>UUID</th><th>Label</th><th>BBox</th><th>Reason</th></tr></thead><tbody>'
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


def _render_document(data, image, overlays, viewport, procedure_html, summary_html):
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
.canvas-wrap { width: 100%; max-height: 78vh; overflow: auto; border: 1px solid #d1d5db; background: #f9fafb; }
.canvas { position: relative; display: inline-block; }
.canvas img { display: block; width: auto; height: auto; max-width: none; }
.blank { background: #fafafa; color: #555; display:flex; align-items:center; justify-content:center; }
.target-box { position: absolute; border: 4px solid #eab308; box-sizing: border-box; background: rgba(234,179,8,0.18); z-index: 3; }
.target-label { position: absolute; background: #a16207; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 7; }
.box { position: absolute; border: 3px solid #2563eb; box-sizing: border-box; background: rgba(37,99,235,0.12); }
.seq-badge { position: absolute; width: 26px; height: 26px; line-height: 26px; text-align: center; border-radius: 50%; background: #2563eb; color: #fff; font-weight: 700; font-size: 14px; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.5); z-index: 5; }
.label { position: absolute; background: #111827; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.impact-box { position: absolute; border: 4px solid #dc2626; box-sizing: border-box; background: rgba(220,38,38,0.16); z-index: 4; }
.impact-possible { border-style: dashed; background: rgba(220,38,38,0.10); }
.impact-label { position: absolute; background: #991b1b; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; z-index: 6; }
.manual-box { position: absolute; border: 3px dashed #f59e0b; box-sizing: border-box; background: rgba(245,158,11,0.18); }
.manual-label { position: absolute; background: #92400e; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.context-box { position: absolute; border: 2px solid #2563eb; box-sizing: border-box; background: rgba(37,99,235,0.14); }
.context-label { position: absolute; background: #1d4ed8; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.procedure { margin: 0 0 18px; max-width: 1200px; border: 1px solid #d1d5db; border-radius: 6px; padding: 14px 18px; background: #fff; }
.procedure h2 { margin: 0 0 4px; font-size: 17px; }
.procedure h3 { margin: 14px 0 6px; font-size: 13px; color: #111827; }
.procedure .meta { margin: 0 0 10px; }
.procedure ol, .procedure ul { margin: 6px 0 6px 18px; padding: 0; }
.procedure li { margin: 4px 0; font-size: 13px; line-height: 1.4; }
.procedure .phase { display:inline-block; min-width: 230px; color: #1d4ed8; font-weight: 600; font-size: 12px; }
.procedure .field-gap { color: #b45309; font-weight: 600; }
.procedure .release { margin-top: 10px; font-size: 12px; color: #4b5563; border-top: 1px dashed #d1d5db; padding-top: 8px; }
.procedure .alerts { margin-top: 12px; padding: 10px 12px; border: 1px solid #f59e0b; background: #fffbeb; color: #7c2d12; }
.procedure .alerts h3 { margin-top: 0; color: #7c2d12; }
.procedure .coverage { margin-top: 12px; padding: 10px 12px; border: 1px solid #d1d5db; background: #f9fafb; color: #111827; }
.procedure .coverage h3 { margin-top: 0; }
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
        + f'<p class="meta">Boxes: {len(overlays)}. Assurance: {html.escape(str(data.get("assurance_status")))}. '
        + "Scroll the image pane horizontally or vertically to inspect the full P&amp;ID.</p>\n"
        + procedure_html
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
    steps = (procedure or {}).get("ordered_steps") or []
    if not steps and not warning_html:
        return ""

    items = []
    for step in steps:
        cls = ' class="field-gap"' if step.get("field_gap") else ""
        phase = (
            f'<span class="phase">[Phase {step.get("phase")} | '
            f'{html.escape(str(step.get("ref") or ""))}] {html.escape(str(step.get("title") or ""))}</span>'
        )
        items.append(f'<li{cls}>{phase} {html.escape(str(step.get("action") or ""))}</li>')

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
    steps_html = ""
    if items:
        steps_html = "<h3>Ordered Isolation Steps</h3><ol>" + "\n".join(items) + "</ol>"
    else:
        steps_html = '<h3>Ordered Isolation Steps</h3><p class="meta">No ordered procedure steps are present in this payload.</p>'

    assurance = html.escape(str(data.get("assurance_status") or "unknown"))
    selected = ", ".join(html.escape(str(item)) for item in data.get("selected_equipment") or [])
    return (
        f'<div class="procedure"><h2>Isolation Procedure</h2>'
        f'<p class="meta">Equipment: {selected or "unknown"}. Assurance: {assurance}. '
        f'Procedure basis: OSHA {standard}(d).</p>'
        f'{warning_html}'
        f'{coverage_html}'
        f'<h3>Sequencing Basis</h3><p class="meta">{html.escape(order_note)}</p>'
        f'{steps_html}'
        f'<p class="release"><b>Release from isolation ({release_ref}):</b> {release}</p>'
        "</div>"
    )


def _render_procedure_warnings(unselected_sources, manual_checks, context_instruments, downstream_impact, obligations):
    rows = []
    unresolved_obligations = [
        item
        for item in (obligations.get("items") or [])
        if item.get("source_type") == "process" and item.get("status") == "unresolved"
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
        ("likely", "Likely affects"),
        ("possible", "May affect"),
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
            wording = "likely affects" if severity == "likely" else "may affect"
            rows.append(
                f'<li class="{severity}">Downstream impact ({html.escape(title)}): '
                f"{source} {wording} {affected} ({affected_class}); HILT path hops: {hops}.</li>"
            )
    return "".join(rows)
