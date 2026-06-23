#!/usr/bin/env bash 
# bash scripts/feishu_dev.sh   MARKET_AGENT_LOG_LEVEL=INFO PYTHONUNBUFFERED=1 bash scripts/feishu_dev.sh 2>&1 | tee /tmp/feishu.log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DISABLE_PROXY_ON_START="${DISABLE_PROXY_ON_START:-1}"

if [[ "$DISABLE_PROXY_ON_START" == "1" ]]; then
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
  unset http_proxy https_proxy all_proxy
  echo "Starting Feishu bot without proxy env"
fi

exec python3 "$ROOT/cli/feishu_bot.py" "$@"
