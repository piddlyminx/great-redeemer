# Repository Guidelines

## Project Structure & Module Organization
- Root-level script: `redeem.py` (CLI for redeeming gift codes).
- Runtime artifact: `captcha.jpg` written to the repo root (ephemeral).
- Suggested layout for new code: put helpers in `wos_redeem/` and tests in `tests/`.

## Build, Test, and Development Commands
- Use `uv` for execution and dependency management (no venv setup needed).
- Install or sync deps: `uv sync` (optional; creates `.venv/`).
- Run locally (always via uv):
  - `uv run python redeem.py --fid 571970746 --cdk GAECHEONJEOL [--verbose]`
- Useful tooling (optional):
  - `uvx black`, `uvx ruff`, `uvx pytest`
 - Lockfile: commit `uv.lock`; update with `uv lock` (or `uv lock --upgrade` to bump versions).

## Coding Style & Naming Conventions
- Use 4‑space indentation, `utf-8`, and Unix newlines.
- Python style: PEP 8 with type hints where practical.
- Naming: functions/vars `snake_case`, constants `UPPER_SNAKE_CASE` (e.g., `SECRET`).
- Formatting/linting:
  - `black .` (line length 88)
  - `ruff check .` (prefer fixing warnings before PR)
- Keep functions pure where possible and add short docstrings for public helpers.

## Testing Guidelines
- Framework: `pytest`.
- Location and names: `tests/` with files named `test_*.py`.
- Run: `pytest -q`.
- For HTTP calls, mock `requests.post` (e.g., `pytest-mock`) or use `responses`/`respx` to avoid real API traffic.
- Aim for coverage of signing, canonicalization, and error paths.

## Commit & Pull Request Guidelines
- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Keep messages imperative and focused: “fix sign payload ordering”.
- PRs should include: summary, rationale, before/after behavior, and test notes. Link any related issue.
- Small, reviewable changes are preferred over large mixed PRs.

## Security & Configuration Tips
- Do not commit real account IDs, codes, or API responses.
- Treat secrets as env vars (e.g., `WOS_SECRET`) rather than hardcoding; avoid printing them.
- Add transient files like `captcha.jpg` to `.gitignore`.
- Set sensible timeouts for network calls and handle non‑JSON responses defensively.

## Execute‑Validate Debugging (Strong Recommendation)

When diagnosing issues, prefer running small, targeted checks and validating real outputs over speculation. This repo has moving parts (worker queue, DB state, status files, SSE), and concrete signals beat guesses.

Principles
- Start from observed symptoms, then form the minimal hypothesis you can test immediately.
- Execute a quick probe; capture the exact output (timestamps, counts, errors).
- Update your hypothesis based on results; keep iterating until the cause is reproduced or ruled out.
- Favor reading the system’s own signals (logs, status files, DB queries) over mental simulation.

Suggested Loop
1) Observe: copy the exact log line or UI state you see.
2) Probe: run a focused command (one liner) to measure the suspected component.
3) Compare: does output support the hypothesis? If not, pivot quickly.
4) Fix small; re‑run the same probe to confirm.

Worker Debug Playbook
- Container health and logs
  - `docker compose ps`
  - `docker compose logs --since=5m worker`
- Status files (written by the worker)
  - `docker compose cp worker:/state/.worker_status - | tail -n +1`
  - `docker compose cp worker:/state/.worker_heartbeat - | tail -n +1`
- Live in‑container probes (run via uv):
  - Eligible backlog: `docker compose exec worker sh -lc "uv run python -c 'from wos_redeem.tasks import SessionLocal,eligible_count; s=SessionLocal(); print(eligible_count(s))'"`
  - In‑memory queue length: `docker compose exec worker sh -lc "uv run python -c 'from wos_redeem.queueing import worker_state; print(len(worker_state.queue))'"`
  - Worker log file (structured exceptions): `docker compose exec worker sh -lc 'tail -n 200 /logs/worker.log'`

Common Gotchas (examples from this project)
- Naive vs aware datetimes: comparing a naive DB timestamp to an aware UTC `now()` raises `TypeError`. Normalize timestamps to UTC before comparisons.
- Queue snapshot vs runtime: `.worker_status` may be stale; check `worker_state.queue` inside the container for truth.
- Environment drift: ensure critical env (e.g., `OPENROUTER_API_KEY`) is present in the target container, not just locally.

When to speculate
- Only after exhausting cheap probes or when access is impossible. Document assumptions and the quickest next probe to confirm or falsify them.

Why this matters
- Short feedback loops prevent chasing the wrong cause. In this repo, executing the probes surfaced a timezone bug immediately, while a speculative fix would have missed it.
