"""Declarative tool schemas advertised to the Gemini orchestrator.

Pure data: name, description and JSON-Schema parameters for each tool. The
implementation mapping lives in agent/tools.py (``DISPATCH``), which binds these
names to the ``t_*`` functions -- deliberately NOT stored here, so the schema can
be read or serialized without importing the pipeline.

Invariant: every name below must have a matching ``t_<name>`` in agent/tools.py.
tests/test_agent_registry.py enforces that.
"""
from __future__ import annotations


TOOL_SPECS: list[dict] = [
    {
        "name": "fetch_boundary",
        "description": (
            "Fetch the equipment boundary (components + boundary source nozzles) from JanusGraph "
            "for the given equipment tag. Always call this first. Stores the full boundary server-side; "
            "returns a compact summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "equipment_tag": {
                    "type": "string",
                    "description": (
                        "Optional and normally omitted. The run is already scoped to one "
                        "equipment tag; a different value here is ignored."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "find_candidates",
        "description": (
            "Run deterministic isolation-candidate selection over the fetched boundary. Returns the "
            "ranked candidate list (valves/blinds/etc.) with tag, class, isolation method, depth, source. "
            "Requires fetch_boundary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resolve_bboxes",
        "description": (
            "Resolve candidate bounding boxes from Plant360 STLM/HILT (needs the P&ID job, which is "
            "inferred from candidate unit_name if not given). Enables visual overlay and context "
            "classification. Returns bbox resolution stats and unselected-source gaps. Requires find_candidates."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "analyze_isolation_obligations",
        "description": (
            "Build deterministic boundary-source isolation obligations after bbox resolution. Returns "
            "process obligation counts, unresolved source paths, and orange manual bypass/parallel-route "
            "candidate counts. Requires resolve_bboxes and feeds build_evidence/validate."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "analyze_isolation_schemes_and_relief",
        "description": (
            "Detect available isolation schemes and stored-energy relief candidates from existing HILT "
            "topology. It reports single block, double block, double block with bleed, and positive "
            "isolation when present; it does not recommend stronger schemes from hazard assumptions. "
            "Ambiguous relief candidates may be classified by LLM as advisory metadata. Requires resolve_bboxes."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_unselected_sources",
        "description": (
            "List boundary source nozzles that have NO selected isolation candidate (the coverage gaps). "
            "Use when validate/build_evidence report missing_boundary_count > 0 to see exactly which nozzles "
            "are uncovered and why (e.g. nearest candidates too deep). Then call investigate_source on each."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "investigate_source",
        "description": (
            "Pull focused detail for ONE boundary source: all candidates (selected and not) for it, the "
            "connected HILT lines, and label confidence. Use to reason about why a source is uncovered or "
            "whether it is non-process instrument context. source_component_id is an id/tag from "
            "fetch_boundary or list_unselected_sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_component_id": {
                    "type": "string",
                    "description": "The id or tag of the boundary source to investigate.",
                }
            },
            "required": ["source_component_id"],
        },
    },
    {
        "name": "build_evidence",
        "description": (
            "Classify deterministic evidence (barrier / positive-isolation / verification) and compute "
            "missing-evidence gaps. Call after find_candidates (and usually resolve_bboxes). Returns "
            "counts and the human-readable missing_evidence list."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "analyze_instrument_context",
        "description": (
            "Run deterministic instrument-context analysis over HILT/STLM for the selected equipment. "
            "Requires resolve_bboxes. Returns compact counts and top advisory checks for PI/LI/PT/LT/"
            "controllers/alarms. This context improves the SOP but must NOT change assurance_status."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "validate",
        "description": (
            "Compute the AUTHORITATIVE isolation assurance verdict (deterministic planner + validator). "
            "This is the only source of assurance_status; you cannot declare isolation yourself. "
            "Returns assurance_status, rationale, terminal flag, and remaining gaps. Call before finishing."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_osha_guidance",
        "description": (
            "Retrieve relevant regulatory text from the bundled OSHA 29 CFR 1910.147 reference (RAG). "
            "Use to ground LOTO sequencing reasoning in real citations -- e.g. topic='stored energy', "
            "'verification', 'isolation sequence', 'lockout device', 'release'. Call as many times as needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "What you want OSHA guidance on, e.g. 'stored energy relief' or 'verification of isolation'.",
                }
            },
            "required": ["topic"],
        },
    },
    {
        "name": "build_loto_procedure",
        "description": (
            "Build the OSHA 1910.147(d) LOTO procedure skeleton from the validated plan. The 6-phase order "
            "is FIXED by regulation (authoritative -- you cannot reorder phases). Then propose a safe "
            "WITHIN-phase device order (especially Phase 3 isolation) using process-flow reasoning, cite OSHA "
            "via get_osha_guidance, and flag every phase with a field-action gap. Requires validate."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_isolation_order",
        "description": (
            "Commit your chosen WITHIN-phase isolation order as an ordered list of device uuids "
            "(the closure order of valves/barriers). This is ENGINEERING JUDGMENT -- OSHA 1910.147 does "
            "NOT prescribe which valve to close first; only the phase order is regulated. Use the "
            "candidate ids from find_candidates/build_loto_procedure. Then call build_loto_procedure again "
            "to reflect your order, and state your rationale (e.g. process-flow direction) in the summary."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ordered_uuids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Device uuids (candidate ids) in the order the agent proposes to isolate them.",
                }
            },
            "required": ["ordered_uuids"],
        },
    },
    {
        "name": "analyze_downstream_impact",
        "description": (
            "Run deterministic downstream reachability analysis from selected isolation barriers over the "
            "HILT process-line graph. Requires validate. Returns compact counts and top warnings only; "
            "full structured downstream_impact is stored in the final payload. The agent may summarize "
            "possible impacts as 'may affect' and likely impacts as 'likely affects', but must not invent "
            "or upgrade reachability."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "finalize_plan",
        "description": (
            "Build the final UI payload (isolation points with bbox/tags/methods) from the validated plan. "
            "Requires validate. Returns the isolation points summary."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]
