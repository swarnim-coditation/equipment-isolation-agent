from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.enums import FlowRole, ImpactSeverity, IsolationDecision, OverlayKind


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_any(cls, value: Any) -> "BBox | None":
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            x, y, width, height = [int(item) for item in value]
        except Exception:
            return None
        if width <= 0 or height <= 0:
            return None
        return cls(x=x, y=y, width=width, height=height)

    def to_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]

    def __iter__(self):
        return iter(self.to_list())

    def __getitem__(self, index: int) -> int:
        return self.to_list()[index]

    def __len__(self) -> int:
        return 4

    def __repr__(self) -> str:
        return repr(self.to_list())


@dataclass(frozen=True)
class SourcePath:
    source_component_tag: str = ""
    source_component_id: str = ""
    source_name: str = ""
    traversal_depth: int | None = None
    source_distance: float | None = None
    source_visual_distance: float | None = None
    reason: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourcePath":
        return cls(
            source_component_tag=str(payload.get("source_component_tag") or ""),
            source_component_id=str(payload.get("source_component_id") or ""),
            source_name=str(payload.get("source_name") or ""),
            traversal_depth=_optional_int(payload.get("traversal_depth")),
            source_distance=_optional_float(payload.get("source_distance")),
            source_visual_distance=_optional_float(payload.get("source_visual_distance")),
            reason=str(payload.get("reason") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "source_component_tag": self.source_component_tag,
            "source_component_id": self.source_component_id,
            "source_name": self.source_name,
            "traversal_depth": self.traversal_depth,
            "reason": self.reason,
        }
        if self.source_distance is not None:
            result["source_distance"] = self.source_distance
        if self.source_visual_distance is not None:
            result["source_visual_distance"] = self.source_visual_distance
        return {key: value for key, value in result.items() if value not in (None, "")}


@dataclass(frozen=True)
class CandidateClassification:
    raw_entity_class: str = ""
    raw_entity_type: str = ""
    class_values: tuple[str, ...] = ()
    matched_policy_classes: tuple[str, ...] = ()
    decision: IsolationDecision = IsolationDecision.NOT_ISOLATION
    is_barrier: bool = False
    is_positive_isolation: bool = False
    is_verification: bool = False

    @property
    def requires_manual_review(self) -> bool:
        return self.decision == IsolationDecision.CONDITIONAL_MANUAL_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_entity_class": self.raw_entity_class,
            "raw_entity_type": self.raw_entity_type,
            "class_values": list(self.class_values),
            "matched_policy_classes": list(self.matched_policy_classes),
            "decision": self.decision.value,
            "is_barrier": self.is_barrier,
            "is_positive_isolation": self.is_positive_isolation,
            "is_verification": self.is_verification,
            "requires_manual_review": self.requires_manual_review,
        }


@dataclass(frozen=True)
class IsolationCandidate:
    equipment_tag: str
    source_component_tag: str
    source_component_id: Any
    candidate_id: Any
    visual_id: str
    candidate_label: str
    tag_number: str | None
    isolation_method: str
    matched_keywords: tuple[str, ...]
    classification: CandidateClassification
    traversal_depth: int
    reason: str
    properties: dict[str, Any] = field(default_factory=dict)
    bbox: BBox | None = None
    source_paths: tuple[SourcePath, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IsolationCandidate":
        classification_payload = payload.get("classification") or {}
        decision = IsolationDecision(str(payload.get("policy_decision") or classification_payload.get("decision") or IsolationDecision.NOT_ISOLATION.value))
        classification = CandidateClassification(
            raw_entity_class=str(classification_payload.get("raw_entity_class") or (payload.get("properties") or {}).get("entity_class") or payload.get("candidate_label") or ""),
            raw_entity_type=str(classification_payload.get("raw_entity_type") or (payload.get("properties") or {}).get("entity_type") or ""),
            class_values=tuple(classification_payload.get("class_values") or ()),
            matched_policy_classes=tuple(payload.get("matched_keywords") or classification_payload.get("matched_policy_classes") or ()),
            decision=decision,
            is_barrier=bool(classification_payload.get("is_barrier", False)),
            is_positive_isolation=bool(classification_payload.get("is_positive_isolation", False)),
            is_verification=bool(classification_payload.get("is_verification", False)),
        )
        return cls(
            equipment_tag=str(payload.get("equipment_tag") or ""),
            source_component_tag=str(payload.get("source_component_tag") or ""),
            source_component_id=payload.get("source_component_id"),
            candidate_id=payload.get("candidate_id"),
            visual_id=str(payload.get("visual_id") or payload.get("candidate_id") or ""),
            candidate_label=str(payload.get("candidate_label") or ""),
            tag_number=payload.get("tag_number"),
            isolation_method=str(payload.get("isolation_method") or ""),
            matched_keywords=tuple(payload.get("matched_keywords") or ()),
            classification=classification,
            traversal_depth=int(payload.get("traversal_depth") or 0),
            reason=str(payload.get("reason") or ""),
            properties=dict(payload.get("properties") or {}),
            bbox=BBox.from_any(payload.get("bbox")),
            source_paths=tuple(SourcePath.from_dict(item) for item in payload.get("source_paths") or ()),
            extra={key: value for key, value in payload.items() if key not in _CANDIDATE_MODEL_KEYS},
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            **self.extra,
            "equipment_tag": self.equipment_tag,
            "source_component_tag": self.source_component_tag,
            "source_component_id": self.source_component_id,
            "candidate_id": self.candidate_id,
            "visual_id": self.visual_id,
            "candidate_label": self.candidate_label,
            "tag_number": self.tag_number,
            "isolation_method": self.isolation_method,
            "matched_keywords": list(self.matched_keywords),
            "policy_decision": self.classification.decision.value,
            "requires_manual_review": self.classification.requires_manual_review,
            "classification": self.classification.to_dict(),
            "traversal_depth": self.traversal_depth,
            "reason": self.reason,
            "properties": self.properties,
            "bbox": self.bbox.to_list() if self.bbox else [],
        }
        if self.source_paths:
            result["source_paths"] = [path.to_dict() for path in self.source_paths]
            result["source_path_count"] = len(self.source_paths)
        return result


@dataclass(frozen=True)
class DownstreamImpactWarning:
    severity: ImpactSeverity
    source_candidate_id: str
    source_tag: str
    affected_id: str
    affected_tag: str
    affected_class: str
    affected_type: str
    impact_type: str
    basis: str
    path_hops: int
    affected_bbox: BBox | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "source_candidate_id": self.source_candidate_id,
            "source_tag": self.source_tag,
            "affected_id": self.affected_id,
            "affected_tag": self.affected_tag,
            "affected_class": self.affected_class,
            "affected_type": self.affected_type,
            "impact_type": self.impact_type,
            "basis": self.basis,
            "path_hops": self.path_hops,
            "affected_bbox": self.affected_bbox.to_list() if self.affected_bbox else [],
        }


@dataclass(frozen=True)
class Overlay:
    kind: OverlayKind
    bbox: BBox
    label: str
    title: str
    css_class: str
    label_class: str
    summary_seq: str
    summary_uuid: str
    summary_reason: str
    severity: str = ""
    badge: str = ""


_CANDIDATE_MODEL_KEYS = {
    "equipment_tag",
    "source_component_tag",
    "source_component_id",
    "candidate_id",
    "visual_id",
    "candidate_label",
    "tag_number",
    "isolation_method",
    "matched_keywords",
    "classification",
    "policy_decision",
    "requires_manual_review",
    "traversal_depth",
    "reason",
    "properties",
    "bbox",
    "source_paths",
    "source_path_count",
}


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None
