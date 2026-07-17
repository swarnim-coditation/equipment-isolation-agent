# Equipment Isolation Runner

Equipment isolation planner for LOTO (lock-out/tag-out). It resolves an
isolation boundary from graph traversal and Plant360 APIs, validates assurance
status, and builds an OSHA 1910.147(d) LOTO procedure.

Two runners share the same deterministic domain modules:

- **Deterministic runner (`run.py`)** — no LLM calls; pure graph traversal + APIs.
- **Agentic runner (`agent/`)** — a Gemini LLM orchestrates the same deterministic
  stages as tools (see [Agentic Runner](#agentic-runner-gemini-orchestrated)).

> See `AGENTS.md` for the full command table, per-file architecture map, pipeline
> steps, and configuration notes.

## Setup

```bash
uv sync  # installs dependencies to .venv/
```

Copy `.env.example` → `.env` and set `PLANT360_AUTH_TOKEN` (for bboxes/P&ID
images) and, for the agentic runner, `GEMINI_API_KEY`.

## Run

```bash
uv run python -m run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100
```

List available equipment tags from JanusGraph:

```bash
uv run python -m run --list-equipment
```

The list includes graph id, tag, name, entity class, job id, and PNID/job name
when the equipment can be matched to STLM data. Limit it for quick browsing:

```bash
uv run python -m run --list-equipment --equipment-limit 20
```

For bbox resolution, provide a Plant360 API token via `--auth-token` or the
`PLANT360_AUTH_TOKEN` environment variable. Without API auth, the runner still
returns graph candidates and assurance status, but bboxes remain empty.

## Outputs

Default output directory: `output/` (repo-relative, git-ignored)

```text
BT-11.json    final UI payload
BT-11.html    bbox overlay viewer
```

`BT-11.html` overlays resolved bboxes. Pass `--image-url` (or let the API image
download succeed) to render boxes over a P&ID image; otherwise the viewer uses a
blank canvas. Override the directory with `--output-dir`.

## Architecture

The deterministic runner executes a **15-step pipeline**: resolve Unigraph
project metadata → fetch boundary + resolve job → select candidates → resolve
bboxes → isolation obligations → schemes + relief → instrument context →
evidence classification → plan evidence checks → validate assurance → downstream
impact → LOTO procedure → build final payload → download P&ID image → write JSON
+ HTML viewer.

```text
run.py            CLI entrypoint, orchestrates the deterministic pipeline
config.py         Runtime config dataclasses
graph_client.py   Gremlin connection and vertex helpers
boundary.py       Equipment/nozzle boundary traversal
candidates.py     Deterministic isolation candidate selection
bbox.py           STLM bbox resolver (merges AUTHORITATIVE HILT topology picks)
hilt_topology.py  HILT nozzle<->valve connectivity resolver (AUTHORITATIVE)
obligations.py    Process/isolation obligation analysis
relief.py         Isolation scheme + relief-point detection
impact.py         Downstream impact analysis
instrument_context.py  Instrument context classification (advisory only)
evidence.py       Evidence classification
planner.py        Deterministic evidence-check rules
validator.py      Assurance status validator (AUTHORITATIVE)
loto.py           OSHA 1910.147(d) LOTO procedure sequencer
output.py         UI payload and HTML overlay writer
viewer.py         HTML overlay renderer
image.py          P&ID image download
domain/           Shared domain types: enums, models, classification, serialization
```

## Agentic Runner (Gemini-orchestrated)

The `agent/` package adds a runner where a Gemini LLM is the **orchestrator**. It
runs a tool-calling loop and decides which deterministic stage to call next. The
deterministic modules above are preserved **unchanged** and exposed to the agent
as tools; the deterministic `validate()` remains the **authoritative** source of
`assurance_status` (the agent can gather more evidence but cannot declare
isolation on its own).

```bash
uv run python -m agent --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100
```

Agent tools: `fetch_boundary`, `find_candidates`, `resolve_bboxes`,
`analyze_isolation_obligations`, `analyze_schemes_and_relief`,
`list_unselected_sources`, `investigate_source`, `build_evidence`,
`analyze_instrument_context`, `validate`, `get_osha_guidance`,
`build_loto_procedure`, `set_isolation_order`, `analyze_downstream_impact`,
`finalize_plan`.

After `validate()`, the agent builds an **OSHA 1910.147(d) LOTO procedure**: the
6-phase order is fixed/authoritative (deterministic `loto.py`), and the agent
uses `get_osha_guidance` (RAG over the bundled OSHA 29 CFR 1910.147 reference) to
reason about within-phase device ordering and cite provisions. The procedure
(with field-action gaps for missing bleed/verification) is added to the output
payload as `loto_procedure`.

Outputs (default dir `output_agent/`):

```text
BT-11.json         final UI payload (same shape as the deterministic runner)
BT-11.html         bbox overlay viewer
BT-11_trace.json   agent transcript + per-tool audit trace
```

Requires `GEMINI_API_KEY` in `.env`. Default model `gemini-2.5-flash` (override
with `--model`). This is a POC decision-support aid, not a certified LOTO
procedure.

## Tests

Pure-logic unit tests run offline (no graph/API) via stdlib `unittest`:

```bash
uv run python -m unittest discover -s tests       # all tests
uv run python -m unittest tests.test_relief       # a single module
```

Compare the agent against the deterministic baseline across equipment:

```bash
uv run python eval_compare.py BT-11 C-02
```
