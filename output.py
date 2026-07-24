"""Output writers. Payload construction lives in payload.py.

Kept as the import surface for run.py, agent/cli.py and tests.
"""
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from domain.serialization import to_jsonable
from payload import build_final_payload, _int_or_text  # noqa: F401  (re-exported)
from viewer import render_viewer_html


def write_json(path, payload):
    path.write_text(json.dumps(to_jsonable(payload), indent=2) + "\n", encoding="utf-8")


def write_viewer(path, payload, image_url=""):
    image_src = _portable_image_src(Path(path), image_url)
    path.write_text(render_viewer_html(to_jsonable(payload), image_url=image_src), encoding="utf-8")


def _portable_image_src(viewer_path: Path, image_url: str) -> str:
    """Return an HTML image src that stays valid when the output folder moves."""
    if not image_url:
        return ""

    parsed = urlparse(str(image_url))
    if parsed.scheme in {"http", "https", "data"}:
        return str(image_url)
    if parsed.scheme and parsed.scheme != "file":
        return str(image_url)

    image_path = _local_image_path(parsed, image_url)
    if image_path is None:
        return str(image_url)

    viewer_dir = viewer_path.parent
    try:
        rel_path = os.path.relpath(image_path, viewer_dir)
    except ValueError:
        return str(image_url)
    return Path(rel_path).as_posix()


def _local_image_path(parsed, image_url: str) -> Path | None:
    if parsed.scheme == "file":
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            return None
        return Path(unquote(parsed.path))

    path = Path(str(image_url)).expanduser()
    if path.is_absolute():
        return path
    return None
