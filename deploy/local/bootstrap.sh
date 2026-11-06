#!/bin/sh
# Pull the latest SPARK code, then hand over to the repository's start script.
set -u
SRC=/opt/spark/src
URL="${SPARK_REPO_URL:-https://github.com/TheCodeheadMT/SPARK_DOCKER.git}"
REF="${SPARK_REF:-main}"
# Only needed if SPARK_REPO_URL points at a private repository: a read-only token. It is sent as a header and
# never written to disk.
if [ -n "${GIT_TOKEN:-}" ]; then
  AUTH=$(printf 'x-access-token:%s' "$GIT_TOKEN" | base64 -w0)
  set -- -c "http.extraHeader=Authorization: Basic $AUTH"
else
  set --
fi
mkdir -p "$SRC"
git config --global --add safe.directory "$SRC"
[ -d "$SRC/.git" ] || git -C "$SRC" init -q
git -C "$SRC" remote remove origin 2>/dev/null
git -C "$SRC" remote add origin "$URL"
echo "[spark] checking $URL ($REF) for updates"
if git "$@" -C "$SRC" fetch -q --depth 1 origin "$REF" \
    && git -C "$SRC" reset -q --hard FETCH_HEAD; then
  echo "[spark] up to date: $(git -C "$SRC" log -1 --format='%h %s')"
elif [ -f "$SRC/deploy/local/start.sh" ]; then
  echo "[spark] WARNING: could not update (offline, or no access to the repository); starting the last version pulled"
else
  echo "[spark] ERROR: could not download SPARK from $URL ($REF)." >&2
  echo "[spark] Check the internet connection and SPARK_REF." >&2
  sleep 30
  exit 1
fi
exec bash "$SRC/deploy/local/start.sh"
