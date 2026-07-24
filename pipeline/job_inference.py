"""Job-name / job-id inference from boundary and candidate data.

Extracted verbatim from run.py. This is the FULL inference algorithm: candidate
job properties, then boundary-derived unit names, then an STLM lookup fallback.

Both runners now share this: run.py calls it directly and
``AgentSession.infer_job_from_candidates`` delegates to it (D2). It used to have a
reduced candidate-only variant in agent/session.py; that is gone.

``_norm`` here is intentionally NOT domain.topology.normalize_tag: it lower-cases
and strips but does NOT fold spaces/dashes to underscores. Merging them would
change job-name matching. tests/test_geometry_helpers.py pins the difference.
"""
from __future__ import annotations

from dataclasses import replace

from api_client import Plant360Client
from config import JOB_IDS_BY_NAME
from domain.hilt_geometry import extract_symbols as _extract_symbols


def _first_value(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return None


def _norm(value):
    return str(value or "").strip().lower()


def _config_with_inferred_job(config, candidate_data, boundary_data=None):
    counts = {}
    for candidate in candidate_data.get("candidates", []) or []:
        job_name = _candidate_job_name(candidate, config.job_ids_by_name)
        if job_name:
            counts[job_name] = counts.get(job_name, 0) + 1
    for job_name in _boundary_job_names(boundary_data, config.job_ids_by_name):
        counts[job_name] = counts.get(job_name, 0) + 1
    if not counts:
        stlm_job_name = _infer_job_name_from_stlm(config, candidate_data, boundary_data)
        if stlm_job_name:
            counts[stlm_job_name] = counts.get(stlm_job_name, 0) + 1
    if not counts:
        return config
    inferred_job_name = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    if inferred_job_name == config.job_name:
        return config
    inferred_job_id = config.job_ids_by_name.get(inferred_job_name, "") or JOB_IDS_BY_NAME.get(inferred_job_name, "")
    debug = candidate_data.setdefault("debug", {})
    debug["input_job_name"] = config.job_name
    debug["input_job_id"] = config.resolved_job_id
    debug["inferred_job_name"] = inferred_job_name
    debug["inferred_job_id"] = inferred_job_id
    debug["inferred_job_source"] = "selected_candidate_or_boundary_context"
    return replace(config, job_name=inferred_job_name, job_id=inferred_job_id)


def _candidate_job_name(candidate, job_ids_by_name=None):
    properties = candidate.get("properties") or {}
    known_jobs = job_ids_by_name or JOB_IDS_BY_NAME
    for key in ("unit_name", "pnid", "pnid_name", "job_name", "job"):
        value = str(properties.get(key) or candidate.get(key) or "").strip()
        if value in known_jobs:
            return value
    return ""


def _boundary_job_names(boundary_data, job_ids_by_name=None):
    if not boundary_data:
        return []
    known_jobs = job_ids_by_name or JOB_IDS_BY_NAME
    names = []
    for props in _boundary_properties(boundary_data):
        for key in ("unit_name", "pnid", "pnid_name", "job_name", "job"):
            value = str(props.get(key) or "").strip()
            if value in known_jobs:
                names.append(value)
    return names


def _infer_job_name_from_stlm(config, candidate_data, boundary_data):
    if config.resolved_job_id or not config.api.auth_token:
        return ""
    ids = _job_lookup_ids(candidate_data, boundary_data)
    if not ids:
        return ""

    client = Plant360Client(config.api)
    for job_name, job_id in (config.job_ids_by_name or JOB_IDS_BY_NAME).items():
        try:
            payload = client.stlm_symbols(job_id)
        except Exception:
            continue
        for symbol in _extract_symbols(payload):
            for key in ("uuid", "id", "source_id", "associated_equipment_id", "parent"):
                if _norm(symbol.get(key)) in ids:
                    return job_name
    return ""


def _job_lookup_ids(candidate_data, boundary_data):
    ids = set()
    for candidate in candidate_data.get("candidates", []) or []:
        for value in (
            candidate.get("visual_id"),
            candidate.get("source_visual_id"),
            candidate.get("candidate_id"),
            candidate.get("source_component_id"),
            (candidate.get("properties") or {}).get("node_id"),
            (candidate.get("properties") or {}).get("source_id"),
            (candidate.get("properties") or {}).get("uuid"),
        ):
            key = _norm(value)
            if key:
                ids.add(key)
    for props in _boundary_properties(boundary_data):
        for key in ("node_id", "source_id", "uuid", "id", "name"):
            value = props.get(key)
            normalized = _norm(value)
            if normalized:
                ids.add(normalized)
    return ids


def _boundary_properties(boundary_data):
    if not boundary_data:
        return []
    properties = []
    for boundary in boundary_data.get("equipment_boundaries", []) or []:
        equipment = boundary.get("equipment") or {}
        props = dict(equipment.get("properties") or {})
        if equipment.get("id") is not None:
            props.setdefault("id", equipment.get("id"))
        properties.append(props)
        for component in boundary.get("components", []) or []:
            props = dict(component.get("properties") or {})
            if component.get("id") is not None:
                props.setdefault("id", component.get("id"))
            properties.append(props)
    return properties
