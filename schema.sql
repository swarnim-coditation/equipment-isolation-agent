CREATE TABLE IF NOT EXISTS isolation_runs (
    run_id TEXT PRIMARY KEY,
    equipment_tag TEXT NOT NULL,
    runner TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    request JSONB NOT NULL,
    agent JSONB,
    result JSONB,
    trace JSONB,
    artifacts JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    run_dir TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS isolation_runs_status_idx
    ON isolation_runs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS isolation_runs_equipment_idx
    ON isolation_runs (equipment_tag, created_at DESC);

CREATE TABLE IF NOT EXISTS isolation_run_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES isolation_runs(run_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS isolation_run_events_run_id_id_idx
    ON isolation_run_events (run_id, id);
