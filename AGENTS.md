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
