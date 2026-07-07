import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from math import ceil
from pathlib import Path
from urllib.parse import quote

from api_client import Plant360Client


DEFAULT_JOB_CACHE = Path(".plant360_job_cache.json")
JOBS_PAGE_SIZE = 50
JOBS_MAX_WORKERS = 8


def resolve_job_from_boundary(config, boundary_data, cache_path=DEFAULT_JOB_CACHE):
    if config.resolved_job_id:
        return config, {
            **_resolution_context(config, []),
            "job_resolution": "already_configured",
            "job_name": config.job_name,
            "job_id": config.resolved_job_id,
            "fatal": False,
        }

    pnid_names = _pnid_names(boundary_data)
    if not pnid_names:
        return _resolution_failure(
            config,
            pnid_names,
            "missing_pnid_name",
            "No P&ID name was found on the selected equipment boundary.",
            fatal=False,
        )

    for pnid_name in pnid_names:
        mapped_job_id = (config.job_ids_by_name or {}).get(pnid_name)
        if mapped_job_id:
            return (
                replace(config, job_name=pnid_name, job_id=mapped_job_id),
                {
                    **_resolution_context(config, pnid_names),
                    "job_resolution": "profile_mapping",
                    "job_name": pnid_name,
                    "job_id": mapped_job_id,
                    "fatal": False,
                },
            )

    cache = _read_cache(cache_path)
    scope_key = _job_cache_scope(config)
    cached_jobs = (((cache.get("scopes") or {}).get(scope_key) or {}).get("jobs_by_name") or {})
    for pnid_name in pnid_names:
        cached_job_id = cached_jobs.get(pnid_name)
        if cached_job_id:
            return (
                replace(config, job_name=pnid_name, job_id=str(cached_job_id)),
                {
                    **_resolution_context(config, pnid_names),
                    "job_resolution": "cache",
                    "job_name": pnid_name,
                    "job_id": str(cached_job_id),
                    "fatal": False,
                },
            )

    if not config.api.auth_token:
        return _resolution_failure(
            config,
            pnid_names,
            "missing_auth_token",
            "PLANT360_AUTH_TOKEN is required to resolve a P&ID job automatically.",
            fatal=_has_configured_collection(config),
        )

    try:
        resolved = _resolve_from_nested_jobs(config, pnid_names)
    except Exception as exc:
        return _resolution_failure(
            config,
            pnid_names,
            "configured_collection_jobs_api_error",
            f"Configured CNVRT collection job lookup failed: {exc}",
            fatal=_has_configured_collection(config),
        )
    if resolved:
        pnid_name, job = resolved
        job_id = str(job.get("id") or "")
        _write_job_cache(cache_path, scope_key, pnid_name, job_id, job)
        return (
            replace(config, job_name=pnid_name, job_id=job_id),
            {
                **_resolution_context(config, pnid_names),
                "job_resolution": "project_collection_jobs_api",
                "job_name": pnid_name,
                "job_id": job_id,
                "job_input_file_image": _job_input_file_image(job),
                "fatal": False,
            },
        )

    if _has_configured_collection(config):
        return _resolution_failure(
            config,
            pnid_names,
            "job_name_not_found_in_configured_collection",
            (
                "No P&ID job matching the boundary P&ID name was found in the configured "
                f"CNVRT project {config.cnvrt_project_id}, collection {config.collection_id}."
            ),
            fatal=True,
        )

    resolved = _scan_jobs_for_names(config, pnid_names)
    if not resolved:
        return _resolution_failure(
            config,
            pnid_names,
            "job_name_not_found",
            "No P&ID job matching the boundary P&ID name was found.",
            fatal=False,
        )

    pnid_name, job = resolved
    job_id = str(job.get("id") or "")
    _write_job_cache(cache_path, scope_key, pnid_name, job_id, job)
    return (
        replace(config, job_name=pnid_name, job_id=job_id),
        {
            **_resolution_context(config, pnid_names),
            "job_resolution": "jobs_scan",
            "job_name": pnid_name,
            "job_id": job_id,
            "job_input_file_image": _job_input_file_image(job),
            "fatal": False,
        },
    )


def _pnid_names(boundary_data):
    names = []
    for boundary in (boundary_data or {}).get("equipment_boundaries", []) or []:
        _append_pnid(names, ((boundary.get("equipment") or {}).get("properties") or {}).get("pnid"))
        for comp in boundary.get("components", []) or []:
            _append_pnid(names, (comp.get("properties") or {}).get("pnid"))
    return names


def _append_pnid(names, value):
    value = str(value or "").strip()
    if value and value not in names:
        names.append(value)


def _scan_jobs_for_names(config, pnid_names):
    targets = {name.lower(): name for name in pnid_names}
    client = Plant360Client(config.api)
    first_page = client.get_json(f"/jobs?page=1&page_size={JOBS_PAGE_SIZE}")
    for item in first_page.get("results") or []:
        hit = _job_name_hit(item, targets)
        if hit:
            return hit, item

    count = int(first_page.get("count") or 0)
    page_count = max(1, ceil(count / JOBS_PAGE_SIZE))
    if page_count <= 1:
        return None

    def fetch_page(page):
        page_data = client.get_json(f"/jobs?page={page}&page_size={JOBS_PAGE_SIZE}")
        for item in page_data.get("results") or []:
            hit = _job_name_hit(item, targets)
            if hit:
                return hit, item
        return None

    pool = ThreadPoolExecutor(max_workers=JOBS_MAX_WORKERS)
    futures = [pool.submit(fetch_page, page) for page in range(2, page_count + 1)]
    try:
        for future in as_completed(futures):
            result = future.result()
            if result:
                for pending in futures:
                    pending.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                return result
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return None


def _resolve_from_nested_jobs(config, pnid_names):
    if not config.cnvrt_project_id or not config.collection_id:
        return None
    client = Plant360Client(config.api)
    for pnid_name in pnid_names:
        encoded_name = quote(pnid_name)
        data = client.get_json(
            f"/projects/{config.cnvrt_project_id}/collections/{config.collection_id}/jobs?name={encoded_name}"
        )
        for item in data.get("results") or []:
            if str(item.get("name") or "").strip().lower() == pnid_name.lower():
                return pnid_name, item
    return None


def _job_name_hit(item, targets):
    name = str(item.get("name") or "").strip()
    return targets.get(name.lower())


def _read_cache(cache_path):
    path = Path(cache_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_job_cache(cache_path, scope_key, pnid_name, job_id, job):
    path = Path(cache_path)
    cache = _read_cache(path)
    scope = cache.setdefault("scopes", {}).setdefault(str(scope_key), {})
    jobs = scope.setdefault("jobs_by_name", {})
    jobs[pnid_name] = str(job_id)
    details = scope.setdefault("job_details_by_name", {})
    details[pnid_name] = {
        "id": str(job_id),
        "name": job.get("name"),
        "project": job.get("project"),
        "collection": job.get("collection"),
        "input_file_image": _job_input_file_image(job),
        "input_file_type": job.get("input_file_type"),
    }
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _job_input_file_image(job):
    value = job.get("input_file_image")
    if isinstance(value, dict):
        return value.get("id")
    return value


def _has_configured_collection(config):
    return bool(str(config.cnvrt_project_id or "").strip() and str(config.collection_id or "").strip())


def _job_cache_scope(config):
    base_url = str(config.api.base_url or "").rstrip("/")
    project_id = str(config.cnvrt_project_id or "default").strip() or "default"
    collection_id = str(config.collection_id or "default").strip() or "default"
    return f"{base_url}|project={project_id}|collection={collection_id}"


def _resolution_context(config, pnid_names):
    return {
        "pnid_names": list(pnid_names or []),
        "cnvrt_project_id": str(config.cnvrt_project_id or ""),
        "collection_id": str(config.collection_id or ""),
        "api_base_url": str(config.api.base_url or "").rstrip("/"),
    }


def _resolution_failure(config, pnid_names, error, message, fatal=False):
    return config, {
        **_resolution_context(config, pnid_names),
        "job_resolution": "unavailable",
        "job_resolution_error": error,
        "message": message,
        "fatal": bool(fatal),
    }
