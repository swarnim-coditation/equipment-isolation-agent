# Equipment Isolation Runner — Agent Instructions

## Quick Start

```bash
cd "equipment-isolation-agent"
uv sync  # installs deps to .venv/

# Run isolation for equipment (deterministic baseline, no LLM)
uv run python -m run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100

# Run the AGENTIC (Gemini-orchestrated) isolation runner
uv run python -m agent --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100

# List available equipment
uv run python -m run --list-equipment

# Compare agent vs deterministic baseline across equipment
uv run python eval_compare.py BT-11 C-02
```

## Environment

- Python 3.11 managed by `uv`
- Dependencies in `pyproject.toml`: `gremlinpython>=3.6.2`, `requests>=2.31.0`, `google-genai>=1.0` (Gemini SDK, used by the agent)
- Virtual env at `.venv/` (created by `uv sync`)
- `.env` file ignored by git; copy `.env.example` to `.env` and add `PLANT360_AUTH_TOKEN` and `GEMINI_API_KEY`
- `GEMINI_API_KEY` is used by the agentic runner (`python -m agent`); the deterministic `run.py` does not need it

## Key Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Run isolation (deterministic) | `uv run python -m run --equipment <TAG> --job-name <NAME> --job-id <ID>` |
| Run isolation (agentic / Gemini) | `uv run python -m agent --equipment <TAG> [--model gemini-2.5-flash] [--max-steps 16]` |
| List equipment | `uv run python -m run --list-equipment [--equipment-limit N]` |
| Eval agent vs baseline | `uv run python eval_compare.py <TAG>...` (or `--limit N`) |
| With API auth | `PLANT360_AUTH_TOKEN=xxx uv run python -m run ...` or `--auth-token xxx` |
| Project context | Edit `project_config.json` or pass `--project-profile <NAME>` |
| Custom graph host/source | `--host <IP> --port <PORT> --project-id <UNIGRAPH_ID>`; traversal source defaults to `graph<UNIGRAPH_ID>_traversal` |
| Custom output dir | `--output-dir /path/to/dir` |
| Quiet mode | `--quiet` (prints only final paths/status) |

## Architecture

```
run.py          CLI entrypoint, orchestrates 9-step pipeline (deterministic baseline)
config.py       Frozen dataclasses: GraphConfig, ApiConfig, IsolationPolicy, WorkScope, RunConfig
graph_client.py Gremlin connection (DriverRemoteConnection), vertex helpers
boundary.py     Equipment/nozzle graph traversal
candidates.py   Deterministic isolation candidate selection
api_client.py   Plant360 REST client (STLM symbols, P&ID image)
bbox.py         STLM bbox resolver
flow.py         HILT flow-direction classifier (nozzle inlet/outlet)
hilt_topology.py HILT piping-topology isolation resolver (AUTHORITATIVE nozzle<->valve)
evidence.py     Evidence classification (barrier/positive/verification)
planner.py      Required evidence checks (deterministic rules)
validator.py    Assurance status validation
loto.py         Deterministic OSHA 1910.147(d) LOTO procedure sequencer (fixed phase order)
output.py       JSON + HTML viewer writer
image.py        P&ID image download
eval_compare.py Eval harness: agent vs deterministic baseline across equipment

agent/          AGENTIC runner — Gemini orchestrates the deterministic stages as tools
  session.py    AgentSession: server-side pipeline state + audit trace
  tools.py      Tool wrappers (compact summaries) + dispatch registry
  prompts.py    System prompt (role, workflow, authoritative-validate rule)
  loop.py       Gemini tool-calling loop (ReAct) with max_steps + guardrail
  osha.py       Keyword RAG retriever over the bundled OSHA 1910.147 reference
  docs/         Bundled OSHA 29 CFR 1910.147 reference (osha_1910_147.md)
  cli.py        `python -m agent` entrypoint
  __main__.py   module runner
```

### Agentic design (agent/)

The LLM is the **orchestrator**: it runs in a tool-calling loop and decides which
deterministic stage to call next. The deterministic modules (boundary, candidates,
bbox, evidence, planner, validator, output) are preserved UNCHANGED and exposed as
tools. The deterministic `validate()` is the AUTHORITATIVE source of
`assurance_status`; the agent may gather more evidence but cannot declare
isolation on its own. For LOTO sequencing, the deterministic `loto.py` produces the
AUTHORITATIVE OSHA 1910.147(d) 6-phase order (fixed by regulation); the agent uses
`get_osha_guidance` (RAG over the bundled OSHA doc) to reason about within-phase
device ordering and cite provisions, but cannot reorder or skip phases. Nozzle->valve
connectivity is resolved AUTHORITATIVELY by `hilt_topology.py` (the parsed P&ID piping
graph) and merged into the candidate set in `bbox.py`, overriding JanusGraph
depth+bbox picks that can be topologically wrong. Heavy pipeline data stays
server-side in `AgentSession`; tools return compact summaries to keep Gemini's
context small. Every tool call is recorded in an audit trace (`<TAG>_trace.json`).

## Configuration Notes

- Project context defaults come from `project_config.json`; active profile is `aker_277` (`cnvrt_project_id=277`, `collection_id=206`, Unigraph `project_id=13`, traversal source `graph13_traversal`)
- Use `--project-profile biodiesel_graph9` to intentionally run the older biodiesel/FT-18 context
- Override the derived traversal source only when needed with `--traversal-source <ALIAS>`
- API base URL: `https://api.plant360.ai:8080`
- Known job IDs in `config.py:JOB_IDS_BY_NAME` (pnid_1_bio_final=2099, pnid_2_bio_final=2100, etc.)
- Deterministic output dir: `/tmp/opencode/equipment_isolation_no_llm`
- Agentic output dir: `/tmp/opencode/equipment_isolation_agent`
- Agent default model: `gemini-2.5-flash` (override via `--model`); `gemini-2.0-flash` is deprecated/404
- Isolation policy: max depth 3, eligible classes (valves, blinds, flanges, breakers, disconnects)
- Work scope defaults: intrusive=true, high_risk_service=true → requires positive isolation

## Pipeline Steps (from run.py)

1. Fetch equipment boundary from JanusGraph
2. Select deterministic isolation candidates
3. Resolve candidate bboxes from STLM (requires API auth)
4. Classify deterministic evidence
5. Plan required evidence checks
6. Validate isolation assurance
7. Build final UI JSON payload
8. Download P&ID image (or use `--image-url`)
9. Write JSON output + HTML viewer

## Gotchas

- **No tests/lint/type-check/CI** — `eval_compare.py` is the only regression check (agent vs deterministic parity); verify manually via CLI otherwise
- **Graph connection required** — JanusGraph must be reachable at configured host/port
- **API auth needed for bboxes/P&ID image** — without token, bboxes stay empty
- **Agent needs `GEMINI_API_KEY`** — the agentic runner fails fast without it; the deterministic runner ignores it
- **Job inference** — if `--job-name` not provided, runner infers from candidate `unit_name` matching `JOB_IDS_BY_NAME`
- **HTML viewer** uses blank canvas unless `--image-url` or API image download succeeds
- **Output files**: deterministic `<TAG>_output.json` + `<TAG>_viewer.html`; agentic also writes `<TAG>_trace.json`
- **Agent non-determinism** — `temperature=0` but LLM calls can vary run-to-run; the audit trace is the source of truth
- **Safety** — the agent is a POC decision-support aid, not a certified LOTO procedure; `validate()` is authoritative

## File Ownership

- Deterministic pipeline code at project root (no subpackage)
- Agentic code in the `agent/` package
- Single-module imports use absolute names (e.g., `from bbox import ...`)
- No generated code or migrations
