# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WOS Code Redeemer is a multi-component application for automatically redeeming gift codes from Whiteout Survival (WOS) game:
- **Backend API** (FastAPI): Admin dashboard and JSON API for managing alliances, users, and codes
- **Frontend SPA** (React + Vite + DaisyUI): Modern dashboard UI
- **Background workers**: RSS scraper (finds new codes) + redemption worker (attempts codes with AI captcha solving)
- **Database**: PostgreSQL (production) or SQLite (dev) with Alembic migrations

**Note**: [redeem.py](redeem.py) is a proof-of-concept CLI script, not used in the application. The actual redemption logic is in [worker.py](wos_redeem/worker.py), [tasks.py](wos_redeem/tasks.py), [api.py](wos_redeem/api.py), and the ONNX solver in [captcha_solver.py](wos_redeem/captcha_solver.py).

## Development Setup

**Python environment**: Always use the existing venv at `.venv/` (created with `uv`). Install/sync dependencies:
```bash
uv sync
```

**Database migrations**: Apply with Alembic:
```bash
uv run alembic upgrade head
```

**Frontend development**: From `frontend/` directory:
```bash
npm install          # install dependencies
npm run dev          # dev server (http://localhost:5173)
npm run build        # production build → ../static/ui/
```

**Running the application**:
- **API server only** (no workers): `uv run uvicorn wos_redeem.app:app --reload --port 8000`
- **Workers only**: `uv run python -m wos_redeem.worker`
- **Docker Compose** (full stack): `docker-compose up --build`

**Testing**:
```bash
uv run pytest           # run all tests
uv run pytest -v       # verbose output
uv run pytest tests/test_api.py  # single file
```

## Architecture & Code Structure

### Backend Package (`wos_redeem/`)
- [db.py](wos_redeem/db.py): SQLAlchemy models (Alliance, User, GiftCode, Redemption, RedemptionAttempt, WebAccount)
- [app.py](wos_redeem/app.py): FastAPI application with JSON API routes and SPA serving
- [api.py](wos_redeem/api.py): Low-level WOS API client functions (call_player, call_captcha, call_gift_code)
- [tasks.py](wos_redeem/tasks.py): Background worker loops (RSS scraper, redemption worker with throttling/backoff)
- [captcha_solver.py](wos_redeem/captcha_solver.py): Local ONNX CAPTCHA solver
- [auth.py](wos_redeem/auth.py): Password hashing and bootstrap admin utilities
- [cf_access.py](wos_redeem/cf_access.py): Cloudflare Access JWT verification
- [worker.py](wos_redeem/worker.py): Entry point to run workers standalone

### Key Concepts
- **Redemption workflow**: Worker fetches captcha → sends to OpenRouter vision model → submits code with solution → records result
- **Throttling**: `REDEEM_MAX_ATTEMPTS_PER_CYCLE` (default 2), `REDEEM_DELAY_S` (default 2s), `REDEEM_MIN_RETRY_MINUTES` (default 15), `REDEEM_MAX_ATTEMPTS_PER_PAIR` (default 3)
- **Worker Queue**: A thread-safe in-memory queue maintains upcoming (user, code) pairs; the API/SSE previews come from this real queue when available.
- **Auth**: Cloudflare Access JWT verification; inactive accounts auto-provisioned as managers; admins bootstrapped via env vars
- **SPA serving**: [app.py](wos_redeem/app.py) injects `<base href>` dynamically for Traefik prefix routing
- **Heartbeats**: Workers write `.rss_heartbeat`, `.worker_heartbeat`, `.worker_status` JSON to `STATUS_DIR` for monitoring

### Frontend (`frontend/`)
- React SPA built with Vite, TypeScript, Tailwind CSS, DaisyUI
- Routes: `/`, `/admin`, `/admin/alliances`, `/admin/users`, `/admin/codes`
- Build output → `static/ui/` (served by FastAPI)

### Database
- Models use SQLAlchemy 2.0 mapped classes with type hints
- Alembic migrations in `alembic/versions/`
- Connection string via `DATABASE_URL` env var (defaults to SQLite)

### Docker Deployment
- [docker-compose.yml](docker-compose.yml): 3-service stack (db, app, worker) with shared `/state` volume for heartbeats
- Traefik labels for reverse proxy with path prefix `/great-redeemer`
- Environment variables in `.env.local`

## Environment Variables

**Required**:
- `OPENROUTER_API_KEY`: For captcha solving (qwen/qwen2.5-vl-72b-instruct:free by default)
- `WOS_SECRET`: Century Game API signing secret (default: `tB87#kPtkxqOS2`)

**Optional**:
- `DATABASE_URL`: Connection string (default: `sqlite:///./wos.db`)
- `BASE_PATH`: FastAPI root_path for prefix routing (default: empty)
- `STATUS_DIR`: Directory for heartbeat files (default: `.`)
- `START_WORKERS`: Set to `1` to run workers inside API container (default: `0`)
- `REDEEM_MAX_ATTEMPTS_PER_CYCLE`: Max redemption attempts per worker loop (default: `2`)
- `REDEEM_DELAY_S`: Delay between attempts in seconds (default: `2`)
- `REDEEM_MIN_RETRY_MINUTES`: Backoff period before retrying (default: `15`)
- `REDEEM_MAX_ATTEMPTS_PER_PAIR`: Max attempts allowed per (user, code) pair before it is considered failed (default: `3`)
- `REDEEM_POLL_SECONDS`: Worker poll interval (default: `20`)
- `DISABLE_AUTH_ALL`: Disable all auth checks for local dev (default: `0`)
- `CLOUDFLARE_TEAM_DOMAIN`, `CLOUDFLARE_AUD`: For Cloudflare Access JWT verification

## Important Notes

- **Captcha failure logging**: Failed captcha attempts are saved to `failures/` directory with metadata (FID, guess, reason) for analysis
- **API signing**: Century Game API uses custom signature scheme (see [api.py:20-34](wos_redeem/api.py#L20-L34) for canonicalize/sign_payload logic)
- **Worker reconciliation**: Each cycle checks for redemptions with "RECEIVED" msg and marks them success
- **Conventional Commits**: Use `feat:`, `fix:`, `refactor:`, `test:`, `chore:` prefixes
- **Code style**: PEP 8, 4-space indentation, type hints preferred; format with `uvx black .` and lint with `uvx ruff check .`
