"""The Gemini tool-calling agent loop.

Manual ReAct-style loop: send system prompt + tools -> Gemini returns text and/or
function calls -> execute each function call deterministically against the
``AgentSession`` -> feed compact results back -> repeat until Gemini stops calling
tools (or MAX_STEPS is hit).

Guardrails:
- Hard cap ``max_steps`` to bound cost and runaway loops.
- If the loop ends without a terminal ``validate()`` result, force one final
  ``validate()`` so the authoritative assurance_status is always produced.
- Every tool call is recorded in ``session.trace`` for audit.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from google import genai
from google.genai import types

from agent.prompts import SYSTEM_PROMPT, user_message
from agent.session import AgentSession
from agent.tools import TOOL_SPECS, call_tool

DEFAULT_MODEL = "gemini-2.5-flash"


def function_declarations() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name=spec["name"],
            description=spec["description"],
            parameters=spec["parameters"],
        )
        for spec in TOOL_SPECS
    ]


def _emit(on_event: Callable | None, kind: str, payload: Any) -> None:
    if on_event:
        try:
            on_event(kind, payload)
        except Exception:
            pass


def run_agent(
    session: AgentSession,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    max_steps: int = 16,
    on_event: Callable | None = None,
) -> dict:
    """Run the orchestrator loop. Returns a result dict with transcript, step
    count, and the authoritative assurance_status. Mutates ``session`` with all
    pipeline data + trace.
    """
    client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY", ""))
    declarations = function_declarations()
    equipment_tag = session.config.equipment_tag

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=user_message(equipment_tag))])
    ]
    transcript: list[dict] = []
    assurance_status: str | None = None
    validate_terminal = False
    steps_used = 0

    _emit(on_event, "start", {"equipment": equipment_tag, "model": model, "max_steps": max_steps})

    for step in range(1, max_steps + 1):
        steps_used = step
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[types.Tool(function_declarations=declarations)],
                temperature=0,
            ),
        )

        parts = (response.candidates[0].content.parts or []) if response.candidates else []
        text_chunks = [p.text for p in parts if getattr(p, "text", None)]
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if text_chunks:
            joined = "\n".join(text_chunks)
            transcript.append({"step": step, "role": "model", "kind": "text", "text": joined})
            _emit(on_event, "model_text", joined)

        if not calls:
            break

        contents.append(response.candidates[0].content)

        for fc in calls:
            name = fc.name
            args = dict(fc.args or {})
            _emit(on_event, "tool_call", {"name": name, "args": args})
            result = call_tool(session, name, args)
            _emit(on_event, "tool_result", {"name": name, "result": result})
            if name == "validate":
                assurance_status = result.get("assurance_status")
                if result.get("terminal"):
                    validate_terminal = True
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(name=name, response=result)
                        )
                    ],
                )
            )

    forced = _ensure_pipeline(session, validate_terminal, on_event)
    if forced:
        transcript.append(
            {"step": "guardrail", "role": "system", "kind": "guardrail", "forced": forced}
        )
    if assurance_status is None and session.validation_data is not None:
        assurance_status = session.validation_data.get("assurance_status")
    validate_terminal = bool(
        (session.validation_data or {}).get("isolation_validation", {}).get("terminal")
    )

    return {
        "transcript": transcript,
        "steps_used": steps_used,
        "assurance_status": assurance_status,
        "validate_terminal": validate_terminal,
        "forced": forced,
    }


def _ensure_pipeline(session: AgentSession, validate_terminal: bool, on_event) -> list[str]:
    """Guardrail: walk the deterministic pipeline forward from wherever the agent
    stopped so the CLI always gets (a) an authoritative assurance_status via
    validate() and (b) a final payload via finalize_plan(). Returns the list of
    stages it had to force. If fetch_boundary never succeeded, nothing can be
    produced and the CLI reports the error -- but the trace is still written.
    """
    forced: list[str] = []

    def _run(name: str, args: dict | None = None) -> dict:
        forced.append(name)
        result = call_tool(session, name, args or {})
        _emit(on_event, "guardrail", {"forced": name, "result": result})
        return result

    if (
        session.final_payload is not None
        and session.loto_procedure is not None
        and session.downstream_impact is not None
        and session.isolation_obligations is not None
        and session.relief_analysis is not None
    ):
        return forced
    if session.boundary_data is None:
        _run("fetch_boundary", {"equipment_tag": session.config.equipment_tag})
    if session.candidate_data is None:
        _run("find_candidates")
    if session.bbox_data is None:
        _run("resolve_bboxes")
    if session.isolation_obligations is None and session.bbox_data is not None:
        _run("analyze_isolation_obligations")
    if session.relief_analysis is None and session.bbox_data is not None:
        _run("analyze_isolation_schemes_and_relief")
    if session.evidence_data is None:
        _run("build_evidence")
    if session.validation_data is None:
        _run("validate")
    if session.downstream_impact is None and session.validation_data is not None:
        _run("analyze_downstream_impact")
    if session.final_payload is None and session.validation_data is not None:
        _run("finalize_plan")
    if session.loto_procedure is None and session.validation_data is not None:
        _run("build_loto_procedure")
    return forced
