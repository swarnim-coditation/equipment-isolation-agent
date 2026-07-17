"""Shared topology constants and helpers.

Single source of truth for the HILT process/context/signal line-class sets, the
distance/depth sentinels, and tag normalization. These were previously copy-pasted
across candidates/obligations/relief/impact/hilt_topology/instrument_context/bbox;
divergence between the copies silently changed connectivity in one traversal but
not another. Import from here instead.
"""

from __future__ import annotations

from typing import Any

from domain.classification import normalize_class

# HILT piping-graph line classifications. Membership must stay identical across
# every module that filters graph edges, or the same drawing yields different
# connectivity depending on which traversal ran.
PROCESS_LINE_CLASSES = {
    "primary_process_line",
    "secondary_process_line",
    "main_process_line",
    "process_line",
}

SIGNAL_LINE_CLASSES = {
    "instrument_signal_line",
    "signal_line",
    "electrical_signal_line",
}

CONTEXT_LINE_CLASSES = {"piping_to_instrument_line", "companion_line"} | SIGNAL_LINE_CLASSES

# Sentinels used as "sort last / infinitely far" placeholders when a candidate has
# no measured distance or graph depth.
FAR_DISTANCE = 999999.0
FAR_DEPTH = 99


def tag_prefix(value: Any) -> str:
    """Leading alphabetic prefix of a tag, e.g. 'PI-100' -> 'pi'. Used to spot
    instrument tag families (pressure indicators, gauges) for verification."""
    result = []
    for char in str(value or "").strip().lower():
        if char.isalpha():
            result.append(char)
            continue
        break
    return "".join(result)


def normalize_tag(value: Any) -> str:
    """Canonical tag/id normalization for cross-module joins.

    lower-cases, trims, and maps spaces and dashes to underscores so that
    'N-1', 'n 1' and 'N_1' all compare equal. Both sides of any comparison must
    use this function.
    """
    return normalize_class(value)


def nozzle_belongs_to_equipment(nozzle_tag: Any, equipment_tag: Any) -> bool:
    """True if a nozzle tag (e.g. 'N2_FT-18' or 'N2_FT18') belongs to the given
    equipment (e.g. 'FT-18').

    Matches the equipment suffix behind a separator boundary, and tolerates nozzle
    tags that drop the equipment's *internal* separators — a common P&ID convention
    where 'FT-18' appears inside a nozzle tag as 'FT18'. The leading '_' boundary is
    always required, so equipment '18' still does not match nozzle 'N218'.
    """
    noz = normalize_tag(nozzle_tag)
    eq = normalize_tag(equipment_tag)
    if not noz or not eq:
        return False
    return any(noz.endswith("_" + variant) for variant in {eq, eq.replace("_", "")} if variant)
