"""Lightweight keyword retrieval over the bundled OSHA 1910.147 reference.

No vector database -- the OSHA standard is a single document, so we split it into
sections (by markdown headers) and score sections by keyword overlap with the
query. This lets the agent pull relevant regulatory text on demand to ground its
LOTO sequencing reasoning in real citations (RAG), rather than relying on model
memory.
"""
from __future__ import annotations

from pathlib import Path

_DOC_PATH = Path(__file__).resolve().parent / "docs" / "osha_1910_147.md"

# Topic -> boosting keywords. A section containing any of these for a matched
# topic gets a higher score, improving precision over plain word overlap.
_TOPIC_KEYWORDS: dict[str, set[str]] = {
    "sequence": {"sequence", "order", "phase", "phases", "steps", "shall be done"},
    "stored energy": {"stored", "residual", "bleed", "vent", "drain", "dissipate", "restrain", "reaccumulation", "pressure"},
    "verification": {"verify", "verification", "zero energy", "test point", "gauge", "deenergization", "confirm"},
    "isolation": {"isolation", "isolate", "valve", "line valve", "blind", "flange", "energy isolating device"},
    "lockout device": {"lockout device", "lock", "tag", "affix", "individual lock", "safe or off"},
    "release": {"release", "restore", "reenergize", "removal", "return to service", "reverse"},
    "preparation": {"preparation", "magnitude", "type and magnitude", "hazards", "knowledge"},
    "shutdown": {"shutdown", "shut down", "orderly", "normal stopping", "stop button"},
    "definitions": {"energy isolating device", "definition", "energized", "blank flange", "slip blind"},
    "appendix": {"appendix", "typical", "minimal", "template", "sequence of lockout"},
    "mapping": {"mapping", "process equipment", "p&id", "pid", "bleed", "verification_candidate"},
    "scope": {"scope", "purpose", "minimum performance", "servicing and maintenance"},
}


def _load_sections() -> list[tuple[str, str]]:
    """Return [(header, body), ...] split on '## ' headers (keeps '### ' inside)."""
    text = _DOC_PATH.read_text(encoding="utf-8")
    parts = text.split("\n## ")
    sections: list[tuple[str, str]] = []
    for part in parts:
        if not part.strip():
            continue
        lines = part.splitlines()
        header = lines[0].strip().lstrip("#").strip()
        body = "\n".join(lines).strip()
        sections.append((header, body))
    return sections


_SECTIONS = _load_sections()


def list_osha_topics() -> list[str]:
    """Return the section headers + known topics, so the agent knows what it can ask about."""
    return [header for header, _ in _SECTIONS] + sorted(_TOPIC_KEYWORDS.keys())


def get_osha_guidance(topic: str, max_sections: int = 3) -> dict:
    """Retrieve the most relevant OSHA sections for a free-text topic/query.

    Returns {"query": topic, "sections": [{"header", "ref", "excerpt"}], "note": ...}.
    """
    query = (topic or "").strip().lower()
    if not query:
        return {
            "query": topic,
            "sections": [],
            "available_topics": list_osha_topics(),
            "note": "Empty query. Ask about e.g. 'stored energy', 'verification', 'isolation sequence'.",
        }

    query_terms = set(_tokenize(query))
    scored: list[tuple[float, str, str]] = []
    for header, body in _SECTIONS:
        header_l = header.lower()
        body_l = body.lower()
        score = 0.0
        # direct term overlap
        for term in query_terms:
            if term in header_l:
                score += 3.0
            if term in body_l:
                score += 1.0
        # topic-keyword boost
        for topic_key, keywords in _TOPIC_KEYWORDS.items():
            if topic_key in query or any(t in query_terms for t in topic_key.split()):
                hits = sum(1 for kw in keywords if kw in body_l or kw in header_l)
                score += 0.8 * hits
        if score > 0:
            scored.append((score, header, body))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:max_sections]
    sections_out = [
        {"header": header, "ref": _ref_for(header), "excerpt": _excerpt(body, 1200)}
        for _, header, body in top
    ]
    return {
        "query": topic,
        "match_count": len(scored),
        "sections": sections_out,
        "available_topics": list_osha_topics() if not top else [],
    }


def _ref_for(header: str) -> str:
    h = header.lower()
    if "mandatory sequence" in h or h.startswith("3"):
        return "29 CFR 1910.147(d)"
    if "release" in h or h.startswith("4"):
        return "29 CFR 1910.147(e)"
    if "appendix" in h or h.startswith("5"):
        return "1910.147 App A"
    if "definition" in h or h.startswith("2"):
        return "29 CFR 1910.147(b)"
    if "scope" in h or h.startswith("1"):
        return "29 CFR 1910.147(a)"
    if "mapping" in h:
        return "application note"
    return "29 CFR 1910.147"


def _excerpt(body: str, limit: int) -> str:
    body = body.strip()
    return body if len(body) <= limit else body[: limit - 3] + "..."


def _tokenize(text: str) -> list[str]:
    tokens = []
    current = ""
    for char in text:
        if char.isalnum():
            current += char
        else:
            if current:
                tokens.append(current)
                current = ""
    if current:
        tokens.append(current)
    return [t for t in tokens if len(t) > 2]
