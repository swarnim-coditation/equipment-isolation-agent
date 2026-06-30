# Local No-LLM Equipment Isolation Runner

Deterministic local runner for equipment isolation. It uses graph traversal and Plant360 APIs only. No LLM calls are made.

## Run

```bash
python -m local_no_llm.run --equipment BT-11 --job-name pnid_2_bio_final --job-id 2100
```

List available equipment tags from JanusGraph:

```bash
python -m local_no_llm.run --list-equipment
```

The list includes graph id, tag, name, entity class, job id, and PNID/job name when the equipment can be matched to STLM data.

Limit the list for quick browsing:

```bash
python -m local_no_llm.run --list-equipment --equipment-limit 20
```

For bbox resolution, provide a Plant360 API token either with `--auth-token` or the `PLANT360_AUTH_TOKEN` environment variable. Without API auth, the runner still returns graph candidates and assurance status, but bboxes remain empty.

Run from this directory:

```bash
cd "AI Agents Export/Equipment Isolation"
```

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
run.py          CLI entrypoint
```
