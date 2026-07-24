"""Shared fatal-condition detail builders for both runners.

The deterministic runner and the agent runner must AGREE on what constitutes a
fatal job-resolution / project-metadata failure and on what detail describes it.
They deliberately differ in what they DO about it:

- ``run.py`` raises, aborting the process (fail fast is correct for a CLI).
- ``agent/tools.py`` returns a dict, because raising inside a tool would be
  flattened to a string by ``call_tool`` and lose structure, and the audit trace
  must stay writable.

So the detail lives here and the policy lives in the callers. Previously the two
had drifted: the agent's variant omitted the equipment tag, skipped the config
fallbacks for cnvrt_project_id / collection_id, and returned both ``message`` and
``job_resolution_error`` unresolved instead of one precedence-ordered reason.
"""
from __future__ import annotations


def fatal_job_resolution_detail(config, boundary_data) -> dict | None:
    """Structured detail for a fatal job resolution, or None if not fatal.

    Reads the debug block written by ``resolve_job_from_boundary``. Falls back to
    the config for ids the debug block omits, so the caller always gets the most
    specific value available.
    """
    debug = ((boundary_data or {}).get("debug") or {})
    if not debug.get("fatal"):
        return None
    return {
        "equipment_tag": getattr(config, "equipment_tag", "") or "",
        "job_resolution": debug.get("job_resolution"),
        "job_resolution_error": debug.get("job_resolution_error"),
        "message": debug.get("message"),
        "reason": debug.get("message") or debug.get("job_resolution_error") or "Job resolution failed.",
        "pnid_names": debug.get("pnid_names") or [],
        "cnvrt_project_id": debug.get("cnvrt_project_id") or getattr(config, "cnvrt_project_id", "") or "",
        "collection_id": debug.get("collection_id") or getattr(config, "collection_id", "") or "",
    }


def format_fatal_job_resolution(detail: dict) -> str:
    """run.py's exact message string, rebuilt from the shared detail."""
    pnid_names = ", ".join(str(item) for item in (detail.get("pnid_names") or [])) or "-"
    return (
        "Configured CNVRT job resolution failed for "
        f"equipment {detail.get('equipment_tag')}; pnid_names={pnid_names}; "
        f"cnvrt_project_id={detail.get('cnvrt_project_id') or '-'}; "
        f"collection_id={detail.get('collection_id') or '-'}; "
        f"reason={detail.get('reason')}"
    )


def format_fatal_project_metadata(config, metadata_debug: dict) -> str:
    message = (metadata_debug or {}).get("error") or "Project metadata resolution failed."
    return (
        "Configured project metadata failed for "
        f"equipment {config.equipment_tag or '-'}; "
        f"unigraph_project_id={config.graph.project_id or '-'}; "
        f"cnvrt_project_id={config.cnvrt_project_id or '-'}; "
        f"collection_id={config.collection_id or '-'}; "
        f"reason={message}"
    )
