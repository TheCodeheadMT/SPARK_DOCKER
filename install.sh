#!/bin/sh
# SPARK installer for macOS and Linux (also works inside WSL on Windows).
#
#   curl -fsSL https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main/install.sh | sh
#
# Needs Docker (Docker Desktop, OrbStack, Rancher Desktop, or Docker Engine with the
# Compose plugin). Installs into ~/spark, starts SPARK and opens it in the browser.
# Running it again updates an existing install and keeps your data and settings.
#
# Options (environment variables, e.g. curl ... | SPARK_PORT=8080 sh):
#   SPARK_HOME     install folder                (default ~/spark)
#   SPARK_PORT     port for http://localhost:... (default: 8000, or the next free one)
#   SPARK_REF      version to run: main, a tag like v0.1.0-rc1, or a commit (default main)
#   SPARK_NO_OPEN  set to 1 to not open the browser
#
# The whole script is inside main(), so nothing runs if the download is cut short.

main() {
  set -eu
  RAW="${SPARK_RAW_BASE:-https://raw.githubusercontent.com/TheCodeheadMT/SPARK_DOCKER/main}"
  HOME_DIR="${SPARK_HOME:-$HOME/spark}"
  CONTAINER=spark

  if [ -t 1 ]; then B="$(printf '\033[1m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"; R="$(printf '\033[31m')"; N="$(printf '\033[0m')"
  else B=; G=; Y=; R=; N=; fi
  say()  { printf '%s\n' "${B}==>${N} $*"; }
  ok()   { printf '%s\n' "${G}✓${N} $*"; }
  warn() { printf '%s\n' "${Y}!${N} $*"; }
  die()  { printf '%s\n' "${R}✗ $*${N}" >&2; exit 1; }

  OS="$(uname -s)"
  case "$OS" in Darwin) OS=mac ;; Linux) OS=linux ;; *) die "Unsupported system $OS. On Windows use the PowerShell command in the README." ;; esac

  printf '\n%s\n\n' "${B}SPARK installer${N}"

  # --- 1. Docker ----------------------------------------------------------------------
  command -v curl >/dev/null 2>&1 || die "curl is required."
  if ! command -v docker >/dev/null 2>&1; then
    if [ "$OS" = mac ]; then
      die "Docker is not installed. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) or OrbStack (https://orbstack.dev), start it once, then run this command again."
    fi
    die "Docker is not installed. Install Docker Desktop (https://docs.docker.com/desktop/setup/install/linux/) or Docker Engine (https://docs.docker.com/engine/install/), then run this command again."
  fi

  if ! docker info >/dev/null 2>&1; then
    if docker info 2>&1 | grep -qi 'permission denied'; then
      die "Docker is installed but your user can't use it. Run: sudo usermod -aG docker \$USER, log out and back in, then run this command again."
    fi
    say "Starting Docker..."
    if [ "$OS" = mac ]; then
      open -a Docker >/dev/null 2>&1 || open -a OrbStack >/dev/null 2>&1 || open -a "Rancher Desktop" >/dev/null 2>&1 || true
    else
      systemctl --user start docker-desktop >/dev/null 2>&1 || true
    fi
    i=0
    until docker info >/dev/null 2>&1; do
      i=$((i + 1))
      [ "$i" -le 60 ] || die "Docker is not running. Start Docker Desktop (or run: sudo systemctl start docker), then run this command again."
      sleep 2
    done
  fi
  ok "Docker is running"

  if ! docker compose version >/dev/null 2>&1; then
    die "Docker Compose v2 is missing. Update Docker Desktop, or install the Compose plugin: https://docs.docker.com/compose/install/linux/"
  fi
  cv="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  major="${cv%%.*}"; rest="${cv#*.}"; minor="${rest%%.*}"
  case "$major$minor" in *[!0-9]*|'') ;; *)
    if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 17 ]; }; then
      die "Docker Compose $cv is too old (2.17 or newer needed). Update Docker."
    fi ;;
  esac
  ok "Docker Compose $cv"

  # --- 2. Files -----------------------------------------------------------------------
  mkdir -p "$HOME_DIR"
  cd "$HOME_DIR"
  say "Installing to $HOME_DIR"
  curl -fsSL "$RAW/deploy/local/docker-compose.yml" -o docker-compose.yml.new || die "Could not download SPARK. Check the internet connection."
  curl -fsSL "$RAW/deploy/local/.env.example" -o .env.example || die "Could not download SPARK. Check the internet connection."
  mv docker-compose.yml.new docker-compose.yml

  # Docker left over from an uninstalled Docker Desktop can point at a credential
  # helper that no longer exists, which breaks image downloads. Use a clean config.
  rm -rf .docker-config
  store="$(sed -n 's/.*"credsStore"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$HOME/.docker/config.json" 2>/dev/null | head -n 1)"
  if [ -n "$store" ] && ! command -v "docker-credential-$store" >/dev/null 2>&1; then
    warn "Docker is set to use a missing login helper (docker-credential-$store); SPARK uses its own Docker settings."
    ctx="$(docker context show 2>/dev/null || echo default)"
    mkdir -p .docker-config
    printf '{"currentContext": "%s"}\n' "$ctx" > .docker-config/config.json
    [ -d "$HOME/.docker/contexts" ] && ln -s "$HOME/.docker/contexts" .docker-config/contexts
    [ -d "$HOME/.docker/cli-plugins" ] && ln -s "$HOME/.docker/cli-plugins" .docker-config/cli-plugins
  fi
  [ -d .docker-config ] && export DOCKER_CONFIG="$HOME_DIR/.docker-config"

  # set_env KEY VALUE: set (or add) KEY in .env
  set_env() {
    awk -v k="$1" -v v="$2" 'BEGIN{d=0} $0 ~ "^[[:space:]]*#?[[:space:]]*"k"=" && !d {print k"="v; d=1; next} {print} END{if(!d) print k"="v}' .env > .env.tmp && mv .env.tmp .env
  }
  get_env() { sed -n "s/^$1=\([^ #]*\).*/\1/p" .env 2>/dev/null | head -n 1; }
  port_free() { curl -s -o /dev/null --connect-timeout 1 "http://127.0.0.1:$1/" ; [ $? -eq 7 ]; }

  running=false
  docker ps -q --filter "name=^/${CONTAINER}$" | grep -q . && running=true

  if [ ! -f .env ]; then
    cp .env.example .env
    port="${SPARK_PORT:-}"
    if [ -z "$port" ]; then
      port=8000
      until port_free "$port"; do
        port=$((port + 1))
        [ "$port" -le 8100 ] || die "No free port between 8000 and 8100. Set one with SPARK_PORT=..."
      done
    fi
    set_env SPARK_PORT "$port"
    ok "Settings created (.env), port $port"
  else
    [ -n "${SPARK_PORT:-}" ] && set_env SPARK_PORT "$SPARK_PORT"
    ok "Keeping your settings (.env)"
  fi
  [ -n "${SPARK_REF:-}" ] && set_env SPARK_REF "$SPARK_REF"
  port="$(get_env SPARK_PORT)"; port="${port:-8000}"
  if [ "$running" = false ] && ! port_free "$port"; then
    die "Port $port is in use by another program. Set SPARK_PORT=<port> in $HOME_DIR/.env (or rerun with SPARK_PORT=8080), then run this again."
  fi

  # --- 3. The 'spark' command -----------------------------------------------------------
  cat > spark <<EOF
#!/bin/sh
# SPARK control command (created by the installer). Run: spark help
cd "$HOME_DIR" || exit 1
[ -d .docker-config ] && export DOCKER_CONFIG="$HOME_DIR/.docker-config"
port="\$(sed -n 's/^SPARK_PORT=\([0-9]*\).*/\1/p' .env 2>/dev/null | head -n 1)"; url="http://localhost:\${port:-8000}"
case "\${1:-help}" in
  start)     docker compose up -d && echo "SPARK is starting: \$url (first start can take a few minutes; see: spark logs)" ;;
  stop)      docker compose down ;;
  restart)   docker compose restart && echo "Restarted (and updated): \$url" ;;
  update)    curl -fsSL "$RAW/install.sh" | SPARK_HOME="$HOME_DIR" sh ;;
  logs)      docker compose logs -f --tail 100 ;;
  status)    docker compose ps; echo "\$url" ;;
  open)      if command -v open >/dev/null 2>&1; then open "\$url"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "\$url"; else echo "\$url"; fi ;;
  uninstall) docker compose down -v --rmi all && rm -f "\$HOME/.local/bin/spark" && echo "SPARK removed. Your data is still in $HOME_DIR/spark-data; delete $HOME_DIR to remove everything." ;;
  *) echo "Usage: spark <command>
  start      start SPARK
  stop       stop SPARK
  restart    restart and update to the latest version
  update     re-run the installer (also updates this command)
  logs       follow the log (Ctrl+C to stop)
  status     show whether SPARK is running
  open       open SPARK in the browser
  uninstall  remove SPARK (keeps your data folder)" ;;
esac
EOF
  chmod +x spark
  cmd="$HOME_DIR/spark"
  case ":$PATH:" in *":$HOME/.local/bin:"*)
    mkdir -p "$HOME/.local/bin" && ln -sf "$HOME_DIR/spark" "$HOME/.local/bin/spark" && cmd=spark ;;
  esac

  # --- 4. Start -------------------------------------------------------------------------
  say "Starting SPARK (the first start downloads and builds everything: about 3-5 minutes)"
  docker compose up -d --build --force-recreate --quiet-pull >/dev/null 2>compose.log || {
    cat compose.log >&2; die "Docker could not start SPARK (see the messages above)."; }
  rm -f compose.log

  url="http://localhost:$port"
  shown=0
  t=0
  while :; do
    lines="$(docker logs "$CONTAINER" 2>&1 | grep '^\[spark\]' || true)"
    n="$(printf '%s\n' "$lines" | grep -c . || true)"
    if [ "$n" -gt "$shown" ]; then
      printf '%s\n' "$lines" | tail -n "$((n - shown))" | sed 's/^\[spark\]/   /'
      shown=$n
    fi
    if curl -fs -o /dev/null "$url/api/health"; then break; fi
    state="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo missing)"
    case "$state" in
      running*|created*) ;;
      *) docker logs --tail 30 "$CONTAINER" >&2 2>&1 || true; die "SPARK stopped while starting (see the log above)." ;;
    esac
    t=$((t + 3))
    [ "$t" -le 1200 ] || die "SPARK is taking unusually long to start. Check: $cmd logs"
    sleep 3
  done

  ok "SPARK is running"
  if [ "${SPARK_NO_OPEN:-0}" != 1 ]; then
    if [ "$OS" = mac ]; then open "$url" >/dev/null 2>&1 || true
    elif command -v wslview >/dev/null 2>&1; then wslview "$url" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then xdg-open "$url" >/dev/null 2>&1 || true
    fi
  fi

  cat <<EOF

${B}SPARK is ready:${N} $url

  Your data:   $HOME_DIR/spark-data   (back up this folder)
  Settings:    $HOME_DIR/.env

  $cmd stop        stop SPARK
  $cmd start       start it again (it also starts with Docker)
  $cmd restart     restart and update to the latest version
  $cmd help        all commands

EOF
}

main "$@"
