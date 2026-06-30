from dataclasses import dataclass, field
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
    port: str = "8182"
    project_id: str = "274"

    @property
    def gremlin_url(self) -> str:
        return f"ws://{self.host.strip()}:{self.port.strip()}/gremlin"

    @property
    def traversal_source(self) -> str:
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
        "gate_valve",
        "ball_valve",
        "globe_valve",
        "check_valve",
        "control_valve",
        "blind",
        "spade",
        "flange",
        "breaker",
        "disconnect",
    )
    excluded_classes: tuple[str, ...] = ("equipment", "pump", "tank", "vessel", "line", "pipe")
    conditional_classes: tuple[str, ...] = ("check_valve", "control_valve")
    include_conditional_candidates: bool = False
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
    collection_id: str = "196"
    collection_name: str = "Unit"
    graph: GraphConfig = field(default_factory=GraphConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    policy: IsolationPolicy = field(default_factory=IsolationPolicy)
    work_scope: WorkScope = field(default_factory=WorkScope)
    output_dir: Path = Path("/tmp/opencode/equipment_isolation_no_llm")

    @property
    def resolved_job_id(self) -> str:
        return self.job_id or JOB_IDS_BY_NAME.get(self.job_name, "")

    @property
    def context(self) -> dict:
        context = {
            "project_id": self.graph.project_id,
            "collection_id": self.collection_id,
            "collection_name": self.collection_name,
        }
        if self.job_name:
            context["job_name"] = self.job_name
        if self.resolved_job_id:
            context["job_id"] = self.resolved_job_id
        return context
