# De-game the qupick MCP server for deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip the inherited trading-game/browser surface from the copied backend so the deployed artifact is a single-purpose qupick MCP server (6 tools, Bearer auth, `/healthz`, SMTP, Supabase) with no dead code.

**Architecture:** The backend is a copy of the `qtw-tradinggame` project. Only the 6 MCP operations (`register_agent`, `get_agent`, `optimize`, `get_market`, `get_leaderboard`, `ping_backend`) are consumed by qupick. This plan removes the browser/booth machinery that nothing on this instance consumes: CORS, the QR deep-link base, the WebSocket live-push subsystem (routes + in-process event bus), and the background mark-to-market loop. The leaderboard remains, scored **on solve** (`apply_solve`) instead of drifting live.

**Tech Stack:** Python 3.13, FastAPI, fastapi-mcp, SQLAlchemy (async) + asyncpg, uv, ruff, pytest.

## Global Constraints

- Python ≥3.11, run via `uv`. Lint with `ruff check`; format with `ruff format`. Line length 100.
- Absolute imports only (no relative `..` in app code is N/A here — existing code uses package-relative `from ..x`; match the existing style).
- **Replace, don't deprecate** — delete removed code entirely; no shims.
- The DB-backed test suite needs Postgres `qtw_test` (`tests/conftest.py`). **Docker is unavailable in the authoring environment**, so each task's full-suite step must run where Postgres is reachable: `docker compose up -d db && docker compose exec -T db createdb -U qtw qtw_test && cd backend && uv run pytest -q`. In any environment, `ruff check` and the import-smoke command always run.
- Import-smoke command (used as a fast local gate in several tasks):
  `cd backend && uv run python -c "from backend.api.app import create_app; create_app(); print('app builds')"`

**Current working-tree state (already applied this session, uncommitted):** SMTP email sender replacing Resend (`email/sender.py`, `config.py`, `tests/test_email.py`), env-driven `PORT` (`Dockerfile`), SMTP env in `docker-compose.yml`, and partial `docs/deployment-prep.md` updates. This plan builds on that tree.

---

## Pre-task: branch

The current branch is `docs/deploy-prep` but this is now code. Move to a feature branch first.

- [ ] **Step 1: Create the feature branch**

```bash
cd /home/konrad/Quip/qubitrefill
git checkout -b feat/degame-mcp-deploy
```

---

### Task 1: Remove CORS and `QR_BASE_URL` (zero-risk dead config)

Neither has a consumer on an MCP-only instance: no browser sends an `Origin` header, and `QR_BASE_URL` has no reader in `backend/src` (the QR image is built frontend-side).

**Files:**
- Modify: `backend/src/backend/config.py:130-144` (remove CORS + QR block)
- Modify: `backend/src/backend/api/app.py:21` (drop `CORSMiddleware` import), `app.py:90-98` (drop middleware block + fix comment)

**Interfaces:**
- Produces: `create_app()` no longer adds `CORSMiddleware`; `config` no longer defines `CORS_ORIGINS` or `QR_BASE_URL`.

- [ ] **Step 1: Remove the CORS/QR config block**

In `backend/src/backend/config.py`, delete the entire API section (the `_DEFAULT_CORS_ORIGINS`, `_cors_env`, `CORS_ORIGINS`, and `QR_BASE_URL` definitions and their comments), so the file goes straight from the market-data section to the Database section.

- [ ] **Step 2: Remove the CORS middleware and its import in `app.py`**

Delete `from fastapi.middleware.cors import CORSMiddleware` (line 21). Replace the middleware block so only the auth middleware remains:

```python
    app = FastAPI(title="QTW 2026 Trading Game", lifespan=lifespan)
    # Per-agent Bearer auth. fastapi-mcp re-runs this on the internal /mcp dispatch.
    app.add_middleware(APIKeyMiddleware)
    app.include_router(routes.router)
```

(The `title` is fixed in Task 4.)

- [ ] **Step 3: Lint and import-smoke**

Run: `cd backend && uv run ruff check src && uv run python -c "from backend.api.app import create_app; create_app(); print('app builds')"`
Expected: `All checks passed!` then `app builds`.

- [ ] **Step 4: Confirm no stray references**

Run: `cd /home/konrad/Quip/qubitrefill && rg -n "CORS_ORIGINS|QR_BASE_URL|CORSMiddleware" backend/src || echo "clean"`
Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/backend/config.py backend/src/backend/api/app.py
git commit -m "refactor(backend): drop CORS and QR config (browser-only, unused)"
```

---

### Task 2: Remove the WebSocket + event-bus subsystem

The WS routes (`/ws/agent/{handle}`, `/tv/events`) are not MCP tools and have no client here; the in-process event bus exists only to feed them. With no subscribers, `EventBus.publish` is already a no-op, so removing the publishers changes no observable MCP behavior.

**Files:**
- Delete: `backend/src/backend/api/ws.py`
- Delete: `backend/src/backend/events/bus.py`, `backend/src/backend/events/feeds.py`, `backend/src/backend/events/__init__.py` (whole `events/` package)
- Modify: `backend/src/backend/api/app.py` (drop `ws` + `get_bus` imports, `include_router(ws.router)`)
- Modify: `backend/src/backend/api/routes.py:24-25` (drop events imports), `routes.py:36` (drop `AgentUpdate` import), `routes.py:152-161` (remove publish block)
- Test: `backend/tests/test_api.py:114-121` (remove the WebSocket test)

**Interfaces:**
- Produces: `optimize` returns the same `RoutingResult` but performs no bus publish. No `events` package. No `ws` router.

- [ ] **Step 1: Delete the subsystem files**

```bash
cd /home/konrad/Quip/qubitrefill/backend
git rm src/backend/api/ws.py src/backend/events/bus.py src/backend/events/feeds.py src/backend/events/__init__.py
rmdir src/backend/events 2>/dev/null || true
```

- [ ] **Step 2: Drop `ws`/`get_bus` wiring in `app.py`**

In `backend/src/backend/api/app.py`: change `from . import routes, ws` to `from . import routes`; delete `from ..events.bus import get_bus`; delete the `app.include_router(ws.router)` line.

- [ ] **Step 3: Remove the publish block in `routes.py::optimize`**

Delete the events imports (`from ..events import feeds` and `from ..events.bus import get_bus`) and `AgentUpdate` from the schemas import. Replace lines 150-172 (from `await repo.apply_solve(...)` through the `return RoutingResult(...)`) with the publish-free version:

```python
    await repo.apply_solve(agent, outcome.holdings_units, outcome.total, outcome.provider_role)
    job = await JobRepo(session).record(agent.id, outcome.provenance)

    return RoutingResult(
        provider=outcome.provider_label,
        provider_type=outcome.provider_role,
        solve_time=outcome.solve_time_s,
        vs_classical=outcome.vs_classical,
        portfolio=outcome.portfolio,
        kind="first" if outcome.is_first else "retune",
        job_id=job.id,
        solved_at=job.solved_at,
    )
```

- [ ] **Step 4: Remove the WebSocket test**

In `backend/tests/test_api.py`, delete `test_websocket_streams_agent_update` (the function at line 114 and its body through line 121).

- [ ] **Step 5: Lint, import-smoke, reference sweep**

Run: `cd backend && uv run ruff check src tests && uv run python -c "from backend.api.app import create_app; create_app(); print('app builds')"`
Expected: `All checks passed!` then `app builds`.
Run: `rg -n "events\.bus|events\.feeds|from ..events|get_bus|EventBus|ws\.router|websocket" src tests || echo "clean"`
Expected: `clean`.

- [ ] **Step 6: Full suite (where Postgres is available)**

Run: `docker compose up -d db && docker compose exec -T db createdb -U qtw qtw_test 2>/dev/null; cd backend && uv run pytest -q`
Expected: all pass (one fewer test than before — the WS test is gone).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(backend): remove WebSocket live-push subsystem and event bus"
```

---

### Task 3: Remove the mark-to-market loop and dead P&L module

The MTM loop is the only consumer of `mark_to_market` and `AgentRepo.set_valuation`, and its only persisted effect feeds the leaderboard between solves. After removal the leaderboard reflects each agent's last `apply_solve` valuation. Removing it also drops the in-process background task entirely.

**Files:**
- Delete: `backend/src/backend/orchestration/scheduler.py`, `backend/src/backend/financial/pnl.py`
- Modify: `backend/src/backend/api/app.py` (drop scheduler import, `get_sessionmaker` import if now unused, simplify `lifespan`, fix `_check_market_source` log text)
- Modify: `backend/src/backend/persistence/agents.py:16` (drop `AgentUpdate` import), `agents.py:119-124` (remove `set_valuation`)
- Modify: `backend/src/backend/api/schemas.py:88` (remove `AgentUpdate` class)
- Delete: `backend/tests/test_pnl.py`
- Modify: `backend/tests/test_persistence.py:7` (drop `AgentUpdate` import), `test_persistence.py:67-78` (rewrite leaderboard test to use `apply_solve`)

**Interfaces:**
- Produces: `lifespan` only initializes the engine and creates tables — no background task. `AgentRepo` has no `set_valuation`. `schemas` has no `AgentUpdate`.

- [ ] **Step 1: Delete the loop and the dead P&L module**

```bash
cd /home/konrad/Quip/qubitrefill/backend
git rm src/backend/orchestration/scheduler.py src/backend/financial/pnl.py tests/test_pnl.py
```

- [ ] **Step 2: Simplify the `app.py` lifespan**

In `backend/src/backend/api/app.py`: delete `from ..orchestration.scheduler import run_mtm_loop` and remove `get_sessionmaker` from the `from ..db.engine import ...` line (keep `get_engine`, `init_engine`). Also delete the now-unused `from ..events.bus import get_bus` if Task 2 left it. Replace the lifespan body:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _check_market_source()
    init_engine()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Note: the engine is process-wide and intentionally NOT disposed here —
    # tests share it across many app instances and own its lifecycle.
```

- [ ] **Step 3: Fix the `_check_market_source` log text**

In the same file, in `_check_market_source`, change the error message `"... — solves and MTM will fail until it is up ..."` to `"... — solves will fail until it is up ..."`.

- [ ] **Step 4: Remove `set_valuation` and its import in `agents.py`**

In `backend/src/backend/persistence/agents.py`: change `from ..api.schemas import AgentConfig, AgentUpdate, SliderValues` to `from ..api.schemas import AgentConfig, SliderValues`; delete the `set_valuation` method (lines 119-124).

- [ ] **Step 5: Remove the `AgentUpdate` schema**

In `backend/src/backend/api/schemas.py`, delete the `class AgentUpdate(BaseModel): ...` block (line 88 and its fields).

- [ ] **Step 6: Rewrite the leaderboard persistence test**

In `backend/tests/test_persistence.py`: change `from backend.api.schemas import AgentUpdate, SliderValues` to `from backend.api.schemas import SliderValues`. Replace the two `set_valuation` lines in `test_leaderboard_ranks_by_total_descending`:

```python
        await repo.apply_solve(low, {"BTC": 0.1}, total=9_500.0, provider_type="CPU")
        await repo.apply_solve(high, {"BTC": 0.2}, total=12_000.0, provider_type="QPU")
```

(`apply_solve` sets `total`/`pl_usd`/`pl_pct` from `total - bankroll`; bankroll is `config.BANKROLL_USD` = 10_000, so `pl_usd` is -500 and +2_000 — same ordering the test asserts.)

- [ ] **Step 7: Lint, import-smoke, reference sweep**

Run: `cd backend && uv run ruff check src tests && uv run python -c "from backend.api.app import create_app; create_app(); print('app builds')"`
Expected: `All checks passed!` then `app builds`.
Run: `rg -n "run_mtm_loop|scheduler|set_valuation|mark_to_market|AgentUpdate" src tests || echo "clean"`
Expected: `clean`.

- [ ] **Step 8: Full suite (where Postgres is available)**

Run: `cd backend && uv run pytest -q`
Expected: all pass (test_pnl.py gone; leaderboard test green via `apply_solve`).

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(backend): remove mark-to-market loop; leaderboard scores on solve"
```

---

### Task 4: De-game the app branding

Small cosmetic cleanup so the OpenAPI/MCP surface no longer says "Trading Game".

**Files:**
- Modify: `backend/src/backend/api/app.py` (FastAPI `title`, module docstring line referencing the MTM scheduler)

- [ ] **Step 1: Rename the app title and fix the docstring**

In `backend/src/backend/api/app.py`: change `FastAPI(title="QTW 2026 Trading Game", lifespan=lifespan)` to `FastAPI(title="qupick", lifespan=lifespan)`. In the module docstring near the top, change the line that says it "initialises the DB (engine + tables) and runs the MTM scheduler" to "initialises the DB (engine + tables)".

- [ ] **Step 2: Lint and import-smoke**

Run: `cd backend && uv run ruff check src && uv run python -c "from backend.api.app import create_app; print(create_app().title)"`
Expected: `All checks passed!` then `qupick`.

- [ ] **Step 3: Commit**

```bash
git add backend/src/backend/api/app.py
git commit -m "chore(backend): rename app title to qupick"
```

---

### Task 5: Update deployment docs to the de-gamed reality

Remove every browser/CORS/QR/MTM/Frontend reference from the active deployment doc so it matches the shipped artifact.

**Files:**
- Modify: `docs/deployment-prep.md` (rows/sections listed below)

- [ ] **Step 1: Edit `docs/deployment-prep.md`**

Apply all of these:
- §0 Hosting row: change the consequence to "Single instance is fine but no longer forced — the background mark-to-market loop was removed; `create_all` on a shared DB is the only multi-instance concern (see §8)."
- §0 Schema row: keep, unchanged.
- §1 components table: **delete** the `Frontend (MVP)` row.
- §2a accounts table: **delete** row 5 (`Netlify`); renumber the remaining rows.
- §3 must-set env table: **delete** the `CORS_ORIGINS` and `QR_BASE_URL` rows; keep `PORT`.
- §3 optional env table: keep `SMTP_PORT`/`SMTP_STARTTLS`.
- §4: **delete the whole "Config (all env-driven)" section** (CORS/QR no longer exist).
- §5: **delete the "Background scheduler" bullet** (the loop is gone). In the TLS bullet, leave the Caddy text.
- §6 security checklist: **delete** the "TLS in front of the backend (keys flow on every request)" item's CORS dependency? No — keep TLS. **Delete** any CORS line if present (none in §6) — leave as is.
- §7 verification: **delete** step 7 ("CORS / QR — load the real frontend origin ...").
- §8 known gaps: **delete** the "`CORS_ORIGINS` and `QR_BASE_URL` default to the demo domain" bullet.

- [ ] **Step 2: Reference sweep on active docs**

Run: `cd /home/konrad/Quip/qubitrefill && rg -n "CORS|QR_BASE_URL|run_mtm_loop|Frontend \(MVP\)|/ws/|/tv/|mark-to-market loop" docs/deployment-prep.md || echo "clean"`
Expected: `clean`.

- [ ] **Step 3: Commit**

```bash
git add docs/deployment-prep.md
git commit -m "docs: align deployment-prep with the de-gamed MCP server"
```

---

## Self-Review

- **Spec coverage:** Tier 1 (CORS+QR) → Task 1; Tier 2 (WS+bus) → Task 2; Tier 3 (MTM, leaderboard-on-solve) → Task 3; de-game branding → Task 4; docs → Task 5. All audited surfaces covered.
- **Closure check:** every consumer of removed symbols (`CORS_ORIGINS`, `QR_BASE_URL`, `ws.router`, `get_bus`/`EventBus`/`feeds`, `run_mtm_loop`, `set_valuation`, `mark_to_market`, `AgentUpdate`) is handled in the task that removes it; `test_pnl.py`, the WS test, and the leaderboard persistence test are all addressed.
- **Type consistency:** `apply_solve(agent, holdings_units, total, provider_type)` is used identically in Task 3 step 6 and matches `agents.py:103`. `RoutingResult` fields in Task 2 step 3 match the original `optimize` return.
- **Not removed (intentionally):** `get_leaderboard` tool, `BANKROLL_USD`, `jobs_solved`/`primary_provider` — these remain part of the qupick MCP surface.
