import json

from viewer import render_viewer_html


def build_final_payload(validation_data, config, downstream_impact=None):
    candidates = validation_data.get("candidates", []) or []
    context = validation_data.get("context") or config.context
    manual_visual_checks = validation_data.get("manual_visual_isolation_checks") or []
    context_instruments = (validation_data.get("isolation_validation") or {}).get("context_instruments") or validation_data.get("context_instruments") or []
    boundary_context_sources = (validation_data.get("isolation_validation") or {}).get("boundary_context_sources") or validation_data.get("boundary_context_sources") or context_instruments
    selected_equipment_overlays = validation_data.get("selected_equipment_overlays") or []
    isolation_obligations = validation_data.get("isolation_obligations") or (validation_data.get("isolation_validation") or {}).get("isolation_obligations") or {}
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
                "selected_equipment_overlays": selected_equipment_overlays,
                "isolation_points": isolation_points,
                "isolation_obligations": isolation_obligations,
                "downstream_impact": downstream_impact or validation_data.get("downstream_impact"),
            }
        ],
    }


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_viewer(path, payload, image_url=""):
    path.write_text(render_viewer_html(payload, image_url=image_url), encoding="utf-8")


def _int_or_text(value):
    try:
        return int(value)
    except Exception:
        return value
