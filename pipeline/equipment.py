"""Equipment listing helpers shared by the CLI and API."""
from __future__ import annotations

from api_client import Plant360Client
from config import JOB_IDS_BY_NAME
from domain.hilt_geometry import extract_symbols as _extract_symbols
from graph_client import GraphClient, normalize_vertex, props_only, vertex_id
from pipeline.job_inference import _first_value, _norm


def list_equipment(graph_config, limit=0):
    with GraphClient(graph_config) as client:
        rows = [normalize_vertex(row) for row in client.g.V().hasLabel("Equipment").valueMap(True).toList()]

    items = []
    for row in rows:
        props = props_only(row)
        tag = _first_value(props, ("tag", "tag_number", "Equipment Name", "name", "equipment_number"))
        name = _first_value(props, ("name", "Equipment Name", "label"))
        entity_class = _first_value(props, ("entity_class", "class", "type"))
        items.append(
            {
                "id": vertex_id(row),
                "tag": tag or "",
                "name": name or "",
                "entity_class": entity_class or "",
                "node_id": props.get("node_id") or "",
                "job_id": "",
                "job_name": "",
            }
        )

    items.sort(key=lambda item: (str(item["tag"] or item["name"] or ""), str(item["id"])))
    return items[:limit] if limit and limit > 0 else items


def add_equipment_jobs(items, api_config, job_ids_by_name=None):
    if not items or not api_config.auth_token:
        return items

    node_to_job = {}
    client = Plant360Client(api_config)
    for job_name, job_id in (job_ids_by_name or JOB_IDS_BY_NAME).items():
        try:
            payload = client.stlm_symbols(job_id)
        except Exception:
            continue
        for symbol in _extract_symbols(payload):
            for value in (symbol.get("uuid"), symbol.get("id"), symbol.get("source_id")):
                key = _norm(value)
                if key:
                    node_to_job.setdefault(key, (job_name, job_id))

    for item in items:
        job = node_to_job.get(_norm(item.get("node_id")))
        if job:
            item["job_name"] = job[0]
            item["job_id"] = job[1]
    return items


def add_equipment_jobs_from_metadata(items, job_ids_by_name):
    if not items or not job_ids_by_name:
        return items
    by_name = {str(name).strip().lower(): (str(name), str(job_id)) for name, job_id in job_ids_by_name.items()}
    for item in items:
        job = by_name.get(str(item.get("job_name") or "").strip().lower())
        if job:
            item["job_name"] = job[0]
            item["job_id"] = job[1]
    return items
