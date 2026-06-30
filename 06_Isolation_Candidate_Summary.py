from langflow.custom import Component
from langflow.io import DataInput, Output
from langflow.schema import Message


def _unwrap_data(value):
    return value.value if hasattr(value, "value") else value


class IsolationCandidateSummary(Component):
    display_name = "Isolation Candidate Summary"
    description = "Formats isolation candidate data for readable debugging"
    icon = "message-square"
    name = "IsolationCandidateSummary"

    inputs = [
        DataInput(name="candidate_data", display_name="Isolation Candidates"),
    ]

    outputs = [
        Output(display_name="Summary", name="summary", method="build_summary"),
    ]

    def build_summary(self) -> Message:
        data = _unwrap_data(self.candidate_data) or {}

        if data.get("error"):
            return Message(text=f"Error: {data.get('message')}")

        candidates = data.get("candidates", [])

        lines = [
            f"Total candidates: {data.get('total_candidates', len(candidates))}",
            f"All candidates before ranking: {data.get('all_candidates_before_ranking')}",
            "",
        ]

        for index, candidate in enumerate(candidates[:50], start=1):
            energy_type = candidate.get("energy_type", [])
            matched_keywords = candidate.get("matched_keywords", [])
            property_preview = candidate.get("property_preview", {}) or {}
            lines.extend(
                [
                    f"Rank: {index}",
                    f"Equipment: {candidate.get('equipment_tag')}",
                    f"  Isolation tag: {candidate.get('tag_number')}",
                    f"  Candidate id: {candidate.get('candidate_id')}",
                    f"  Label: {candidate.get('candidate_label')}",
                    f"  Type: {candidate.get('tag_type')}",
                    f"  Energy: {', '.join(energy_type)}",
                    f"  Method: {candidate.get('isolation_method')}",
                    f"  Source: {candidate.get('source_component_tag')}",
                    f"  Depth: {candidate.get('traversal_depth')}",
                    f"  Confidence: {candidate.get('confidence')}",
                    f"  Keywords: {', '.join(matched_keywords)}",
                    f"  Reason: {candidate.get('reason')}",
                    f"  Property preview: {property_preview}",
                    "",
                ]
            )

        if len(candidates) > 50:
            lines.append(f"... {len(candidates) - 50} more candidates not shown")

        debug = data.get("debug", {})
        lines.extend(
            [
                "",
                "Debug:",
                f"  raw before dedupe: {debug.get('raw_candidate_count_before_dedupe')}",
                f"  deduped candidates: {debug.get('deduped_candidate_count')}",
                f"  returned candidates: {debug.get('returned_candidate_count')}",
                f"  skipped count: {debug.get('skipped_count')}",
            ]
        )

        skipped_samples = debug.get("skipped_samples", [])
        if skipped_samples:
            lines.extend(["", "Skipped samples:"])
            for sample in skipped_samples[:10]:
                lines.extend(
                    [
                        f"  Source: {sample.get('source_component_tag')}",
                        f"    reason: {sample.get('reason')}",
                        f"    label: {sample.get('label')}",
                        f"    tag: {sample.get('tag')}",
                    ]
                )

        return Message(text="\n".join(lines))
