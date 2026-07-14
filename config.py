import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path


JOB_IDS_BY_NAME = {
    "pnid_1_bio_final": "2099",
    "pnid_2_bio_final": "2100",
    "pnid_3_bio_final": "2102",
    "pnid_5_bio_final": "2103",
    "pnid_7_bio_final": "2104",
    "pnid_4_bio_final": "2105",
    "pnid_6_bio_final": "2106",
}


@dataclass(frozen=True)
class GraphConfig:
    host: str = "44.217.77.13"
    port: str = "18182"
    project_id: str = "9"
    traversal_source_name: str = ""
    username: str = ""
    password: str = ""

    @property
    def gremlin_url(self) -> str:
        return f"ws://{self.host.strip()}:{self.port.strip()}/gremlin"

    @property
    def traversal_source(self) -> str:
        if self.traversal_source_name.strip():
            return self.traversal_source_name.strip()
        return f"graph{self.project_id.strip()}_traversal"


@dataclass(frozen=True)
class ApiConfig:
    base_url: str = "https://api.plant360.ai:8080"
    auth_token: str = ""
    verify_ssl: bool = True


@dataclass(frozen=True)
class IsolationPolicy:
    max_traversal_depth: int = 3
    traversal_limit_per_depth: int = 200
    eligible_classes: tuple[str, ...] = (
        "valve",
        "generic_inline_valve",
        "gate_valve",
        "ball_valve",
        "globe_valve",
        "blind",
        "spade",
        "flange",
        "blank_flange",
        "line_break_point",
        "breaker",
        "disconnect",
    )
    excluded_classes: tuple[str, ...] = (
        "equipment",
        "pump",
        "tank",
        "vessel",
        "line",
        "pipe",
        "instrument",
        "instrument_loop",
        "instrument_loops",
        "locally_mounted_instrument",
        "dcs_function_in_control_room",
        "alarm",
    )
    conditional_classes: tuple[str, ...] = ("check_valve", "control_valve", "undefined_valve")
    include_conditional_candidates: bool = False
    positive_isolation_classes: tuple[str, ...] = (
        "blind",
        "spade",
        "spectacle",
        "flange",
        "blank_flange",
        "line_break_point",
        "disconnect",
        "breaker",
        "spool",
    )
    verification_classes: tuple[str, ...] = ("bleed", "vent", "drain", "gauge", "indicator", "test_point")
    verification_tag_prefixes: tuple[str, ...] = ("pi", "pg")
    candidate_edge_labels: tuple[str, ...] = (
        "HAS_A",
        "STARTS_AT",
        "ENDS_AT",
        "PHYSICALLY_HAS_A",
        "PHYSICALLY_CONNECTED_TO",
        "ASSOCIATED_WITH",
    )


@dataclass(frozen=True)
class WorkScope:
    intrusive_work: bool = True
    confined_space_entry: bool = False
    hot_work: bool = False
    high_risk_service: bool = True

    @property
    def requires_positive_isolation(self) -> bool:
        return any(
            (
                self.intrusive_work,
                self.confined_space_entry,
                self.hot_work,
                self.high_risk_service,
            )
        )


@dataclass(frozen=True)
class RunConfig:
    equipment_tag: str
    job_name: str = ""
    job_id: str = ""
    cnvrt_project_id: str = ""
    unigraph_api_base_url: str = "https://api.plant360.ai/plantgraph"
    job_ids_by_name: dict[str, str] = field(default_factory=dict)
    collection_id: str = "196"
    collection_name: str = "Unit"
    graph: GraphConfig = field(default_factory=GraphConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    policy: IsolationPolicy = field(default_factory=IsolationPolicy)
    work_scope: WorkScope = field(default_factory=WorkScope)
    output_dir: Path = Path("/tmp/eia")

    @property
    def resolved_job_id(self) -> str:
        return self.job_id or self.job_ids_by_name.get(self.job_name, "") or JOB_IDS_BY_NAME.get(self.job_name, "")

    @property
    def context(self) -> dict:
        context = {
            "project_id": self.graph.project_id,
            "unigraph_project_id": self.graph.project_id,
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
        }
        if self.cnvrt_project_id:
            context["cnvrt_project_id"] = self.cnvrt_project_id
        if self.job_name:
            context["job_name"] = self.job_name
        if self.resolved_job_id:
            context["job_id"] = self.resolved_job_id
        return context


DEFAULT_PROJECT_CONFIG = Path(__file__).with_name("project_config.json")


def load_project_profile(config_path="", profile_name=""):
    path = Path(config_path or DEFAULT_PROJECT_CONFIG)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") or {}
    selected = profile_name or payload.get("active_profile") or ""
    if not selected:
        return {}
    if selected not in profiles:
        raise ValueError(f"project profile {selected!r} not found in {path}")
    profile = dict(profiles[selected] or {})
    policy = dict(payload.get("isolation_policy") or {})
    policy.update(profile.get("isolation_policy") or {})
    if policy:
        profile["isolation_policy"] = policy
    profile["profile_name"] = selected
    profile["profile_path"] = str(path)
    return profile


def apply_project_profile(config, profile):
    if not profile:
        return config
    graph_data = profile.get("graph") or {}
    graph = replace(
        config.graph,
        host=str(graph_data.get("host") or config.graph.host),
        port=str(graph_data.get("port") or config.graph.port),
        project_id=str(profile.get("unigraph_project_id") or profile.get("project_id") or config.graph.project_id),
        traversal_source_name=str(graph_data.get("traversal_source") or config.graph.traversal_source_name),
    )
    policy = _apply_policy_profile(config.policy, profile.get("isolation_policy") or {})
    return replace(
        config,
        cnvrt_project_id=str(profile.get("cnvrt_project_id") or config.cnvrt_project_id),
        unigraph_api_base_url=str(profile.get("unigraph_api_base_url") or config.unigraph_api_base_url),
        collection_id=str(profile.get("collection_id") or config.collection_id),
        collection_name=str(profile.get("collection_name") or config.collection_name),
        job_ids_by_name={str(k): str(v) for k, v in (profile.get("job_ids_by_name") or {}).items()},
        graph=graph,
        policy=policy,
    )


def apply_graph_env(graph):
    updates = {}
    url = os.environ.get("JANUSGRAPH_URL", "").strip()
    if url:
        host, port = _parse_janusgraph_url(url)
        if host:
            updates["host"] = host
        if port:
            updates["port"] = port
    username = os.environ.get("JANUSGRAPH_USERNAME", "").strip()
    password = os.environ.get("JANUSGRAPH_PASSWORD", "").strip()
    if username:
        updates["username"] = username
    if password:
        updates["password"] = password
    return replace(graph, **updates) if updates else graph


def _parse_janusgraph_url(url):
    value = url.strip()
    for prefix in ("ws://", "wss://", "http://", "https://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.removesuffix("/gremlin").rstrip("/")
    if ":" not in value:
        return value, ""
    host, port = value.rsplit(":", 1)
    return host.strip(), port.strip()


def _apply_policy_profile(policy, policy_data):
    if not policy_data:
        return policy
    fields = {
        "max_traversal_depth": int,
        "traversal_limit_per_depth": int,
        "eligible_classes": tuple,
        "excluded_classes": tuple,
        "conditional_classes": tuple,
        "include_conditional_candidates": bool,
        "positive_isolation_classes": tuple,
        "verification_classes": tuple,
        "verification_tag_prefixes": tuple,
        "candidate_edge_labels": tuple,
    }
    updates = {}
    for key, caster in fields.items():
        if key not in policy_data:
            continue
        value = policy_data[key]
        if caster is tuple:
            updates[key] = tuple(str(item).strip().lower() for item in (value or []) if str(item).strip())
        elif caster is bool:
            updates[key] = bool(value)
        else:
            updates[key] = caster(value)
    return replace(policy, **updates)
