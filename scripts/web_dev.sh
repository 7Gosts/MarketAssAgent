#!/usr/bin/env bash
# bash scripts/web_dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${WEB_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-8000}"
DISABLE_PROXY_ON_START="${DISABLE_PROXY_ON_START:-1}"

if [[ "$DISABLE_PROXY_ON_START" == "1" ]]; then
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
  unset http_proxy https_proxy all_proxy
  echo "Starting MarketAssAgent Web without proxy env"
fi

echo "Starting MarketAssAgent Web on http://${HOST}:${PORT}/chat"

exec python3 - <<PY
import uvicorn

from utils.logging_utils import get_logger, get_uvicorn_log_config

logger = get_logger(__name__)

logger.info("MarketAssAgent Web 启动中")
uvicorn.run(
    "cli.api_server:app",
    host="${HOST}",
    port=int("${PORT}"),
    reload=True,
    log_config=get_uvicorn_log_config(),
)
PY
