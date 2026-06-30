import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from .bbox import resolve_bboxes
from .bbox import _extract_symbols
from .boundary import fetch_boundaries
from .candidates import find_candidates
from .config import ApiConfig, GraphConfig, IsolationPolicy, JOB_IDS_BY_NAME, RunConfig, WorkScope
from .evidence import build_evidence
from .graph_client import GraphClient, normalize_vertex, props_only, vertex_id
from .image import resolve_pid_image
from .output import build_final_payload, write_json, write_viewer
from .planner import plan_requests
from .api_client import Plant360Client
from .validator import validate


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
    parser.add_argument("--job-name", default="", help="P&ID/job name, e.g. pnid_2_bio_final")
    parser.add_argument("--job-id", default="", help="P&ID/job id, e.g. 2100")
    parser.add_argument("--host", default="44.217.77.13")
    parser.add_argument("--port", default="8182")
    parser.add_argument("--project-id", default="274")
    parser.add_argument("--collection-id", default="196")
    parser.add_argument("--collection-name", default="Unit")
    parser.add_argument("--api-base-url", default="https://api.plant360.ai:8080")
    parser.add_argument("--auth-token", default=os.environ.get("PLANT360_AUTH_TOKEN", ""))
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--output-dir", default="/tmp/opencode/equipment_isolation_no_llm")
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
    return RunConfig(
        equipment_tag=args.equipment,
        job_name=args.job_name,
        job_id=args.job_id,
        collection_id=args.collection_id,
        collection_name=args.collection_name,
        graph=GraphConfig(host=args.host, port=args.port, project_id=args.project_id),
        api=ApiConfig(
            base_url=args.api_base_url,
            auth_token=args.auth_token,
            verify_ssl=not args.no_verify_ssl,
        ),
        policy=IsolationPolicy(max_traversal_depth=args.max_depth),
        work_scope=WorkScope(
            intrusive_work=not args.non_intrusive,
            high_risk_service=not args.not_high_risk,
        ),
        output_dir=Path(args.output_dir),
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


def add_equipment_jobs(items, api_config):
    if not items or not api_config.auth_token:
        return items

    node_to_job = {}
    client = Plant360Client(api_config)
    for job_name, job_id in JOB_IDS_BY_NAME.items():
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


def run(config, image_url=""):
    config.output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[1/9] Fetching equipment boundary from JanusGraph")
    boundary_data = fetch_boundaries(config)
    logger.info(
        "      matched_equipment=%s traversal_limit_hit=%s",
        boundary_data.get("matched_equipment_count"),
        boundary_data.get("traversal_limit_hit"),
    )

    logger.info("[2/9] Selecting deterministic isolation candidates")
    candidate_data = find_candidates(boundary_data, config.policy)
    logger.info(
        "      candidates=%s raw_candidates=%s",
        candidate_data.get("total_candidates"),
        (candidate_data.get("debug") or {}).get("raw_candidate_count_before_dedupe"),
    )

    inferred_config = _config_with_inferred_job(config, candidate_data)
    if inferred_config is not config:
        config = inferred_config
        candidate_data["context"] = config.context
        logger.info(
            "      inferred_pnid=%s job_id=%s from selected graph candidates",
            config.job_name,
            config.resolved_job_id,
        )

    logger.info("[3/9] Resolving candidate bboxes from STLM")
    bbox_data = resolve_bboxes(candidate_data, config)
    logger.info(
        "      bbox_resolved=%s stlm_symbols=%s",
        (bbox_data.get("debug") or {}).get("bbox_resolved_count"),
        (bbox_data.get("debug") or {}).get("bbox_stlm_symbol_count"),
    )

    logger.info("[4/9] Classifying deterministic evidence")
    evidence_data = build_evidence(bbox_data, config)
    evidence_debug = evidence_data.get("debug") or {}
    logger.info(
        "      barriers=%s positive=%s verification=%s",
        evidence_debug.get("evidence_barrier_candidate_count"),
        evidence_debug.get("evidence_positive_candidate_count"),
        evidence_debug.get("evidence_verification_candidate_count"),
    )

    logger.info("[5/9] Planning required evidence checks")
    planner_data = plan_requests(evidence_data, config)
    logger.info(
        "      required_checks=%s",
        (planner_data.get("debug") or {}).get("planner_required_evidence_check_count"),
    )

    logger.info("[6/9] Validating isolation assurance")
    validation_data = validate(planner_data)
    logger.info(
        "      assurance_status=%s terminal=%s",
        validation_data.get("assurance_status"),
        (validation_data.get("isolation_validation") or {}).get("terminal"),
    )

    logger.info("[7/9] Building final UI JSON payload")
    final_payload = build_final_payload(validation_data, config)

    stem = config.equipment_tag.replace("/", "_").replace(" ", "_")
    output_json = config.output_dir / f"{stem}_output.json"
    viewer_html = config.output_dir / f"{stem}_viewer.html"
    if not image_url:
        logger.info("[8/9] Downloading P&ID image from Plant360 API")
        image_url, image_debug = resolve_pid_image(config, config.output_dir, stem)
        final_payload.setdefault("debug", {}).update(image_debug)
        logger.info(
            "      image_file_id=%s image_bytes=%s",
            image_debug.get("pid_image_file_id"),
            image_debug.get("pid_image_bytes"),
        )
    else:
        logger.info("[8/9] Using provided P&ID image URL")

    logger.info("[9/9] Writing JSON output and HTML viewer")
    write_json(output_json, final_payload)
    write_viewer(viewer_html, final_payload, image_url=image_url)
    return output_json, viewer_html, final_payload


def main():
    args = parse_args()
    configure_logging(args.quiet)
    if args.list_equipment:
        items = list_equipment(GraphConfig(host=args.host, port=args.port, project_id=args.project_id), args.equipment_limit)
        add_equipment_jobs(
            items,
            ApiConfig(
                base_url=args.api_base_url,
                auth_token=args.auth_token,
                verify_ssl=not args.no_verify_ssl,
            ),
        )
        print_equipment(items)
        return
    config = build_config(args)
    output_json, viewer_html, payload = run(config, image_url=args.image_url)
    data = payload.get("data", [{}])[0]
    print(f"assurance_status={data.get('assurance_status')}")
    print(f"isolation_points={len(data.get('isolation_points') or [])}")
    print(f"output_json={output_json}")
    print(f"viewer_html={viewer_html}")


def _config_with_inferred_job(config, candidate_data):
    counts = {}
    for candidate in candidate_data.get("candidates", []) or []:
        unit_name = (candidate.get("properties") or {}).get("unit_name")
        if unit_name in JOB_IDS_BY_NAME:
            counts[unit_name] = counts.get(unit_name, 0) + 1
    if not counts:
        return config
    inferred_job_name = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    if inferred_job_name == config.job_name:
        return config
    inferred_job_id = JOB_IDS_BY_NAME.get(inferred_job_name, "")
    debug = candidate_data.setdefault("debug", {})
    debug["input_job_name"] = config.job_name
    debug["input_job_id"] = config.resolved_job_id
    debug["inferred_job_name"] = inferred_job_name
    debug["inferred_job_id"] = inferred_job_id
    debug["inferred_job_source"] = "selected_candidate_unit_name"
    return replace(config, job_name=inferred_job_name, job_id=inferred_job_id)


if __name__ == "__main__":
    main()
