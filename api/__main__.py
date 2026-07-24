"""Run the isolation API with uvicorn."""
from __future__ import annotations

import os

import uvicorn


def main():
    host = os.environ.get("EIA_HOST") or "0.0.0.0"
    port = int(os.environ.get("EIA_PORT") or "8088")
    uvicorn.run("api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
