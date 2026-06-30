from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Data, Message
import json


EVIDENCE_STATE_CODE_VERSION = "14_isolation_evidence_state_2026-06-26_strict_evidence_v2"


POSITIVE_ENTITY_KEYWORDS = {
    "blind",
    "spade",
    "spectacle",
    "blank",
    "disconnect",
    "breaker",
    "spool",
}

POSITIVE_METHOD_KEYWORDS = {
    "insert blind",
    "install blind",
    "blind flange",
    "blank flange",
    "install spade",
    "spade",
    "spectacle blind",
    "remove spool",
    "removed spool",
    "disconnect",
    "rack out",
    "rack-out",
    "breaker",
    "physical separation",
}

VERIFICATION_ENTITY_KEYWORDS = {
    "bleed",
    "vent",
    "drain",
    "gauge",
    "indicator",
    "test point",
}

VERIFICATION_METHOD_KEYWORDS = {
    "bleed",
    "vent",
    "drain",
    "depressurize",
    "depressurise",
    "verify zero",
    "verify depressurization",
    "verify depressurisation",
    "test point",
    "pressure gauge",
    "pressure indicator",
}

VERIFICATION_TAG_PREFIXES = {"pi", "pg"}

BARRIER_ENTITY_KEYWORDS = {
    "valve",
    "blind",
    "spade",
    "disconnect",
    "breaker",
}

BYPASS_KEYWORDS = {"bypass", "alternate", "parallel", "jumper"}


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True)
        except Exception:
            return str(value)
    return str(value)


def _contains_any(value, keywords):
    haystack = _text(value).lower()
    return any(keyword in haystack for keyword in keywords)


def _compact(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _field_text(candidate, properties, *keys):
    return " ".join(
        _text(candidate.get(key) if key in candidate else properties.get(key))
        for key in keys
    ).lower()


def _tag_prefix(value):
    text = str(value or "").strip().lower()
    prefix = []
    for char in text:
        if char.isalpha():
            prefix.append(char)
            continue
        break
    return "".join(prefix)


def _evidence_flags(candidate):
    properties = candidate.get("properties", {}) or {}
    entity_text = _field_text(
        candidate,
        properties,
        "entity_class",
        "candidate_label",
        "tag_type",
        "type",
        "entity_type",
        "valve_type",
        "category",
    )
    method_text = _field_text(candidate, properties, "isolation_method", "reason")
    tag_text = _field_text(
        candidate,
        properties,
        "tag_number",
        "tag",
        "FunctionName",
        "name",
    )

    is_barrier = _contains_any(entity_text, BARRIER_ENTITY_KEYWORDS) or _contains_any(
        method_text, {"close and lock", "isolate", "lock valve"}
    )

    has_positive = _contains_any(
        entity_text, POSITIVE_ENTITY_KEYWORDS
    ) or _contains_any(method_text, POSITIVE_METHOD_KEYWORDS)

    tag_prefix = _tag_prefix(properties.get("tag") or candidate.get("tag_number"))
    function_prefix = _compact(properties.get("FunctionName"))
    has_verification = (
        _contains_any(entity_text, VERIFICATION_ENTITY_KEYWORDS)
        or _contains_any(method_text, VERIFICATION_METHOD_KEYWORDS)
        or tag_prefix in VERIFICATION_TAG_PREFIXES
        or function_prefix in VERIFICATION_TAG_PREFIXES
    )

    # Avoid false positives from generic metadata such as design_pressure,
    # pressure_class, connection_type=Flanged, or gate-valve line specs.
    if "valve" in entity_text and not _contains_any(
        entity_text + " " + method_text + " " + tag_text,
        POSITIVE_ENTITY_KEYWORDS | POSITIVE_METHOD_KEYWORDS,
    ):
        has_positive = False
    if "valve" in entity_text and not _contains_any(
        entity_text + " " + method_text + " " + tag_text,
        VERIFICATION_ENTITY_KEYWORDS | VERIFICATION_METHOD_KEYWORDS,
    ) and tag_prefix not in VERIFICATION_TAG_PREFIXES:
        has_verification = False

    has_bypass = _contains_any(entity_text + " " + method_text + " " + tag_text, BYPASS_KEYWORDS)
    return is_barrier, has_positive, has_verification, has_bypass


class IsolationEvidenceState(Component):
    display_name = "Isolation Evidence State"
    description = "Builds a structured evidence package for isolation planning and validation"
    icon = "package-search"
    name = "IsolationEvidenceState"

    inputs = [
        DataInput(name="candidate_data", display_name="Candidates With BBox"),
        DataInput(
            name="instructions_data",
            display_name="Plant Isolation Instructions",
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="Evidence Data", name="evidence_data", method="build_data"),
        Output(display_name="Evidence Summary", name="evidence_summary", method="build_summary"),
    ]

    def _build_payload(self):
        candidate_data = _unwrap_data(self.candidate_data) or {}
        instructions_data = _unwrap_data(getattr(self, "instructions_data", None)) or {}

        if candidate_data.get("error"):
            return candidate_data

        candidates = candidate_data.get("candidates", []) or []
        debug = dict(candidate_data.get("debug", {}) or {})
        context = dict(candidate_data.get("context", {}) or {})

        source_keys = set()
        covered_sources = set()
        candidate_summaries = []
        barrier_candidates = []
        positive_candidates = []
        verification_candidates = []
        bypass_candidates = []
        unresolved_bbox_candidate_ids = []

        for candidate in candidates:
            source_paths = candidate.get("source_paths") or []
            if not source_paths:
                source_paths = [
                    {
                        "source_component_id": candidate.get("source_component_id"),
                        "source_component_tag": candidate.get("source_component_tag"),
                        "traversal_depth": candidate.get("traversal_depth"),
                    }
                ]

            candidate_source_keys = []
            for path in source_paths:
                source_key = str(
                    path.get("source_component_id")
                    or path.get("source_component_tag")
                    or candidate.get("source_component_id")
                    or candidate.get("source_component_tag")
                    or ""
                ).strip()
                if source_key:
                    source_keys.add(source_key)
                    covered_sources.add(source_key)
                    candidate_source_keys.append(source_key)

            is_barrier, has_positive, has_verification, has_bypass = _evidence_flags(candidate)

            if is_barrier:
                barrier_candidates.append(candidate.get("candidate_id"))
            if has_positive:
                positive_candidates.append(candidate.get("candidate_id"))
            if has_verification:
                verification_candidates.append(candidate.get("candidate_id"))
            if has_bypass:
                bypass_candidates.append(candidate.get("candidate_id"))
            if not candidate.get("bbox"):
                unresolved_bbox_candidate_ids.append(candidate.get("candidate_id"))

            properties = candidate.get("properties", {}) or {}
            candidate_summaries.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "visual_id": candidate.get("visual_id"),
                    "tag_number": candidate.get("tag_number"),
                    "entity_class": properties.get("entity_class")
                    or candidate.get("candidate_label"),
                    "equipment_tag": candidate.get("equipment_tag"),
                    "source_component_tag": candidate.get("source_component_tag"),
                    "source_component_id": candidate.get("source_component_id"),
                    "source_keys": sorted(set(candidate_source_keys)),
                    "source_path_count": candidate.get("source_path_count", 1),
                    "traversal_depth": candidate.get("traversal_depth"),
                    "isolation_method": candidate.get("isolation_method"),
                    "bbox_resolved": bool(candidate.get("bbox")),
                    "barrier_evidence": is_barrier,
                    "positive_isolation_evidence": has_positive,
                    "verification_evidence": has_verification,
                    "bypass_or_alternate_route_evidence": has_bypass,
                }
            )

        expected_boundary_count = debug.get("boundary_component_boundary_count")
        if not isinstance(expected_boundary_count, int):
            try:
                expected_boundary_count = int(expected_boundary_count)
            except Exception:
                expected_boundary_count = None

        covered_count = len(covered_sources)
        missing_boundary_count = None
        if expected_boundary_count is not None:
            missing_boundary_count = max(expected_boundary_count - covered_count, 0)

        missing_evidence = []
        if not candidates:
            missing_evidence.append("No isolation candidates were found for the selected equipment.")
        if missing_boundary_count:
            missing_evidence.append(
                f"{missing_boundary_count} equipment boundary path(s) do not have a selected isolation candidate."
            )
        if not verification_candidates:
            missing_evidence.append(
                "No bleed, vent, drain, gauge, pressure indicator, or approved test-point evidence was found."
            )
        work_scope = instructions_data.get("work_scope") or {}
        requires_positive = any(
            bool(work_scope.get(key))
            for key in (
                "intrusive_work",
                "confined_space_entry",
                "hot_work",
                "high_risk_service",
            )
        )
        if requires_positive and not positive_candidates:
            missing_evidence.append(
                "Work scope requires positive isolation evidence, but no blind, spade, blank flange, disconnection, breaker, or equivalent was found."
            )
        if bypass_candidates:
            missing_evidence.append(
                "Potential bypass or alternate-route evidence was detected and must be resolved."
            )

        evidence_state = {
            "code_version": EVIDENCE_STATE_CODE_VERSION,
            "context": context,
            "plant_isolation_instructions": instructions_data.get(
                "plant_isolation_instructions"
            ),
            "work_scope": work_scope,
            "candidate_count": len(candidates),
            "expected_boundary_count": expected_boundary_count,
            "covered_boundary_source_count": covered_count,
            "missing_boundary_count": missing_boundary_count,
            "candidate_summaries": candidate_summaries,
            "barrier_candidate_ids": barrier_candidates,
            "positive_candidate_ids": positive_candidates,
            "verification_candidate_ids": verification_candidates,
            "bypass_candidate_ids": bypass_candidates,
            "unresolved_bbox_candidate_ids": unresolved_bbox_candidate_ids,
            "missing_evidence": missing_evidence,
        }

        debug.update(
            {
                "evidence_state_code_version": EVIDENCE_STATE_CODE_VERSION,
                "evidence_candidate_count": len(candidates),
                "evidence_expected_boundary_count": expected_boundary_count,
                "evidence_covered_boundary_source_count": covered_count,
                "evidence_missing_boundary_count": missing_boundary_count,
                "evidence_barrier_candidate_count": len(barrier_candidates),
                "evidence_positive_candidate_count": len(positive_candidates),
                "evidence_verification_candidate_count": len(verification_candidates),
                "evidence_missing_evidence_count": len(missing_evidence),
            }
        )

        return {
            **candidate_data,
            "error": False,
            "context": context,
            "debug": debug,
            "evidence_state": evidence_state,
            "missing_evidence": missing_evidence,
        }

    def build_data(self) -> Data:
        return Data(value=self._build_payload())

    def build_summary(self) -> Message:
        return Message(text="Isolation evidence state:\n" + json.dumps(self._build_payload(), indent=2))
