import html
import json


def build_final_payload(validation_data, config):
    candidates = validation_data.get("candidates", []) or []
    context = validation_data.get("context") or config.context
    manual_visual_checks = validation_data.get("manual_visual_isolation_checks") or []
    context_instruments = (validation_data.get("isolation_validation") or {}).get("context_instruments") or validation_data.get("context_instruments") or []
    boundary_context_sources = (validation_data.get("isolation_validation") or {}).get("boundary_context_sources") or validation_data.get("boundary_context_sources") or context_instruments
    isolation_points = []
    for candidate in candidates:
        properties = candidate.get("properties", {}) or {}
        isolation_points.append(
            {
                "equipment_id": candidate.get("equipment_tag"),
                "uuid": str(candidate.get("candidate_id")),
                "bbox": candidate.get("bbox") or [],
                "entity_class": properties.get("entity_class") or candidate.get("candidate_label"),
                "tag_number": candidate.get("tag_number"),
                "energy_type": (candidate.get("energy_type") or ["process"])[0],
                "isolation_method": candidate.get("isolation_method"),
                "reason": f"{candidate.get('reason')}. Candidate vertex id: {candidate.get('candidate_id')}. Source component: {candidate.get('source_component_tag')}.",
            }
        )
    return {
        "error": False,
        "message": "Completed",
        "debug": validation_data.get("debug", {}),
        "data": [
            {
                "job_id": _int_or_text(context.get("job_id")),
                "job_name": context.get("job_name"),
                "project_id": _int_or_text(context.get("project_id")),
                "project_name": f"Project {context.get('project_id')}",
                "collection_id": _int_or_text(context.get("collection_id")),
                "collection_name": context.get("collection_name"),
                "selected_equipment": [config.equipment_tag],
                "input_details": {**context, "selected_equipment": [config.equipment_tag], "target_mode": "selected_equipment"},
                "assurance_status": validation_data.get("assurance_status"),
                "isolation_validation": validation_data.get("isolation_validation"),
                "unselected_boundary_sources": (validation_data.get("isolation_validation") or {}).get("unselected_boundary_sources") or [],
                "boundary_context_sources": boundary_context_sources,
                "context_instruments": context_instruments,
                "manual_visual_isolation_checks": manual_visual_checks,
                "isolation_points": isolation_points,
            }
        ],
    }


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_viewer(path, payload, image_url=""):
    data = payload.get("data", [{}])[0]
    points = data.get("isolation_points", [])
    unselected_sources = data.get("unselected_boundary_sources", []) or []
    context_instruments = data.get("boundary_context_sources", []) or data.get("context_instruments", []) or []
    manual_checks = data.get("manual_visual_isolation_checks", []) or []
    # Map each isolation device uuid -> its sequence number in the LOTO Phase 3
    # (isolation) order, so we can badge the bboxes 1,2,3... in closure order.
    seq_by_uuid = _isolation_sequence(data.get("loto_procedure"))
    drawable_points = []
    for point in points:
        bbox = point.get("bbox") or []
        if len(bbox) != 4:
            continue
        drawable_points.append((point, bbox))

    if drawable_points:
        min_x = min(int(point_bbox[0]) for _, point_bbox in drawable_points)
        min_y = min(int(point_bbox[1]) for _, point_bbox in drawable_points)
        max_x = max(int(point_bbox[0]) + int(point_bbox[2]) for _, point_bbox in drawable_points)
        max_y = max(int(point_bbox[1]) + int(point_bbox[3]) for _, point_bbox in drawable_points)
    else:
        min_x = min_y = max_x = max_y = 0

    padding = 180
    scroll_x = max(min_x - padding, 0)
    scroll_y = max(min_y - padding, 0)
    if image_url and not drawable_points:
        scroll_y = 850

    if image_url or not drawable_points:
        offset_x = 0
        offset_y = 0
        canvas_width = 5458
        canvas_height = 3109
    else:
        offset_x = scroll_x
        offset_y = scroll_y
        canvas_width = max(max_x - offset_x + padding, 640)
        canvas_height = max(max_y - offset_y + padding, 420)

    boxes = []
    summary_rows = []
    for point, bbox in drawable_points:
        x, y, w, h = [int(value) for value in bbox]
        display_x = x - offset_x
        display_y = y - offset_y
        seq = seq_by_uuid.get(str(point.get("uuid")))
        label = point.get("tag_number") or point.get("entity_class") or point.get("uuid")
        if seq:
            label = f"#{seq}  {label}"
        title = f"{label} | uuid={point.get('uuid')} | bbox={bbox}"
        boxes.append(
            f'<div class="box" style="left:{display_x}px;top:{display_y}px;width:{w}px;height:{h}px;" title="{html.escape(str(title))}"></div>'
        )
        boxes.append(
            f'<div class="label" style="left:{display_x}px;top:{max(display_y - 22, 0)}px;">{html.escape(str(label))}</div>'
        )
        if seq:
            badge_left = display_x - 17
            badge_top = max(display_y - 17, 0)
            boxes.append(
                f'<div class="seq-badge" style="left:{badge_left}px;top:{badge_top}px;" '
                f'title="Isolation step {seq}">{seq}</div>'
            )
        summary_rows.append(
            "<tr>"
            f"<td>{html.escape(str(seq or ''))}</td>"
            f"<td>{html.escape(str(point.get('uuid')))}</td>"
            f"<td>{html.escape(str(label))}</td>"
            f"<td>{html.escape(str(bbox))}</td>"
            f"<td>{html.escape(str(point.get('reason') or ''))}</td>"
            "</tr>"
        )
    for check in manual_checks:
        bbox = check.get("bbox") or []
        if len(bbox) != 4:
            continue
        x, y, w, h = [int(value) for value in bbox]
        display_x = x - offset_x
        display_y = y - offset_y
        label = "manual check"
        title = f"{check.get('entity_class')} | uuid={check.get('uuid')} | bbox={bbox}"
        boxes.append(
            f'<div class="manual-box" style="left:{display_x}px;top:{display_y}px;width:{w}px;height:{h}px;" title="{html.escape(str(title))}"></div>'
        )
        boxes.append(
            f'<div class="manual-label" style="left:{display_x}px;top:{max(display_y - 22, 0)}px;">{html.escape(label)}</div>'
        )
        summary_rows.append(
            "<tr>"
            f"<td></td>"
            f"<td>{html.escape(str(check.get('uuid')))}</td>"
            f"<td>{html.escape(str(check.get('entity_class') or label))}</td>"
            f"<td>{html.escape(str(bbox))}</td>"
            f"<td>{html.escape(str(check.get('reason') or ''))}</td>"
            "</tr>"
        )
    for context in context_instruments:
        bbox = context.get("source_bbox") or []
        if len(bbox) != 4:
            continue
        x, y, w, h = [int(value) for value in bbox]
        display_x = x - offset_x
        display_y = y - offset_y
        label = context.get("source_component_tag") or "boundary context"
        title = f"boundary context | source={label} | class={context.get('classification')} | bbox={bbox}"
        boxes.append(
            f'<div class="context-box" style="left:{display_x}px;top:{display_y}px;width:{w}px;height:{h}px;" title="{html.escape(str(title))}"></div>'
        )
        boxes.append(
            f'<div class="context-label" style="left:{display_x}px;top:{max(display_y - 22, 0)}px;">{html.escape(str(label))}</div>'
        )
        summary_rows.append(
            "<tr>"
            f"<td></td>"
            f"<td>{html.escape(str(context.get('source_component')))}</td>"
            f"<td>{html.escape(str(label))}</td>"
            f"<td>{html.escape(str(bbox))}</td>"
            f"<td>{html.escape(str(context.get('reason') or ''))}</td>"
            "</tr>"
        )
    image = (
        f'<img src="{html.escape(image_url)}" />'
        if image_url
        else f'<div class="blank" style="width:{canvas_width}px;height:{canvas_height}px;">Focused no-image view. Original offset: x={offset_x}, y={offset_y}. Use --image-url for full P&amp;ID background.</div>'
    )
    summary = "" if not summary_rows else (
        '<table><thead><tr><th>Seq</th><th>UUID</th><th>Label</th><th>BBox</th><th>Reason</th></tr></thead><tbody>'
        + "\n".join(summary_rows)
        + "</tbody></table>"
    )
    warning = ""
    if unselected_sources:
        source_labels = ", ".join(
            html.escape(_source_warning_label(item))
            for item in unselected_sources
        )
        warning += (
            '<div class="warning">Incomplete isolation boundary: '
            f'{len(unselected_sources)} source path(s) have no selected drawable isolation candidate: {source_labels}. '
            'Manual field/UI resolution is required before this can be treated as isolated.</div>'
        )
    if manual_checks:
        warning += (
            '<div class="manual-warning">Manual visual isolation check required: '
            f'{len(manual_checks)} possible unclassified parallel-branch valve(s) were detected visually/textually but not parsed as valve symbols. '
            'Confirm and close/lock these branches in the field/UI if applicable.</div>'
        )
    if context_instruments:
        warning += (
            '<div class="context-warning">Boundary context: '
            f'{len(context_instruments)} nozzle/source path(s) were classified as non-process context and not counted as process isolation boundaries.</div>'
        )
    procedure_html = _render_loto_panel(data.get("loto_procedure"))
    path.write_text(
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
.box { position: absolute; border: 3px solid #e11d48; box-sizing: border-box; background: rgba(225,29,72,0.12); }
.seq-badge { position: absolute; width: 26px; height: 26px; line-height: 26px; text-align: center; border-radius: 50%; background: #e11d48; color: #fff; font-weight: 700; font-size: 14px; border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.5); z-index: 5; }
.label { position: absolute; background: #111827; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.manual-box { position: absolute; border: 3px dashed #f59e0b; box-sizing: border-box; background: rgba(245,158,11,0.18); }
.manual-label { position: absolute; background: #92400e; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.manual-warning { margin: 0 0 16px; padding: 10px 12px; border: 1px solid #f59e0b; background: #fff7ed; color: #9a3412; font-weight: 600; }
.context-box { position: absolute; border: 2px solid #2563eb; box-sizing: border-box; background: rgba(37,99,235,0.14); }
.context-label { position: absolute; background: #1d4ed8; color: white; font-size: 12px; padding: 3px 6px; white-space: nowrap; border-radius: 3px; }
.context-warning { margin: 0 0 16px; padding: 10px 12px; border: 1px solid #60a5fa; background: #eff6ff; color: #1e40af; font-weight: 600; }
.loto { margin-top: 20px; max-width: 1200px; border: 1px solid #d1d5db; border-radius: 6px; padding: 14px 18px; background: #fff; }
.loto h2 { margin: 0 0 4px; font-size: 16px; }
.loto .meta { margin: 0 0 10px; }
.loto ol { margin: 6px 0 6px 18px; padding: 0; }
.loto li { margin: 4px 0; font-size: 13px; line-height: 1.4; }
.loto .phase { display:inline-block; min-width: 230px; color: #1d4ed8; font-weight: 600; font-size: 12px; }
.loto .field-gap { color: #b45309; font-weight: 600; }
.loto .release { margin-top: 10px; font-size: 12px; color: #4b5563; border-top: 1px dashed #d1d5db; padding-top: 8px; }
table { border-collapse: collapse; margin-top: 16px; max-width: 1200px; font-size: 13px; }
th, td { border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
</style>
</head>
<body>
<h1>Equipment Isolation Overlay</h1>
<p class="meta">Boxes: BOX_COUNT. Assurance: ASSURANCE_STATUS. Scroll the image pane horizontally or vertically to inspect the full P&amp;ID.</p>
WARNING_BLOCK
<div id="imagePane" class="canvas-wrap" data-scroll-x="SCROLL_X" data-scroll-y="SCROLL_Y">
<div class="canvas">
"""
        .replace("BOX_COUNT", str(len(drawable_points)))
        .replace("ASSURANCE_STATUS", html.escape(str(data.get("assurance_status"))))
        .replace("WARNING_BLOCK", warning)
        .replace("SCROLL_X", str(scroll_x))
        .replace("SCROLL_Y", str(scroll_y))
        + image
        + "\n"
        + "\n".join(boxes)
        + "\n</div>\n</div>\n"
        + summary
        + procedure_html
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


def _int_or_text(value):
    try:
        return int(value)
    except Exception:
        return value


def _source_warning_label(item):
    label = str(item.get("source_component_tag") or "").strip()
    if label:
        return label
    if item.get("source_label_confidence") == "graph_only_unlabeled_component":
        return "unlabeled graph-only source"
    return str(item.get("source_component") or "unknown")


def _isolation_sequence(procedure):
    """Return {device_uuid: sequence_number} for the LOTO Phase 3 (isolation)
    order -- i.e. the order in which valves are closed. Used to badge bboxes."""
    if not procedure:
        return {}
    seq_by_uuid = {}
    seq = 0
    for step in procedure.get("ordered_steps") or []:
        if step.get("phase") == 3 and step.get("device_uuid"):
            seq += 1
            seq_by_uuid[str(step["device_uuid"])] = seq
    return seq_by_uuid


def _render_loto_panel(procedure):
    if not procedure:
        return ""
    steps = procedure.get("ordered_steps") or []
    if not steps:
        return ""
    items = []
    for step in steps:
        cls = ' class="field-gap"' if step.get("field_gap") else ""
        phase = (
            f'<span class="phase">[Phase {step.get("phase")} | '
            f'{html.escape(str(step.get("ref") or ""))}] {html.escape(str(step.get("title") or ""))}</span>'
        )
        items.append(f'<li{cls}>{phase} {html.escape(str(step.get("action") or ""))}</li>')
    standard = html.escape(str(procedure.get("standard") or "29 CFR 1910.147"))
    release_ref = html.escape(str(procedure.get("release_from_loto_ref") or "1910.147(e)"))
    release = html.escape(str(procedure.get("release_note") or ""))
    order_source = procedure.get("within_phase_order_source") or "engine_candidate_order_not_proposed"
    if order_source == "agent_engineering_judgment":
        order_note = (
            "Within-phase device order is the agent's engineering judgment "
            "(OSHA does NOT prescribe which valve to close first -- only the phase order is regulated)."
        )
    elif order_source == "flow_grounding_inlet_first_default":
        order_note = (
            "Within-phase device order is a flow-grounded default (isolate INLET/upstream first, then outlet), "
            "derived from the P&ID flow direction parsed by the HILT graph. "
            "OSHA does NOT prescribe which valve to close first -- only the phase order is regulated."
        )
    else:
        order_note = (
            "Within-phase device order NOT yet proposed by the agent (shown in engine candidate order). "
            "OSHA prescribes only the phase order, not the within-phase device order."
        )
    return (
        f'<div class="loto"><h2>LOTO Procedure &mdash; OSHA {standard}(d)</h2>'
        f'<p class="meta">{html.escape(order_note)}</p>'
        '<ol>' + "\n".join(items) + '</ol>'
        f'<p class="release"><b>Release ({release_ref}):</b> {release}</p>'
        '</div>'
    )
