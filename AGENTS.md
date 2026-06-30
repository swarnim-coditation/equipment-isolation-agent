# Equipment Isolation Runner — Agent Instructions

## Quick Start

```bash
cd "equipment-isolation-agent"
uv sync  # installs deps to .venv/

# Run isolation for equipment
uv run python -m run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100

# List available equipment
uv run python -m run --list-equipment
```

## Environment

- Python 3.11 managed by `uv`
- Dependencies in `pyproject.toml`: `gremlinpython>=3.6.2`, `requests>=2.31.0`
- Virtual env at `.venv/` (created by `uv sync`)
- `.env` file ignored by git; copy `.env.example` to `.env` and add `PLANT360_AUTH_TOKEN`
- `GEMINI_API_KEY` in `.env.example` appears unused

## Key Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| Run isolation | `uv run python -m run --equipment <TAG> --job-name <NAME> --job-id <ID>` |
| List equipment | `uv run python -m run --list-equipment [--equipment-limit N]` |
| With API auth | `PLANT360_AUTH_TOKEN=xxx uv run python -m run ...` or `--auth-token xxx` |
| Custom graph host | `--host <IP> --port <PORT> --project-id <ID>` |
| Custom output dir | `--output-dir /path/to/dir` |
| Quiet mode | `--quiet` (prints only final paths/status) |

## Architecture

```
run.py          CLI entrypoint, orchestrates 9-step pipeline
config.py       Frozen dataclasses: GraphConfig, ApiConfig, IsolationPolicy, WorkScope, RunConfig
graph_client.py Gremlin connection (DriverRemoteConnection), vertex helpers
boundary.py     Equipment/nozzle graph traversal
candidates.py   Deterministic isolation candidate selection
api_client.py   Plant360 REST client (STLM symbols, P&ID image)
bbox.py         STLM bbox resolver
evidence.py     Evidence classification (barrier/positive/verification)
planner.py      Required evidence checks (deterministic rules)
validator.py    Assurance status validation
output.py       JSON + HTML viewer writer
image.py        P&ID image download
```

## Configuration Notes

- Graph defaults: `host=44.217.77.13`, `port=8182`, `project_id=274`, traversal source `graph274_traversal`
- API base URL: `https://api.plant360.ai:8080`
- Known job IDs in `config.py:JOB_IDS_BY_NAME` (pnid_1_bio_final=2099, pnid_2_bio_final=2100, etc.)
- Output default: `/tmp/opencode/equipment_isolation_no_llm`
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

- **No tests, linting, type checking, or CI** — verify manually via CLI
- **Graph connection required** — JanusGraph must be reachable at configured host/port
- **API auth needed for bboxes/P&ID image** — without token, bboxes stay empty
- **Job inference** — if `--job-name` not provided, runner infers from candidate `unit_name` matching `JOB_IDS_BY_NAME`
- **HTML viewer** uses blank canvas unless `--image-url` or API image download succeeds
- **Output files**: `<TAG>_output.json` and `<TAG>_viewer.html` in output dir

## File Ownership

- All code at project root (no subpackage)
- Single module, imports use absolute names (e.g., `from bbox import ...`)
- No generated code or migrations