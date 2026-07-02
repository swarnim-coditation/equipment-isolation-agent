# Local No-LLM Equipment Isolation Runner

Deterministic local runner for equipment isolation. It uses graph traversal and Plant360 APIs only. No LLM calls are made.

## Setup

```bash
uv sync  # installs dependencies to .venv/
```

## Run

```bash
uv run python -m run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100
```

List available equipment tags from JanusGraph:

```bash
uv run python -m run --list-equipment
```

The list includes graph id, tag, name, entity class, job id, and PNID/job name when the equipment can be matched to STLM data.

Limit the list for quick browsing:

```bash
uv run python -m run --list-equipment --equipment-limit 20
```

For bbox resolution, provide a Plant360 API token either with `--auth-token` or the `PLANT360_AUTH_TOKEN` environment variable. Without API auth, the runner still returns graph candidates and assurance status, but bboxes remain empty.

## Outputs

Default output directory:

```text
/tmp/opencode/equipment_isolation_no_llm
```

Files:

```text
BT-11_output.json
BT-11_viewer.html
```

`BT-11_viewer.html` overlays resolved bboxes. Pass `--image-url` to render boxes over a P&ID image. Without it, the viewer uses a blank canvas.

## Architecture

```text
run.py          CLI entrypoint, orchestrates 9-step pipeline
config.py       Runtime config dataclasses
graph_client.py Gremlin connection and vertex helpers
boundary.py     Equipment/nozzle boundary traversal
candidates.py   Deterministic isolation candidate selection
api_client.py   Plant360 API client
bbox.py         STLM bbox resolver
evidence.py     Evidence classification
planner.py      Deterministic graph/API evidence requests
validator.py    Assurance status validator
output.py       UI payload and HTML overlay writer
image.py        P&ID image download
```

## Agentic Runner (Gemini-orchestrated)

The `agent/` package adds a second runner where a Gemini LLM is the **orchestrator**.
It runs a tool-calling loop and decides which deterministic stage to call next. The
deterministic modules above are preserved **unchanged** and exposed to the agent as
tools; the deterministic `validate()` remains the **authoritative** source of
`assurance_status` (the agent can gather more evidence but cannot declare isolation
on its own).

```bash
uv run python -m agent --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100
```

The agent tools: `fetch_boundary`, `find_candidates`, `resolve_bboxes`,
`list_unselected_sources`, `investigate_source`, `build_evidence`, `validate`,
`get_osha_guidance`, `build_loto_procedure`, `finalize_plan`. When `validate()`
reports missing boundaries, the agent proactively investigates each uncovered
nozzle before finalizing. After `validate()`, it builds an **OSHA 1910.147(d)
LOTO procedure**: the 6-phase order is fixed/authoritative (deterministic
`loto.py`), and the agent uses `get_osha_guidance` (RAG over the bundled OSHA
29 CFR 1910.147 reference) to reason about within-phase device ordering and cite
provisions. The procedure (with field-action gaps for missing bleed/verification)
is added to the output payload as `loto_procedure`.

Outputs (default dir `/tmp/opencode/equipment_isolation_agent`):

```text
BT-11_output.json   final UI payload (same shape as the deterministic runner)
BT-11_viewer.html   bbox overlay viewer
BT-11_trace.json    agent transcript + per-tool audit trace
```

Requires `GEMINI_API_KEY` in `.env`. Default model `gemini-2.5-flash` (override
with `--model`). This is a POC decision-support aid, not a certified LOTO procedure.

Compare the agent against the deterministic baseline across equipment:

```bash
uv run python eval_compare.py BT-11 C-02
```