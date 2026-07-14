from dataclasses import replace

from api_client import Plant360Client
from config import ApiConfig


def enrich_config_from_unigraph(config):
    """Validate configured Unigraph/CNVRT project context and load job ids.

    Unigraph owns the relationship between a project, its CNVRT collections, and
    the CNVRT job ids behind each P&ID. Using that route prevents fallback job
    scans from attaching a same-named drawing from another project.
    """
    debug = {
        "status": "skipped",
        "unigraph_api_base_url": str(config.unigraph_api_base_url or "").rstrip("/"),
        "unigraph_project_id": str(config.graph.project_id or ""),
        "cnvrt_project_id": str(config.cnvrt_project_id or ""),
        "cnvrt_collection_id": str(config.collection_id or ""),
        "job_count": 0,
    }
    if not config.api.auth_token:
        debug.update({"status": "unavailable", "error": "missing_auth_token"})
        return config, debug
    if not config.unigraph_api_base_url or not config.graph.project_id:
        debug.update({"status": "unavailable", "error": "missing_unigraph_project_config"})
        return config, debug

    client = Plant360Client(
        ApiConfig(
            base_url=config.unigraph_api_base_url,
            auth_token=config.api.auth_token,
            verify_ssl=config.api.verify_ssl,
        )
    )

    project_id = str(config.graph.project_id).strip()
    try:
        project = client.get_json(f"/api/projects/{project_id}")
    except Exception as exc:
        debug.update({"status": "unavailable", "error": f"project_lookup_failed: {exc}"})
        return config, debug

    debug["project"] = _project_summary(project)
    if config.cnvrt_project_id:
        mapped_projects = _safe_get_json(
            client,
            f"/api/projects/by-cnvrt?cnvrt_project_id={config.cnvrt_project_id}",
        )
        if isinstance(mapped_projects, list):
            debug["cnvrt_project_matches"] = [_project_summary(item) for item in mapped_projects]
            if mapped_projects and project_id not in {str(item.get("id")) for item in mapped_projects}:
                debug.update(
                    {
                        "status": "failed",
                        "fatal": True,
                        "error": "configured_unigraph_project_not_linked_to_cnvrt_project",
                    }
                )
                return config, debug

    collections = _as_list(client.get_json(f"/api/projects/{project_id}/collections"))
    collection = _select_collection(collections, config.collection_id)
    debug["collections"] = [_collection_summary(item) for item in collections or []]
    if config.collection_id and not collection:
        debug.update(
            {
                "status": "failed",
                "fatal": True,
                "error": "configured_cnvrt_collection_not_found_in_unigraph_project",
            }
        )
        return config, debug
    if not collection:
        debug.update({"status": "unavailable", "error": "no_collection_selected"})
        return config, debug

    unigraph_collection_id = str(collection.get("id") or "")
    debug["selected_collection"] = _collection_summary(collection)
    pnids = _as_list(client.get_json(f"/api/projects/{project_id}/collections/{unigraph_collection_id}/pnids"))
    job_map = {}
    jobs = []
    errors = []
    for item in pnids or []:
        pnid_id = item.get("id")
        if pnid_id in (None, ""):
            continue
        try:
            review = client.get_json(f"/api/projects/{project_id}/pnids/{pnid_id}/direction-review")
        except Exception as exc:
            errors.append({"pnid_id": pnid_id, "error": str(exc)})
            continue
        job_id = str(review.get("cnvrt_job_id") or "").strip()
        pnid_name = str(review.get("pnid_name") or "").strip()
        if not job_id or not pnid_name:
            continue
        job_map[pnid_name] = job_id
        jobs.append(
            {
                "pnid_id": str(review.get("pnid_id") or pnid_id),
                "pnid_name": pnid_name,
                "cnvrt_job_id": job_id,
                "pending_choices": ((review.get("direction_summary") or {}).get("pending_choices")),
            }
        )

    merged_jobs = {**(config.job_ids_by_name or {}), **job_map}
    debug.update(
        {
            "status": "completed",
            "job_count": len(job_map),
            "jobs": jobs,
            "direction_review_errors": errors,
        }
    )
    return replace(config, job_ids_by_name=merged_jobs), debug


def _safe_get_json(client, path):
    try:
        return client.get_json(path)
    except Exception:
        return None


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "results", "data", "collections", "pnids"):
            items = value.get(key)
            if isinstance(items, list):
                return items
    return []


def _select_collection(collections, configured_collection_id):
    configured = str(configured_collection_id or "").strip()
    if not configured:
        return (collections or [None])[0]
    for item in collections or []:
        if str(item.get("cnvrt_collection_id") or "").strip() == configured:
            return item
    for item in collections or []:
        if str(item.get("id") or "").strip() == configured:
            return item
    return None


def _project_summary(item):
    return {
        "id": str((item or {}).get("id") or ""),
        "name": (item or {}).get("name") or "",
        "cnvrt_project_id": str((item or {}).get("cnvrt_project_id") or ""),
        "no_of_collections": (item or {}).get("no_of_collections"),
        "no_of_pnids": (item or {}).get("no_of_pnids"),
        "status": (item or {}).get("status") or "",
    }


def _collection_summary(item):
    return {
        "id": str((item or {}).get("id") or ""),
        "name": (item or {}).get("name") or "",
        "cnvrt_collection_id": str((item or {}).get("cnvrt_collection_id") or ""),
        "export_type": (item or {}).get("export_type") or "",
        "no_of_pnids": (item or {}).get("no_of_pnids"),
    }
