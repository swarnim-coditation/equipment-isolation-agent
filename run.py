import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from bbox import resolve_bboxes
from bbox import _extract_symbols
from boundary import fetch_boundaries
from candidates import find_candidates
from config import (
    ApiConfig,
    GraphConfig,
    IsolationPolicy,
    JOB_IDS_BY_NAME,
    RunConfig,
    WorkScope,
    apply_graph_env,
    apply_project_profile,
    load_project_profile,
)
from evidence import build_evidence
from graph_client import GraphClient, normalize_vertex, props_only, vertex_id
from image import resolve_pid_image
from impact import analyze_downstream_impact
from instrument_context import analyze_instrument_context
from job_resolver import resolve_job_from_boundary
from loto import build_loto_procedure
from obligations import analyze_isolation_obligations
from output import build_final_payload, write_json, write_viewer
from planner import plan_requests
from api_client import Plant360Client
from relief import analyze_isolation_schemes_and_relief
from unigraph_metadata import enrich_config_from_unigraph
from validator import validate


logger = logging.getLogger("local_no_llm")


def load_dotenv():
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_args():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run deterministic equipment isolation locally.")
    parser.add_argument("--equipment", default="", help="Equipment tag, e.g. BT-11")
    parser.add_argument("--list-equipment", action="store_true", help="List available equipment tags and exit")
    parser.add_argument("--equipment-limit", type=int, default=0, help="Maximum equipment rows to list; 0 means all")
    parser.add_argument("--project-config", default="project_config.json", help="Project profile JSON path")
    parser.add_argument("--project-profile", default="", help="Project profile name from --project-config")
    parser.add_argument("--job-name", default="", help="P&ID/job name, e.g. pnid_2_bio_final")
    parser.add_argument("--job-id", default="", help="P&ID/job id, e.g. 2100")
    parser.add_argument("--host", default="", help="Override Gremlin host")
    parser.add_argument("--port", default="", help="Override Gremlin port")
    parser.add_argument("--project-id", default="", help="Override Unigraph project id")
    parser.add_argument("--cnvrt-project-id", default="", help="Override CNVRT project id")
    parser.add_argument("--traversal-source", default="", help="Override Gremlin traversal source alias")
    parser.add_argument("--collection-id", default="", help="Override CNVRT collection id")
    parser.add_argument("--collection-name", default="", help="Override CNVRT collection name")
    parser.add_argument("--api-base-url", default="https://api.plant360.ai:8080")
    parser.add_argument("--unigraph-api-base-url", default="")
    parser.add_argument("--auth-token", default=os.environ.get("PLANT360_AUTH_TOKEN", ""))
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--image-url", default="", help="Optional P&ID image URL for HTML overlay")
    parser.add_argument("--non-intrusive", action="store_true")
    parser.add_argument("--not-high-risk", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Only print final output paths and status")
    args = parser.parse_args()
    if not args.list_equipment and not args.equipment:
        parser.error("--equipment is required unless --list-equipment is used")
    return args


def configure_logging(quiet=False):
    logging.basicConfig(level=logging.WARNING, force=True)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING if quiet else logging.INFO)
    logger.propagate = False


def build_config(args):
    profile = load_project_profile(args.project_config, args.project_profile)
    config = apply_project_profile(
        RunConfig(
            equipment_tag=args.equipment,
            job_name=args.job_name,
            job_id=args.job_id,
            api=ApiConfig(
                base_url=args.api_base_url,
                auth_token=args.auth_token,
                verify_ssl=not args.no_verify_ssl,
            ),
            policy=IsolationPolicy(),
            work_scope=WorkScope(
                intrusive_work=not args.non_intrusive,
                high_risk_service=not args.not_high_risk,
            ),
            output_dir=Path(args.output_dir),
            unigraph_api_base_url=args.unigraph_api_base_url or RunConfig(equipment_tag=args.equipment).unigraph_api_base_url,
        ),
        profile,
    )
    env_graph = apply_graph_env(config.graph)
    graph = replace(
        env_graph,
        host=args.host or env_graph.host,
        port=args.port or env_graph.port,
        project_id=args.project_id or env_graph.project_id,
        traversal_source_name=args.traversal_source or env_graph.traversal_source_name,
    )
    return replace(
        config,
        equipment_tag=args.equipment,
        job_name=args.job_name,
        job_id=args.job_id,
        cnvrt_project_id=args.cnvrt_project_id or config.cnvrt_project_id,
        unigraph_api_base_url=args.unigraph_api_base_url or config.unigraph_api_base_url,
        collection_id=args.collection_id or config.collection_id,
        collection_name=args.collection_name or config.collection_name,
        graph=graph,
        api=ApiConfig(
            base_url=args.api_base_url,
            auth_token=args.auth_token,
            verify_ssl=not args.no_verify_ssl,
        ),
        policy=replace(config.policy, max_traversal_depth=args.max_depth) if args.max_depth is not None else config.policy,
        work_scope=WorkScope(
            intrusive_work=not args.non_intrusive,
            high_risk_service=not args.not_high_risk,
        ),
    )


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


def print_equipment(items):
    print(f"equipment_count={len(items)}")
    if not items:
        return
    columns = (
        ("id", "ID"),
        ("tag", "Tag"),
        ("name", "Name"),
        ("entity_class", "Class"),
        ("job_id", "Job ID"),
        ("job_name", "PNID"),
    )
    widths = {}
    for key, title in columns:
        widths[key] = max(len(title), *(len(str(item.get(key, ""))) for item in items))
    header = "  ".join(title.ljust(widths[key]) for key, title in columns)
    separator = "  ".join("-" * widths[key] for key, _ in columns)
    print(header)
    print(separator)
    for item in items:
        print("  ".join(str(item.get(key, "")).ljust(widths[key]) for key, _ in columns))


def _first_value(properties, keys):
    for key in keys:
        value = properties.get(key)
        if value not in (None, "", []):
            return str(value).strip()
    return None


def _norm(value):
    return str(value or "").strip().lower()


def _raise_fatal_job_resolution(config, job_debug):
    pnid_names = ", ".join(str(item) for item in (job_debug.get("pnid_names") or [])) or "-"
    message = job_debug.get("message") or job_debug.get("job_resolution_error") or "Job resolution failed."
    raise RuntimeError(
        "Configured CNVRT job resolution failed for "
        f"equipment {config.equipment_tag}; pnid_names={pnid_names}; "
        f"cnvrt_project_id={job_debug.get('cnvrt_project_id') or config.cnvrt_project_id or '-'}; "
        f"collection_id={job_debug.get('collection_id') or config.collection_id or '-'}; "
        f"reason={message}"
    )


def _raise_fatal_project_metadata(config, metadata_debug):
    message = metadata_debug.get("error") or "Project metadata resolution failed."
    raise RuntimeError(
        "Configured project metadata failed for "
        f"equipment {config.equipment_tag or '-'}; "
        f"unigraph_project_id={config.graph.project_id or '-'}; "
        f"cnvrt_project_id={config.cnvrt_project_id or '-'}; "
        f"collection_id={config.collection_id or '-'}; "
        f"reason={message}"
    )


def resolve_project_metadata(config):
    config, metadata_debug = enrich_config_from_unigraph(config)
    if metadata_debug.get("fatal"):
        _raise_fatal_project_metadata(config, metadata_debug)
    return config, metadata_debug


def run(config, image_url=""):
    config.output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[1/15] Resolving Unigraph project metadata")
    config, metadata_debug = resolve_project_metadata(config)
    logger.info(
        "      metadata_status=%s jobs=%s project=%s collection=%s",
        metadata_debug.get("status"),
        metadata_debug.get("job_count"),
        config.graph.project_id or "-",
        config.collection_id or "-",
    )

    logger.info("[2/15] Fetching equipment boundary from JanusGraph")
    boundary_data = fetch_boundaries(config)
    config, job_debug = resolve_job_from_boundary(config, boundary_data)
    boundary_data["context"] = config.context
    boundary_data.setdefault("debug", {}).update(job_debug)
    if job_debug.get("fatal"):
        _raise_fatal_job_resolution(config, job_debug)
    logger.info(
        "      matched_equipment=%s traversal_limit_hit=%s job=%s:%s",
        boundary_data.get("matched_equipment_count"),
        boundary_data.get("traversal_limit_hit"),
        config.job_name or "-",
        config.resolved_job_id or "-",
    )

    boundary_data.setdefault("debug", {})["unigraph_metadata"] = metadata_debug

    logger.info("[3/15] Selecting deterministic isolation candidates")
    candidate_data = find_candidates(boundary_data, config.policy)
    logger.info(
        "      candidates=%s raw_candidates=%s",
        candidate_data.get("total_candidates"),
        (candidate_data.get("debug") or {}).get("raw_candidate_count_before_dedupe"),
    )

    inferred_config = _config_with_inferred_job(config, candidate_data, boundary_data)
    if inferred_config is not config:
        config = inferred_config
        candidate_data["context"] = config.context
        logger.info(
            "      inferred_pnid=%s job_id=%s from selected graph candidates",
            config.job_name,
            config.resolved_job_id,
        )

    logger.info("[4/15] Resolving candidate bboxes from STLM/HILT")
    bbox_data = resolve_bboxes(candidate_data, config)
    logger.info(
        "      bbox_resolved=%s stlm_symbols=%s",
        (bbox_data.get("debug") or {}).get("bbox_resolved_count"),
        (bbox_data.get("debug") or {}).get("bbox_stlm_symbol_count"),
    )

    logger.info("[5/15] Analyzing isolation obligations")
    bbox_data = analyze_isolation_obligations(bbox_data, config)
    obligation_summary = ((bbox_data.get("isolation_obligations") or {}).get("summary") or {})
    logger.info(
        "      obligations=%s unresolved=%s manual_candidates=%s",
        obligation_summary.get("process_obligation_count"),
        obligation_summary.get("unresolved_count"),
        obligation_summary.get("manual_candidate_count"),
    )

    logger.info("[6/15] Detecting isolation schemes and relief points")
    bbox_data = analyze_isolation_schemes_and_relief(bbox_data, config)
    scheme_summary = ((bbox_data.get("detected_isolation_schemes") or {}).get("summary") or {})
    relief_summary = ((bbox_data.get("relief_candidates") or {}).get("summary") or {})
    logger.info(
        "      schemes=%s relief_candidates=%s",
        scheme_summary.get("scheme_count"),
        relief_summary.get("candidate_count"),
    )

    logger.info("[7/15] Analyzing instrument context")
    instrument_context = analyze_instrument_context(bbox_data, config)
    bbox_data["instrument_context"] = instrument_context
    bbox_data.setdefault("debug", {})["instrument_context_status"] = instrument_context.get("status")
    bbox_data.setdefault("debug", {})["instrument_context_count"] = len(instrument_context.get("instruments") or [])
    logger.info(
        "      instrument_status=%s instruments=%s",
        instrument_context.get("status"),
        len(instrument_context.get("instruments") or []),
    )

    logger.info("[8/15] Classifying deterministic evidence")
    evidence_data = build_evidence(bbox_data, config)
    evidence_debug = evidence_data.get("debug") or {}
    logger.info(
        "      barriers=%s positive=%s verification=%s",
        evidence_debug.get("evidence_barrier_candidate_count"),
        evidence_debug.get("evidence_positive_candidate_count"),
        evidence_debug.get("evidence_verification_candidate_count"),
    )

    logger.info("[9/15] Planning required evidence checks")
    planner_data = plan_requests(evidence_data, config)
    logger.info(
        "      required_checks=%s",
        (planner_data.get("debug") or {}).get("planner_required_evidence_check_count"),
    )

    logger.info("[10/15] Validating isolation assurance")
    validation_data = validate(planner_data)
    logger.info(
        "      assurance_status=%s terminal=%s",
        validation_data.get("assurance_status"),
        (validation_data.get("isolation_validation") or {}).get("terminal"),
    )

    logger.info("[11/15] Analyzing downstream impact from selected barriers")
    downstream_impact = analyze_downstream_impact(validation_data, config)
    impact_debug = downstream_impact.get("debug") or {}
    logger.info(
        "      downstream_status=%s warnings=%s",
        downstream_impact.get("status"),
        impact_debug.get("warning_count"),
    )

    validation_data["instrument_context"] = instrument_context

    logger.info("[12/15] Building deterministic LOTO procedure")
    loto_procedure = build_loto_procedure(validation_data, config)
    logger.info(
        "      loto_steps=%s order_source=%s",
        len(loto_procedure.get("ordered_steps") or []),
        loto_procedure.get("within_phase_order_source"),
    )

    logger.info("[13/15] Building final UI JSON payload")
    final_payload = build_final_payload(validation_data, config, downstream_impact=downstream_impact)
    final_payload.setdefault("debug", {})["unigraph_metadata"] = metadata_debug
    final_payload.setdefault("data", [{}])[0]["loto_procedure"] = loto_procedure

    stem = config.equipment_tag.replace("/", "_").replace(" ", "_")
    output_json = config.output_dir / f"{stem}.json"
    viewer_html = config.output_dir / f"{stem}.html"
    if not image_url:
        logger.info("[14/15] Downloading P&ID image from Plant360 API")
        image_url, image_debug = resolve_pid_image(config, config.output_dir, stem)
        final_payload.setdefault("debug", {}).update(image_debug)
        logger.info(
            "      image_file_id=%s image_bytes=%s",
            image_debug.get("pid_image_file_id"),
            image_debug.get("pid_image_bytes"),
        )
    else:
        logger.info("[14/15] Using provided P&ID image URL")

    logger.info("[15/15] Writing JSON output and HTML viewer")
    write_json(output_json, final_payload)
    write_viewer(viewer_html, final_payload, image_url=image_url)
    return output_json, viewer_html, final_payload


def main():
    args = parse_args()
    configure_logging(args.quiet)
    config = build_config(args)
    if args.list_equipment:
        config, metadata_debug = resolve_project_metadata(config)
        if not args.quiet:
            logger.info(
                "metadata_status=%s jobs=%s",
                metadata_debug.get("status"),
                metadata_debug.get("job_count"),
            )
        items = list_equipment(config.graph, args.equipment_limit)
        add_equipment_jobs_from_metadata(items, config.job_ids_by_name)
        add_equipment_jobs(
            items,
            config.api,
            config.job_ids_by_name or JOB_IDS_BY_NAME,
        )
        print_equipment(items)
        return
    output_json, viewer_html, payload = run(config, image_url=args.image_url)
    data = payload.get("data", [{}])[0]
    print(f"assurance_status={data.get('assurance_status')}")
    print(f"isolation_points={len(data.get('isolation_points') or [])}")
    print(f"output_json={output_json}")
    print(f"viewer_html={viewer_html}")


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


if __name__ == "__main__":
    main()
