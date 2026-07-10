#!/usr/bin/env bash
# bash scripts/web_dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${WEB_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-8000}"
DISABLE_PROXY_ON_START="${DISABLE_PROXY_ON_START:-1}"
PIDFILE="${ROOT}/.web_dev.pid"

get_port_pid() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true
  else
    ss -ltnp 2>/dev/null | awk -v port=":${PORT}" '$4 ~ port { match($0, /pid=([0-9]+)/, m); if (m[1]) print m[1] }'
  fi
}

cleanup_old_server() {
  if [[ -f "$PIDFILE" ]]; then
    old_pid="$(<"$PIDFILE")"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "Stopping previous MarketAssAgent Web process $old_pid"
      kill "$old_pid" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$PIDFILE"
  fi

  for pid in $(get_port_pid); do
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping existing process on port ${PORT}: $pid"
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
  done
}

cleanup_old_server

if [[ "$DISABLE_PROXY_ON_START" == "1" ]]; then
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
  unset http_proxy https_proxy all_proxy
  echo "Starting MarketAssAgent Web without proxy env"
fi

echo "Starting MarketAssAgent Web on http://${HOST}:${PORT}/chat"

export WEB_HOST="${HOST}"
export WEB_PORT="${PORT}"
export PYTHONPATH="${ROOT}/runtime:${ROOT}/src:${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
printf '%s' "$$" > "$PIDFILE"
exec python3 -m uvicorn cli.api_server:app --host "$HOST" --port "$PORT" --reload
