"""Argparse-free RunConfig construction shared by CLI and API callers."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from config import (
    ApiConfig,
    IsolationPolicy,
    RunConfig,
    WorkScope,
    apply_graph_env,
    apply_project_profile,
    load_project_profile,
)


def build_run_config(
    *,
    equipment_tag: str,
    job_name: str = "",
    job_id: str = "",
    project_config: str | Path = "project_config.json",
    project_profile: str = "",
    auth_token: str = "",
    api_base_url: str = "https://api.plant360.ai:8080",
    verify_ssl: bool = True,
    unigraph_api_base_url: str = "",
    cnvrt_project_id: str = "",
    collection_id: str = "",
    collection_name: str = "",
    host: str = "",
    port: str = "",
    project_id: str = "",
    traversal_source: str = "",
    max_depth: int | None = None,
    intrusive_work: bool = True,
    high_risk_service: bool = True,
    confined_space_entry: bool = False,
    hot_work: bool = False,
    output_dir: str | Path = Path("output"),
) -> RunConfig:
    """Build the same RunConfig shape used by both existing CLIs.

    This is deliberately a thin extraction of run.py / agent.cli config logic so
    API callers cannot introduce a third profile/env/override precedence path.
    """
    base = RunConfig(equipment_tag=equipment_tag)
    profile = load_project_profile(project_config, project_profile) if project_profile != "__api_no_profile__" else {}
    config = apply_project_profile(
        RunConfig(
            equipment_tag=equipment_tag,
            job_name=job_name,
            job_id=job_id,
            api=ApiConfig(
                base_url=api_base_url,
                auth_token=auth_token,
                verify_ssl=verify_ssl,
            ),
            policy=IsolationPolicy(),
            work_scope=WorkScope(
                intrusive_work=intrusive_work,
                confined_space_entry=confined_space_entry,
                hot_work=hot_work,
                high_risk_service=high_risk_service,
            ),
            output_dir=Path(output_dir),
            unigraph_api_base_url=unigraph_api_base_url or base.unigraph_api_base_url,
        ),
        profile,
    )
    env_graph = apply_graph_env(config.graph)
    graph = replace(
        env_graph,
        host=host or env_graph.host,
        port=port or env_graph.port,
        project_id=project_id or env_graph.project_id,
        traversal_source_name=traversal_source or env_graph.traversal_source_name,
    )
    return replace(
        config,
        equipment_tag=equipment_tag,
        job_name=job_name,
        job_id=job_id,
        cnvrt_project_id=cnvrt_project_id or config.cnvrt_project_id,
        unigraph_api_base_url=unigraph_api_base_url or config.unigraph_api_base_url,
        collection_id=collection_id or config.collection_id,
        collection_name=collection_name or config.collection_name,
        graph=graph,
        api=ApiConfig(
            base_url=api_base_url,
            auth_token=auth_token,
            verify_ssl=verify_ssl,
        ),
        policy=replace(config.policy, max_traversal_depth=max_depth) if max_depth is not None else config.policy,
        work_scope=WorkScope(
            intrusive_work=intrusive_work,
            confined_space_entry=confined_space_entry,
            hot_work=hot_work,
            high_risk_service=high_risk_service,
        ),
        output_dir=Path(output_dir),
    )
