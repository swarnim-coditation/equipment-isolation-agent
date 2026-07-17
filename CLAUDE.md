# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth

`AGENTS.md` is the authoritative, up-to-date reference for this repo — read it first.
It has the full command table, per-file architecture map, pipeline steps, config
notes, and gotchas. This file only adds Claude-specific emphasis; do not duplicate
AGENTS.md here. (`README.md` is partially stale — e.g. it says "9-step pipeline"
and lists old output dirs; prefer AGENTS.md when they disagree.)

## Essential commands

```bash
uv sync                                                              # install deps to .venv/
uv run python -m run   --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100   # deterministic
uv run python -m agent --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100   # agentic (Gemini)
uv run python -m unittest discover -s tests                          # all tests (stdlib unittest, NOT pytest)
uv run python -m unittest tests.test_relief                          # a single test module
uv run python eval_compare.py BT-11 C-02                             # agent vs deterministic regression
```

No lint, type-check, or CI exist — `tests/` (pure-logic, offline, <1s) plus
`eval_compare.py` are the only automation.

## Architecture in one breath

Two runners share the same deterministic domain modules at project root:

- **`run.py`** — deterministic 15-stage pipeline, no LLM, no `GEMINI_API_KEY` needed.
- **`agent/`** — Gemini is the *orchestrator*; it calls the same deterministic
  stages as tools. It never re-implements domain logic.

The two must stay behaviorally consistent — a change to a domain module (e.g.
`relief.py`, `loto.py`, `validator.py`) affects both runners at once.

## Invariants that must not be broken

These are the reason the domain layer is factored the way it is — respect them
when editing:

- **`validator.validate()` is the sole authority for `assurance_status`.** The
  agent may gather more evidence but can never declare isolation itself.
- **`loto.py` owns the OSHA 1910.147(d) 6-phase order.** It is fixed and
  authoritative; the agent only reasons about *within-phase* ordering via
  `get_osha_guidance` (RAG) and may not reorder or skip phases.
- **`hilt_topology.py` is AUTHORITATIVE for nozzle↔valve connectivity**, merged
  in `bbox.py`, overriding JanusGraph depth+bbox picks.
- **`instrument_context.py` is advisory** — it never upgrades isolation status.
- Keep heavy pipeline data server-side in `agent/session.py`; agent tools return
  compact summaries to keep the model's context small.

## Running the real pipeline

Tests run offline, but actually running either runner needs a reachable
JanusGraph host (see `project_config.json`, active profile `aker_277`) and, for
bboxes/P&ID images, `PLANT360_AUTH_TOKEN`. The agent additionally requires
`GEMINI_API_KEY`. Copy `.env.example` → `.env` to set these; `.env` is parsed by
a hand-rolled `load_dotenv` (not python-dotenv), so keep it simple `KEY=value`.

## Conventions

- Root modules use flat absolute imports (`from bbox import ...`); shared types
  live in `domain/`.
- This is a POC decision-support aid, not a certified LOTO procedure — treat
  safety-relevant logic conservatively and keep `validate()` authoritative.
