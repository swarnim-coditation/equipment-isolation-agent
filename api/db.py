"""Minimal Postgres access layer for API run persistence."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.session import jsonable


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "prefer"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.dbname and self.user)


def postgres_config_from_env() -> PostgresConfig:
    return PostgresConfig(
        host=os.environ.get("POSTGRES_HOST", "").strip(),
        port=int(os.environ.get("POSTGRES_PORT") or "5432"),
        dbname=os.environ.get("POSTGRES_DB", "").strip(),
        user=os.environ.get("POSTGRES_USER", "").strip(),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        sslmode=os.environ.get("POSTGRES_SSLMODE", "prefer").strip() or "prefer",
    )


def postgres_configured() -> bool:
    return postgres_config_from_env().configured


def auto_init_schema_on_startup() -> bool:
    return os.environ.get("EIA_AUTO_INIT_SCHEMA_ON_STARTUP", "").strip().lower() in {"1", "true", "yes", "on"}


def _connect_kwargs(config: PostgresConfig) -> dict[str, Any]:
    return {
        "host": config.host,
        "port": config.port,
        "dbname": config.dbname,
        "user": config.user,
        "password": config.password,
        "sslmode": config.sslmode,
    }


def _connect(config: PostgresConfig | None = None):
    import psycopg

    config = config or postgres_config_from_env()
    return psycopg.connect(**_connect_kwargs(config))


def init_schema(config: PostgresConfig | None = None, schema_path: str | Path = "schema.sql") -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    with _connect(config) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def _pool_max_size() -> int:
    return int(os.environ.get("POSTGRES_POOL_MAX_SIZE") or "8")


def _pool_timeout() -> float:
    return float(os.environ.get("POSTGRES_POOL_TIMEOUT_SECONDS") or "5")


class PostgresRunRepository:
    def __init__(self, config: PostgresConfig | None = None):
        self.config = config or postgres_config_from_env()
        self._pool = None
        self._pool_lock = threading.Lock()

    @contextmanager
    def _connection(self):
        with self._get_pool().connection() as conn:
            yield conn

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool

                    self._pool = ConnectionPool(
                        "",
                        kwargs=_connect_kwargs(self.config),
                        min_size=0,
                        max_size=_pool_max_size(),
                        timeout=_pool_timeout(),
                    )
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def insert_run(self, record, request_payload: dict) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO isolation_runs (
                        run_id, equipment_tag, runner, status, created_at, started_at,
                        finished_at, request, agent, result, trace, artifacts, error, run_dir
                    )
                    VALUES (
                        %(run_id)s, %(equipment_tag)s, %(runner)s, %(status)s, %(created_at)s,
                        %(started_at)s, %(finished_at)s, %(request)s::jsonb, %(agent)s::jsonb,
                        %(result)s::jsonb, %(trace)s::jsonb, %(artifacts)s::jsonb,
                        %(error)s::jsonb, %(run_dir)s
                    )
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    _record_params(record, request_payload=request_payload),
                )
            conn.commit()

    def update_run(self, record) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE isolation_runs
                    SET status = %(status)s,
                        started_at = %(started_at)s,
                        finished_at = %(finished_at)s,
                        agent = %(agent)s::jsonb,
                        result = %(result)s::jsonb,
                        trace = %(trace)s::jsonb,
                        artifacts = %(artifacts)s::jsonb,
                        error = %(error)s::jsonb
                    WHERE run_id = %(run_id)s
                    """,
                    _record_params(record),
                )
            conn.commit()

    def append_event(self, run_id: str, event: dict) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO isolation_run_events (run_id, event)
                    VALUES (%s, %s::jsonb)
                    """,
                    (run_id, _json(event)),
                )
            conn.commit()

    def delete_run(self, run_id: str) -> None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM isolation_runs WHERE run_id = %s", (run_id,))
            conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, equipment_tag, runner, status, created_at, started_at,
                           finished_at, agent, result, trace, artifacts, error, run_dir
                    FROM isolation_runs
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_dict(row)

    def list_runs(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id, equipment_tag, runner, status, created_at, started_at,
                           finished_at, agent, NULL::jsonb AS result, NULL::jsonb AS trace,
                           artifacts, error, run_dir
                    FROM isolation_runs
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_events(self, run_id: str, after_id: int = 0) -> list[dict]:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, event
                    FROM isolation_run_events
                    WHERE run_id = %s AND id > %s
                    ORDER BY id
                    """,
                    (run_id, after_id),
                )
                rows = cur.fetchall()
        return [{"id": row[0], "event": row[1]} for row in rows]


def _record_params(record, request_payload: dict | None = None) -> dict:
    return {
        "run_id": record.run_id,
        "equipment_tag": record.equipment_tag,
        "runner": record.runner,
        "status": record.status,
        "created_at": _dt(record.created_at),
        "started_at": _dt(record.started_at),
        "finished_at": _dt(record.finished_at),
        "request": _json(request_payload or {}),
        "agent": _json(record.agent),
        "result": _json(record.result),
        "trace": _json(record.trace),
        "artifacts": _json(record.artifacts or {}),
        "error": _json(record.error),
        "run_dir": str(record.run_dir),
    }


def _json(value: Any) -> str:
    return json.dumps(jsonable(value), default=str)


def _dt(value: float | None):
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _ts(value) -> float | None:
    if value is None:
        return None
    return value.timestamp()


def _row_to_dict(row) -> dict:
    return {
        "run_id": row[0],
        "equipment_tag": row[1],
        "runner": row[2],
        "status": row[3],
        "created_at": _ts(row[4]),
        "started_at": _ts(row[5]),
        "finished_at": _ts(row[6]),
        "agent": row[7],
        "result": row[8],
        "trace": row[9],
        "artifacts": row[10] or {},
        "error": row[11],
        "run_dir": row[12],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Equipment isolation API database utility.")
    parser.add_argument("command", choices=["init"])
    parser.add_argument("--schema", default="schema.sql")
    args = parser.parse_args(argv)
    if args.command == "init":
        init_schema(postgres_config_from_env(), args.schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
