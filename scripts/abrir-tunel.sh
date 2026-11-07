#!/usr/bin/env bash
# Start the game with Docker Compose and publish it through a Cloudflare quick tunnel.
#
# Usage:
#   scripts/abrir-tunel.sh            start the game; reuse the tunnel while it is healthy
#   scripts/abrir-tunel.sh nuevo      force a new tunnel (the public URL changes)
#   scripts/abrir-tunel.sh parar      close the tunnel (the containers keep running)
#   scripts/abrir-tunel.sh instalar   download cloudflared into ~/.local/bin (no sudo)
#
# Requirements: WSL or Linux, Docker with the Compose plugin, curl, and a .env file
# (copy .env.example and fill it in). cloudflared is found in PATH or ~/.local/bin.
#
# Quick tunnels are temporary: the URL is new every time and Cloudflare deletes the
# tunnel after about two days. Open one shortly before it is needed.
#
# The log and the pid of the tunnel live in ${XDG_STATE_HOME:-~/.local/state}.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}"
LOG="$STATE/cloudflared.log"
PIDFILE="$STATE/cloudflared.pid"
BIN_DIR="$HOME/.local/bin"
URL_RE='https://[a-z0-9-]+\.trycloudflare\.com'

usage() {
  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

cloudflared_bin() {
  if command -v cloudflared > /dev/null 2>&1; then
    command -v cloudflared
  elif [ -x "$BIN_DIR/cloudflared" ]; then
    echo "$BIN_DIR/cloudflared"
  fi
}

install_cloudflared() {
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) echo "ERROR: unsupported architecture $(uname -m)"; return 1 ;;
  esac
  mkdir -p "$BIN_DIR"
  echo "Downloading cloudflared ($arch) into $BIN_DIR ..."
  curl -fsSL -o "$BIN_DIR/cloudflared" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$arch" || {
    echo "ERROR: the download failed"
    return 1
  }
  chmod +x "$BIN_DIR/cloudflared"
  "$BIN_DIR/cloudflared" --version | head -1
}

proxy_port() {
  local port
  port=$(grep -E '^PROXY_PORT=' "$ROOT/.env" 2> /dev/null | tail -1 | cut -d= -f2 | tr -d "\r\"' ")
  echo "${port:-8080}"
}

# -a: a log that an older cloudflared kept writing to can contain NUL bytes.
current_url() { grep -aoE "$URL_RE" "$LOG" 2> /dev/null | head -1; }

healthy() {
  [ -n "${1:-}" ] && [ "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$1/healthz/")" = "200" ]
}

# The pid recorded for the tunnel this script opened, only if it is still a cloudflared.
tunnel_pid() {
  local pid
  pid=$(cat "$PIDFILE" 2> /dev/null)
  if [ -n "$pid" ] && [ "$(ps -p "$pid" -o comm= 2> /dev/null)" = "cloudflared" ]; then
    echo "$pid"
  fi
}

stop_tunnel() {
  local pid
  pid=$(tunnel_pid)
  if [ -n "$pid" ]; then
    kill "$pid" 2> /dev/null
    # Wait for it to exit so it cannot keep writing into the next tunnel's log.
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2> /dev/null || break
      sleep 0.5
    done
    rm -f "$PIDFILE" "$LOG"
    return 0
  fi
  return 1
}

case "${1:-}" in
  "" | nuevo) ;;
  parar)
    if stop_tunnel; then
      echo "Tunnel closed. The previous public URL no longer works."
    else
      echo "No tunnel opened by this script is running."
    fi
    exit 0
    ;;
  instalar)
    install_cloudflared
    exit $?
    ;;
  *)
    usage
    exit 2
    ;;
esac

CF="$(cloudflared_bin)"
[ -n "$CF" ] || { echo "ERROR: cloudflared not found. Run: scripts/abrir-tunel.sh instalar"; exit 1; }
docker info > /dev/null 2>&1 || { echo "ERROR: Docker is not running (try: sudo systemctl start docker)"; exit 1; }
[ -f "$ROOT/.env" ] || { echo "ERROR: $ROOT/.env is missing. Run: cp .env.example .env and fill in the values"; exit 1; }
cd "$ROOT" || exit 1
PORT="$(proxy_port)"

echo "1/3 Starting the game..."
DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1,.trycloudflare.com" \
DJANGO_CSRF_TRUSTED_ORIGINS="http://localhost:${PORT},https://*.trycloudflare.com" \
DJANGO_SECURE_COOKIES=1 \
  docker compose up -d --build --wait > /dev/null 2>&1 || {
  echo "ERROR: the game did not start. Look at: docker compose logs web"
  exit 1
}
code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://localhost:${PORT}/healthz/")
[ "$code" = "200" ] || { echo "ERROR: the game answers $code on http://localhost:${PORT}"; exit 1; }
echo "    local game OK (http://localhost:${PORT})"

echo "2/3 Checking the tunnel..."
mkdir -p "$STATE"
if [ -z "$(tunnel_pid)" ] && healthy "$(current_url)"; then
  # A healthy tunnel opened outside this script: remember its pid so "nuevo" and "parar" can close it.
  pgrep -n -x cloudflared > "$PIDFILE" 2> /dev/null || true
fi
if [ "${1:-}" != "nuevo" ] && healthy "$(current_url)"; then
  echo "    a healthy tunnel is already open, reusing it"
  echo
  echo "PUBLIC URL: $(current_url)"
  exit 0
fi

stop_tunnel || true
setsid nohup "$CF" tunnel --no-autoupdate --url "http://localhost:${PORT}" > "$LOG" 2>&1 < /dev/null &

echo "3/3 Opening a new tunnel..."
url=""
for _ in $(seq 1 30); do
  url=$(current_url)
  [ -n "$url" ] && break
  sleep 1
done
[ -n "$url" ] || { echo "ERROR: cloudflared gave no URL. Look at: tail $LOG"; exit 1; }
pgrep -n -x cloudflared > "$PIDFILE" 2> /dev/null || true

for _ in $(seq 1 15); do
  healthy "$url" && break
  sleep 3
done
echo
if healthy "$url"; then
  echo "PUBLIC URL: $url"
else
  echo "WARNING: the tunnel is open but does not answer yet. Wait a few seconds and try: $url"
  exit 3
fi
