#!/usr/bin/env bash
# SPARK local release: prepare and start the app inside the container.
#
# Run by the bootstrap in deploy/local/docker-compose.yml after it has pulled the
# latest code into $SPARK_SRC. This script is part of the repository, so it updates
# with the code it starts. On each start it:
#   1. reinstalls Python packages when backend/requirements.txt changed,
#   2. rebuilds the React app when anything under frontend/ changed,
#   3. creates a SECRET_KEY once (kept in the data folder),
#   4. starts the API, which also serves the built app.
# A failed step keeps the previous working build when there is one.
set -uo pipefail

SRC="${SPARK_SRC:-/opt/spark/src}"
CACHE="${SPARK_CACHE:-/opt/spark/cache}"
DATA="${DATA_DIR:-/data}"
VENV="$CACHE/venv"
DIST="$SRC/frontend/dist"

log() { echo "[spark] $*"; }
die() { echo "[spark] ERROR: $*" >&2; exit 1; }

mkdir -p "$CACHE" "$DATA"
cd "$SRC" || die "source folder $SRC is missing"
log "code version: $(git log -1 --format='%h %s (%cd)' --date=short 2>/dev/null || echo unknown)"

# 1. Python packages ---------------------------------------------------------------
req_hash="$(sha256sum backend/requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$CACHE/requirements.sha" 2>/dev/null)" != "$req_hash" ] || [ ! -x "$VENV/bin/uvicorn" ]; then
  log "installing Python packages (first start or requirements changed; this can take a few minutes)"
  [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV" || die "could not create the Python environment"
  if "$VENV/bin/pip" install --disable-pip-version-check --no-cache-dir -q -r backend/requirements.txt; then
    echo "$req_hash" > "$CACHE/requirements.sha"
  elif [ -x "$VENV/bin/uvicorn" ]; then
    log "WARNING: package install failed; starting with the packages already installed"
  else
    die "package install failed and there is no earlier install to fall back on (check the internet connection)"
  fi
fi

# 2. Frontend build ----------------------------------------------------------------
# git's tree hash of frontend/ changes whenever any file in it changes.
fe_hash="$(git rev-parse HEAD:frontend 2>/dev/null || echo none)"
if [ "$(cat "$CACHE/frontend.sha" 2>/dev/null)" != "$fe_hash" ] || [ ! -f "$DIST/index.html" ]; then
  log "building the web app (first start or frontend changed)"
  # node_modules lives in the cache volume so updates don't download everything again
  mkdir -p "$CACHE/node_modules"
  ln -sfn "$CACHE/node_modules" frontend/node_modules
  if (cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent); then
    echo "$fe_hash" > "$CACHE/frontend.sha"
  elif [ -f "$DIST/index.html" ]; then
    log "WARNING: web app build failed; serving the previous build"
  else
    die "web app build failed and there is no earlier build to fall back on"
  fi
fi

# 3. Session secret ----------------------------------------------------------------
if [ -z "${SECRET_KEY:-}" ] || [ "$SECRET_KEY" = "dev-only-change-me" ]; then
  if [ ! -s "$DATA/.secret_key" ]; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > "$DATA/.secret_key"
    chmod 600 "$DATA/.secret_key"
  fi
  SECRET_KEY="$(cat "$DATA/.secret_key")"
  export SECRET_KEY
fi

# 4. Start -------------------------------------------------------------------------
export DATA_DIR="$DATA" FRONTEND_DIST="$DIST" PYTHONPATH="$SRC/backend" PYTHONUNBUFFERED=1
cd "$SRC/backend"
log "starting SPARK: open http://localhost:${SPARK_PORT:-8000}"
exec "$VENV/bin/uvicorn" spark_api.main:app --host 0.0.0.0 --port 8000
