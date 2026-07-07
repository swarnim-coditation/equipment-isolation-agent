#!/usr/bin/env python3
"""Minimal JanusGraph/Gremlin connection probe.

This intentionally avoids the equipment-isolation pipeline. It performs:
1. TCP connect check to host:port.
2. A single Gremlin traversal:
   g.V().limit(1).valueMap(True).toList()

Examples:
    python test_gremlin_connection.py
    python test_gremlin_connection.py --project-id 9
    python test_gremlin_connection.py --traversal-source graph9_traversal
"""
from __future__ import annotations

import argparse
import signal
import socket
import sys
import time

try:
    from config import GraphConfig
    from gremlin_python.driver import serializer
    from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
    from gremlin_python.process.anonymous_traversal import traversal
except ModuleNotFoundError as exc:
    if exc.name == "gremlin_python":
        print(
            "Missing dependency: gremlin_python. Run this from the project environment, for example:\n"
            "  uv run python test_gremlin_connection.py\n"
            "or activate/install dependencies first:\n"
            "  uv sync\n"
            "  source .venv/bin/activate\n"
            "  python test_gremlin_connection.py",
            file=sys.stderr,
        )
        sys.exit(4)
    raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test a Gremlin websocket endpoint with one simple traversal.")
    defaults = GraphConfig()
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", default=defaults.port)
    parser.add_argument("--project-id", default=defaults.project_id)
    parser.add_argument("--traversal-source", default="", help="Override Gremlin traversal source alias")
    parser.add_argument("--tcp-timeout", type=float, default=8.0)
    parser.add_argument("--gremlin-timeout", type=int, default=20)
    return parser.parse_args()


def tcp_probe(host: str, port: str, timeout: float) -> bool:
    started = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            print(f"TCP OK {host}:{port} elapsed={time.time() - started:.3f}s")
            return True
    except Exception as exc:
        print(f"TCP FAIL {host}:{port} elapsed={time.time() - started:.3f}s {type(exc).__name__}: {exc}")
        return False


def _timeout_handler(signum, frame):
    raise TimeoutError("Gremlin probe timed out")


def gremlin_probe(host: str, port: str, traversal_source: str, timeout: int) -> bool:
    url = f"ws://{host}:{port}/gremlin"
    print(f"Gremlin URL: {url}")
    print(f"Traversal source: {traversal_source}")
    started = time.time()
    conn = None
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        conn = DriverRemoteConnection(
            url,
            traversal_source,
            message_serializer=serializer.GraphSONSerializersV3d0(),
        )
        g = traversal().withRemote(conn)
        rows = g.V().limit(1).valueMap(True).toList()
        print(f"GREMLIN OK elapsed={time.time() - started:.3f}s row_count={len(rows)}")
        if rows:
            print(f"Sample row: {rows[0]}")
        return True
    except Exception as exc:
        print(f"GREMLIN FAIL elapsed={time.time() - started:.3f}s {type(exc).__name__}: {exc}")
        return False
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main() -> int:
    args = parse_args()
    traversal_source = args.traversal_source or f"graph{str(args.project_id).strip()}_traversal"
    tcp_ok = tcp_probe(args.host, args.port, args.tcp_timeout)
    if not tcp_ok:
        return 2
    gremlin_ok = gremlin_probe(args.host, args.port, traversal_source, args.gremlin_timeout)
    return 0 if gremlin_ok else 3


if __name__ == "__main__":
    sys.exit(main())
