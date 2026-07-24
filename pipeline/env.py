"""Small .env loader shared by entrypoints."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(*paths: str | Path) -> None:
    candidates = [Path(path) for path in paths if path]
    if not candidates:
        candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
