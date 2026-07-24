"""Filesystem-free agentic pipeline runner shared by CLI and API."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pipeline.stages import resolve_project_metadata

from agent.loop import DEFAULT_MODEL, run_agent
from agent.session import AgentSession


@dataclass
class AgentRunResult:
    config: Any
    final_payload: dict | None
    agent_result: dict
    trace: list[dict]
    metadata_debug: dict


def run_agent_pipeline(
    config,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    max_steps: int = 16,
    on_event: Callable | None = None,
) -> AgentRunResult:
    """Run the agentic pipeline without printing or writing artifacts."""
    config, metadata_debug = resolve_project_metadata(config)
    session = AgentSession(config)
    agent_result = run_agent(
        session,
        model=model,
        api_key=api_key,
        max_steps=max_steps,
        on_event=on_event,
    )

    final_payload = session.final_payload
    if final_payload:
        payload_data = final_payload.setdefault("data", [{}])[0]
        if session.loto_procedure:
            payload_data.setdefault("loto_procedure", session.loto_procedure)
        if session.instrument_context:
            payload_data.setdefault("instrument_context", session.instrument_context)
        if session.relief_analysis:
            payload_data.update(session.relief_analysis)
        final_payload.setdefault("debug", {})["unigraph_metadata"] = metadata_debug

    return AgentRunResult(
        config=session.config,
        final_payload=final_payload,
        agent_result=agent_result,
        trace=session.trace,
        metadata_debug=metadata_debug,
    )
