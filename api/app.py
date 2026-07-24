"""FastAPI application factory."""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import PostgresRunRepository, auto_init_schema_on_startup, init_schema, postgres_config_from_env
from api.routes import router
from api.runs import RunStore
from pipeline.env import load_dotenv

LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    load_dotenv()
    runs_dir = os.environ.get("EIA_RUNS_DIR") or "api_runs"
    max_workers = int(os.environ.get("EIA_MAX_CONCURRENT_RUNS") or "2")
    run_timeout = int(os.environ.get("EIA_RUN_TIMEOUT_SECONDS") or "900")
    pg_config = postgres_config_from_env()
    repository = None
    if pg_config.configured:
        repository = PostgresRunRepository(pg_config)
    run_store = RunStore(
        runs_dir,
        max_workers=max_workers,
        run_timeout_seconds=run_timeout,
        repository=repository,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if pg_config.configured and auto_init_schema_on_startup():
            try:
                init_schema(pg_config)
            except Exception:
                LOGGER.exception("Postgres schema initialization failed; API will continue with degraded persistence")
        yield
        run_store.shutdown()

    app = FastAPI(title="Equipment Isolation Agent API", version="0.1.0", lifespan=lifespan)
    origins = [item.strip() for item in os.environ.get("EIA_CORS_ORIGINS", "").split(",") if item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.state.run_store = run_store
    app.include_router(router)
    return app


app = create_app()
