# Equipment Isolation API Requests

This is the Bruno/OpenCollection request pack for API smoke and regression checks.

Run the API first:

```bash
uv run python -m api
```

Recommended smoke order:

1. `health` should return `200`.
2. `reject blank equipment tag` should return `422`.
3. `reject missing project context` should return `422`.
4. `get unknown run` should return `404`.
5. `list equipment` should return `200` when auth and graph access are configured.
6. `create isolation run` should return `202`; copy its `run_id` into `RUN_ID`.
7. `list runs` should return lightweight run summaries, newest first.
8. Poll `get run status` until `succeeded` or `failed`; this endpoint is intentionally lightweight and should not include the full `result`.
9. Use `get run result`, `get run trace`, `stream run events`, `get run viewer`, and `get P&ID image` against that `RUN_ID`.

Auth regression:

- Run read endpoints require inherited Bearer auth. Without auth they return `401`; with auth, `get unknown run` should return `404`.
- `reject body token without bearer auth` should return `400 missing_auth_token` only when the server-side `PLANT360_AUTH_TOKEN` fallback is disabled. If that local/dev fallback is configured, this request can be accepted because the server uses its own token.

Coverage notes:

- The committed unit tests cover offline API contract, run lifecycle, SSE replay, and repository failure behavior.
- This request pack covers live HTTP behavior and the real graph/Gemini/Plant360 path when credentials are configured.
- It does not inspect Postgres rows directly; verify that separately with SQL when testing persistence.
