# Equipment Isolation Runner — Agent Instructions

## Quick Start

```bash
uv sync  # installs deps to .venv/

# Run isolation for equipment (deterministic baseline, no LLM)
uv run python -m run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100

# Run the AGENTIC (Gemini-orchestrated) isolation runner
uv run python -m agent --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100

# List available equipment from JanusGraph
uv run python -m run --list-equipment

# Run unit tests (stdlib unittest — pytest is NOT installed)
uv run python -m unittest discover -s tests

# Run a single test module
uv run python -m unittest tests.test_isolation_policy

# Compare agent vs deterministic baseline across equipment
uv run python eval_compare.py BT-11 C-02
```

## Environment

- Python 3.11 managed by `uv`; deps in `pyproject.toml`: `gremlinpython`, `requests`, `google-genai>=2.10.0`
- Virtual env at `.venv/` (created by `uv sync`)
- `.env` is git-ignored; copy `.env.example` → `.env` and set `PLANT360_AUTH_TOKEN`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL`, `JANUSGRAPH_URL` / `JANUSGRAPH_USERNAME` / `JANUSGRAPH_PASSWORD`
- `GEMINI_API_KEY` is required by the agentic runner (`python -m agent`); the deterministic `run.py` does not need it
- `.env` is loaded by a hand-rolled parser in `run.py`/`agent/cli.py` (`load_dotenv`) — not python-dotenv

## Key Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Run isolation (deterministic) | `uv run python -m run --equipment <TAG> [--job-name <NAME>] [--job-id <ID>]` |
| Run isolation (agentic / Gemini) | `uv run python -m agent --equipment <TAG> [--model gemini-2.5-flash] [--max-steps 16]` |
| List equipment | `uv run python -m run --list-equipment [--equipment-limit N]` |
| Run tests | `uv run python -m unittest discover -s tests` |
| Eval agent vs baseline | `uv run python eval_compare.py <TAG>...` (or `--limit N`) |
| API auth | `PLANT360_AUTH_TOKEN=xxx` env or `--auth-token xxx` |
| Project context | Edit `project_config.json` or `--project-profile <NAME>` |
| Custom graph host/source | `--host <IP> --port <PORT> --project-id <UNIGRAPH_ID>`; traversal source defaults to `graph<UNIGRAPH_ID>_traversal` |
| Override output dir | `--output-dir /path/to/dir` |
| Quiet mode | `--quiet` |

## Architecture

```
run.py            CLI entrypoint, orchestrates 15-step deterministic pipeline
config.py         Frozen dataclasses: GraphConfig, ApiConfig, IsolationPolicy, WorkScope, RunConfig
graph_client.py   Gremlin connection (DriverRemoteConnection), vertex helpers
boundary.py       Equipment/nozzle graph traversal
candidates.py     Deterministic isolation candidate selection
bbox.py           STLM bbox resolver (merges AUTHORITATIVE HILT topology picks)
hilt_topology.py  HILT piping-topology resolver (AUTHORITATIVE nozzle<->valve connectivity)
obligations.py    Process/isolation obligation analysis
relief.py         Isolation scheme + relief-point detection
impact.py         Downstream impact analysis over HILT process-line graph
instrument_context.py  Instrument context classification (advisory, never upgrades status)
job_resolver.py   Job/P&ID resolution from boundary context
unigraph_metadata.py  Unigraph project metadata enrichment + job-id loading
flow.py           HILT flow-direction classifier (nozzle inlet/outlet)
evidence.py       Evidence classification (barrier/positive/verification)
planner.py        Required evidence checks (deterministic rules)
validator.py      Assurance status validation (AUTHORITATIVE)
loto.py           OSHA 1910.147(d) LOTO procedure sequencer (fixed phase order)
output.py         Final UI JSON payload builder
viewer.py         HTML overlay renderer (bbox/overlay canvas)
image.py          P&ID image download
domain/           Shared domain layer: enums, models, classification, serialization
eval_compare.py   Eval harness: agent vs deterministic baseline across equipment

agent/            AGENTIC runner — Gemini orchestrates deterministic stages as tools
  session.py      AgentSession: server-side pipeline state + audit trace
  tools.py        Tool wrappers (compact summaries) + dispatch registry
  prompts.py      System prompt (role, workflow, authoritative-validate rule)
  loop.py         Gemini tool-calling loop (ReAct) with max_steps + guardrail
  osha.py         Keyword RAG retriever over the bundled OSHA 1910.147 reference
  docs/           Bundled OSHA 29 CFR 1910.147 reference
  cli.py          `python -m agent` entrypoint
  __main__.py     module runner
```

### Agentic design (agent/)

The LLM is the **orchestrator**: it runs a tool-calling loop and decides which
deterministic stage to call next. The deterministic modules are preserved
UNCHANGED and exposed as tools. The deterministic `validate()` is the
AUTHORITATIVE source of `assurance_status`; the agent may gather more evidence
but cannot declare isolation on its own. For LOTO sequencing, the deterministic
`loto.py` produces the AUTHORITATIVE OSHA 1910.147(d) 6-phase order; the agent
uses `get_osha_guidance` (RAG) to reason about within-phase ordering and cite
provisions, but cannot reorder or skip phases. Nozzle->valve connectivity is
resolved AUTHORITATIVELY by `hilt_topology.py` and merged in `bbox.py`,
overriding JanusGraph depth+bbox picks. Heavy pipeline data stays server-side in
`AgentSession`; tools return compact summaries to keep Gemini's context small.
Every tool call is recorded in an audit trace (`<TAG>_trace.json`).

Available agent tools: `fetch_boundary`, `find_candidates`, `resolve_bboxes`,
`analyze_isolation_obligations`, `analyze_isolation_schemes_and_relief`,
`list_unselected_sources`, `investigate_source`, `build_evidence`,
`analyze_instrument_context`, `validate`, `get_osha_guidance`,
`build_loto_procedure`, `set_isolation_order`, `analyze_downstream_impact`,
`finalize_plan`.

## Pipeline Steps (run.py)

15 deterministic stages: (1) resolve Unigraph project metadata, (2) fetch
boundary from JanusGraph + resolve job, (3) select candidates, (4) resolve
bboxes from STLM/HILT, (5) isolation obligations, (6) isolation schemes +
relief, (7) instrument context, (8) evidence classification, (9) plan evidence
checks, (10) validate assurance, (11) downstream impact, (12) LOTO procedure,
(13) build final JSON payload, (14) download P&ID image, (15) write JSON +
HTML viewer.

## Configuration Notes

- Project context defaults from `project_config.json`; active profile is `aker_277` (`cnvrt_project_id=277`, `collection_id=206`, Unigraph `project_id=15`, traversal source `graph15_traversal`, host `44.217.77.13:18182`)
- Use `--project-profile biodiesel_graph9` to run the older biodiesel/FT-18 context
- API base URL: `https://api.plant360.ai:8080`; Unigraph metadata API base: `https://api.plant360.ai/plantgraph`
- Fallback `JOB_IDS_BY_NAME` hardcoded in `config.py` (pnid_1_bio_final=2099, pnid_2_bio_final=2100, etc.)
- Default output dir: `output/` (deterministic), `output_agent/` (agentic), repo-relative and git-ignored
- Agent default model: `gemini-2.5-flash` (override via `GEMINI_MODEL` env or `--model`)
- Isolation policy: max depth 3; eligible classes = valves/blinds/flanges/breakers/disconnects; conditional classes (check/control/undefined valve) selected but flagged manual-review
- Work scope defaults: intrusive=true, high_risk_service=true → requires positive isolation

## Output Files

Deterministic: `<TAG>.json` (final UI payload) + `<TAG>.html` (bbox overlay viewer).
Agentic: same two files plus `<TAG>_trace.json` (agent transcript + per-tool audit trace).
HTML viewer uses a blank canvas unless `--image-url` or the API image download succeeds.

## Gotchas

- **No lint/type-check/CI** — `tests/` has unit tests (run via `unittest`, NOT pytest); `eval_compare.py` is the agent-vs-baseline regression check; no other automation
- **Tests are pure-logic** — they run offline in <1s and do NOT hit the graph or API
- **Graph connection required** to actually run the pipeline — JanusGraph must be reachable at configured host/port
- **API auth needed** for bboxes/P&ID image — without `PLANT360_AUTH_TOKEN`, bboxes stay empty
- **Agent fails fast without `GEMINI_API_KEY`**
- **Job inference** — if `--job-name` not provided, the runner infers it from candidate/boundary `unit_name` matching `job_ids_by_name`
- **Agent non-determinism** — `temperature=0` but LLM calls vary; the audit trace is the source of truth
- **Safety** — the agent is a POC decision-support aid, not a certified LOTO procedure; `validate()` is authoritative

## File Ownership

- Deterministic pipeline code at project root (no subpackage); shared domain types in `domain/`
- Agentic code in the `agent/` package
- Single-module imports use absolute names (e.g., `from bbox import ...`)
- No generated code or migrations

## Unigraph Backend Reference

Local backend repo: `../../graph-convert` (Flask; route registration in `unigraph/api/routes.py`).
Key route for mapping CNVRT project/collection to Unigraph project metadata:
`GET /api/projects/by-cnvrt?cnvrt_project_id=<id>&cnvrt_collection_id=<id>` — the
preferred entry point over hardcoding `unigraph_project_id`.
