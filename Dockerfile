# syntax=docker/dockerfile:1

# Use uv base image with Python preinstalled for fast, reproducible installs
FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS base

WORKDIR /app

# Copy project metadata first to leverage Docker layer caching
COPY . ./

# Install dependencies (and this project) into a project-local venv
RUN uv sync --frozen --no-dev

# Build UI (if Node is available). Use a Node stage for reproducible builds.
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS final
WORKDIR /app
COPY --from=base /app /app
COPY --from=ui /ui/dist /app/static/ui

# Bring a Node.js runtime into the final image (for Codex CLI)
FROM node:20-bookworm-slim AS nodebin

FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS final-with-node
WORKDIR /app
COPY --from=base /app /app
COPY --from=ui /ui/dist /app/static/ui
# Copy Node runtime + global modules and bin shims
COPY --from=nodebin /usr/local /usr/local
# Install Codex CLI globally (provides /usr/local/bin/codex)
RUN npm i -g @openai/codex && \
    codex --version
## Bake a minimal Codex config that trusts /app inside the container
RUN mkdir -p /root/.codex
COPY codex/config.toml /root/.codex/config.toml

# Expose port used by uvicorn
EXPOSE 8000

# Default environment
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    DATABASE_URL=sqlite:///./wos.db

# Default command runs DB migrations then the API server
CMD ["sh", "-lc", "uv run alembic upgrade head && uv run --frozen uvicorn wos_redeem.app:app --host 0.0.0.0 --port 8000 --proxy-headers"]
