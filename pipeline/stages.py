"""Pipeline stages shared by BOTH runners.

Imports nothing from ``agent`` or ``run`` -- the dependency arrow points one way,
so the two runners cannot drift through this layer.
"""
from __future__ import annotations

from pipeline.errors import format_fatal_project_metadata
from unigraph_metadata import enrich_config_from_unigraph


def resolve_project_metadata(config):
    """Pipeline stage 1: enrich the config from Unigraph project metadata.

    Corrects graph.project_id / collection_id and populates job_ids_by_name from
    the ``by-cnvrt`` lookup. Everything downstream -- job inference, bbox
    resolution, therefore candidate selection -- depends on this having run.

    Raises on a fatal lookup: without it the run would silently proceed on
    profile-only defaults and produce a confidently wrong answer.
    """
    config, metadata_debug = enrich_config_from_unigraph(config)
    if metadata_debug.get("fatal"):
        raise RuntimeError(format_fatal_project_metadata(config, metadata_debug))
    return config, metadata_debug
