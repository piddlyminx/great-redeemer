#!/usr/bin/env bash
# Simple deploy helper for Oracle Cloud VM
# - SSH to ubuntu@oracle-cloud
# - Pull changes in ~/great-redeemer
# - Run DB migrations
# - Rebuild and restart docker compose stack

set -euo pipefail

# Config (override with env or flags)
HOST=${HOST:-ubuntu@oracle-cloud}
# Leave APP_DIR empty by default; set default on remote to $HOME/great-redeemer
APP_DIR=${APP_DIR:-}
BRANCH=${BRANCH:-}
COMPOSE_CMD=${COMPOSE_CMD:-"docker compose"}

usage() {
  echo "Usage: $0 [-h host] [-d app_dir] [-b branch]" >&2
  echo "Defaults: host=ubuntu@oracle-cloud, app_dir=~/great-redeemer, branch=(current)" >&2
}

while getopts ":h:d:b:" opt; do
  case $opt in
    h) HOST=$OPTARG ;;
    d) APP_DIR=$OPTARG ;;
    b) BRANCH=$OPTARG ;;
    :) echo "Option -$OPTARG requires an argument" >&2; usage; exit 2 ;;
    \?) usage; exit 2 ;;
  esac
done

echo "Deploying to $HOST (dir: ${APP_DIR:-\$HOME/great-redeemer})" >&2

ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$HOST" \
  "APP_DIR='$APP_DIR' BRANCH='$BRANCH' COMPOSE_CMD='$COMPOSE_CMD' bash -lc 'set -euo pipefail
# Defaults on remote
COMPOSE_CMD=\"${COMPOSE_CMD:-docker compose}\"
APP_DIR=\"${APP_DIR:-$HOME/great-redeemer}\"
# If that path does not exist, try a common ubuntu home
if [ ! -d \"$APP_DIR\" ] && [ -d /home/ubuntu/great-redeemer ]; then
  APP_DIR=/home/ubuntu/great-redeemer
fi
echo \"[remote] Host: \$(hostname)\"
echo \"[remote] Using directory: \$APP_DIR\"
cd \"\$APP_DIR\"

echo \"[remote] Git fetch/pull...\"
git fetch --all --prune
if [ -n \"\$BRANCH\" ]; then
  git checkout \"\$BRANCH\"
fi
if git rev-parse --abbrev-ref --symbolic-full-name @{u} >/dev/null 2>&1; then
  git pull --rebase --autostash
else
  echo \"[remote] No upstream configured; pulling origin/main\"
  git pull --rebase --autostash origin main || true
fi

echo \"[remote] Building images...\"
\$COMPOSE_CMD build --pull

echo \"[remote] Ensuring db is up...\"
\$COMPOSE_CMD up -d db
DB_ID=\$(\$COMPOSE_CMD ps -q db || true)
if [ -n \"\$DB_ID\" ]; then
  echo \"[remote] Waiting for db (healthcheck)...\"
  for i in \$(seq 1 60); do
    status=\$(docker inspect -f '{{.State.Health.Status}}' \"\$DB_ID\" 2>/dev/null || echo none)
    if [ \"\$status\" = \"healthy\" ]; then
      echo \"[remote] db is healthy\"
      break
    fi
    sleep 2
  done
fi

echo \"[remote] Running migrations...\"
\$COMPOSE_CMD run --rm app uv run alembic upgrade head

echo \"[remote] Recreating stack...\"
\$COMPOSE_CMD up -d --build --remove-orphans

echo \"[remote] Services:\"
\$COMPOSE_CMD ps'"

echo "Done."
