#!/usr/bin/env bash
# MarketAssAgent 本地开发统一启动脚本
#
# 用法:
#   bash scripts/dev.sh                 # 默认：API + 飞书机器人
#   bash scripts/dev.sh --api-only      # 仅启动 API
#   bash scripts/dev.sh --feishu        # 仅启动飞书机器人（需先有 API）
#   bash scripts/dev.sh --cli           # 交互式 CLI（不启动服务）
#   bash scripts/dev.sh diagnose        # 配置/连通性诊断
#
# 环境变量:
#   DEV_API_HOST / DEV_API_PORT         API 地址，默认 127.0.0.1:8000
#   DEV_AUTO_START_POSTGRES             默认 1
#   AGENT_PIPELINE_LOG                  默认 1，打印 Agent 流水线日志
#   UVICORN_EXTRA                       传给 uvicorn 的额外参数
#   DEV_START_FEISHU                    0/1 强制开关

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="dev"
API_HOST="${DEV_API_HOST:-127.0.0.1}"
API_PORT="${DEV_API_PORT:-8000}"
API_BASE="http://${API_HOST}:${API_PORT}"
AUTO_START_POSTGRES="${DEV_AUTO_START_POSTGRES:-1}"

export AGENT_PIPELINE_LOG="${AGENT_PIPELINE_LOG:-1}"

MODE="${DEV_MODE:-all}"
START_FEISHU="${DEV_START_FEISHU:-}"
EXPLICIT_MODE=0

if [[ "${1:-}" == "diagnose" ]]; then
  MODE="diagnose"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      EXPLICIT_MODE=1
      START_FEISHU=1
      ;;
    --feishu)
      EXPLICIT_MODE=1
      START_FEISHU=1
      ;;
    --api-only)
      EXPLICIT_MODE=1
      START_FEISHU=0
      ;;
    --cli)
      MODE="cli"
      shift
      ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "[${TAG}] 未知参数: $1（可用 --api-only / --feishu / --cli / diagnose）" >&2
      exit 1
      ;;
  esac
  shift
done

if [[ "$MODE" != "diagnose" && "$MODE" != "cli" ]]; then
  if [[ "$EXPLICIT_MODE" == "0" && -z "$START_FEISHU" ]]; then
    START_FEISHU=1
  elif [[ -z "$START_FEISHU" ]]; then
    START_FEISHU=0
  fi
fi

PIDS=()

log() {
  printf '[%s] %s\n' "$TAG" "$*"
}

resolve_bin() {
  local venv_bin="$1"
  local fallback_bin="$2"
  if [[ -x "$REPO_ROOT/.venv/bin/$venv_bin" ]]; then
    printf '%s\n' "$REPO_ROOT/.venv/bin/$venv_bin"
    return 0
  fi
  command -v "$fallback_bin" 2>/dev/null || true
}

require_bin() {
  local value="$1"
  local label="$2"
  if [[ -z "$value" ]]; then
    log "错误: 未找到 ${label}。请先安装依赖或激活 .venv。"
    exit 1
  fi
}

http_code() {
  local url="$1"
  curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 "$url" 2>/dev/null || echo '000'
}

port_listen_detail() {
  local port="$1"
  ss -tlnp 2>/dev/null | grep ":${port} " || netstat -tlnp 2>/dev/null | grep ":${port} " || true
}

start_prefixed() {
  local prefix="$1"
  shift
  "$@" > >(sed -u "s/^/[${prefix}] /") 2>&1 &
  PIDS+=("$!")
}

stop_all() {
  local pid
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      log "停止 pid=${pid}"
      kill "$pid" 2>/dev/null || true
    fi
  done
  pkill -P "$$" 2>/dev/null || true
  pkill -f '[u]vicorn app.api_server:app' 2>/dev/null || true
  pkill -f '[p]ython3 cli/feishu_bot.py' 2>/dev/null || true
  pkill -f '[p]ython cli/feishu_bot.py' 2>/dev/null || true
}

cleanup() {
  stop_all
}
trap cleanup EXIT INT TERM

ensure_postgres_ready() {
  if ! command -v pg_isready >/dev/null 2>&1; then
    log "未找到 pg_isready，跳过 PostgreSQL 就绪检查。"
    return 0
  fi
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$AUTO_START_POSTGRES" != "1" ]]; then
    log "PostgreSQL 未就绪，且 DEV_AUTO_START_POSTGRES=$AUTO_START_POSTGRES，不自动启动。"
    return 1
  fi
  log "PostgreSQL 未启动，尝试 sudo service postgresql start ..."
  if ! sudo service postgresql start; then
    log "错误: PostgreSQL 启动失败。"
    return 1
  fi
  pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1
}

wait_for_health() {
  local base_url="$1"
  local ok=0
  for _ in $(seq 1 40); do
    if [[ "$(http_code "${base_url}/health")" == "200" ]]; then
      ok=1
      break
    fi
    sleep 0.25
  done
  [[ "$ok" == 1 ]]
}

print_runtime_summary() {
  echo
  log "========== 运行时摘要 =========="
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      log "PostgreSQL: 127.0.0.1:5432 就绪"
    else
      log "PostgreSQL: 127.0.0.1:5432 未就绪"
    fi
  fi
  # shellcheck disable=SC2090
  "$PYTHON_BIN" - <<PY
import os, sys
from pathlib import Path
repo = Path("${REPO_ROOT}")
os.chdir(repo)
sys.path.insert(0, str(repo))
from config.runtime_config import get_llm_runtime_settings, get_postgres_dsn, reload_accounts_config
reload_accounts_config()
s = get_llm_runtime_settings()
print(f"[${TAG}] LLM: provider={s.get('provider')!s} model={s.get('model')!s} base_url={s.get('base_url')!s}")
has_dsn = bool(str(get_postgres_dsn() or "").strip())
print(f"[${TAG}] PostgreSQL YAML: {'已配置 dsn' if has_dsn else 'dsn 未配置'}")
print(f"[${TAG}] AGENT_PIPELINE_LOG={os.environ.get('AGENT_PIPELINE_LOG')!r}")
print(f"[${TAG}] ================================")
PY
}

diagnose() {
  local wsl_ip
  wsl_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  print_runtime_summary
  echo
  log "========== 连通性诊断 $(date '+%F %T') =========="
  log "WSL IP=${wsl_ip:-未知}"

  if port_listen_detail "$API_PORT" | grep -q .; then
    log "✅ API :${API_PORT}"
  else
    log "❌ API :${API_PORT} 未监听"
  fi

  local code
  code="$(http_code "${API_BASE}/health")"
  log "curl ${API_BASE}/health → HTTP ${code}"

  if [[ -n "${wsl_ip:-}" ]]; then
    code="$(http_code "http://${wsl_ip}:${API_PORT}/health")"
    log "curl http://${wsl_ip}:${API_PORT}/health → HTTP ${code}"
  fi

  log "浏览器/调用示例: curl -X POST ${API_BASE}/agent/run -H 'Content-Type: application/json' -d '{\"text\":\"BTC_USDT 行情分析\"}'"
  log "==============================================="
  echo
}

PYTHON_BIN="$(resolve_bin python python3)"
UVICORN_BIN="$(resolve_bin uvicorn uvicorn)"

if [[ "$MODE" == "diagnose" ]]; then
  require_bin "$PYTHON_BIN" "python"
  diagnose
  exit 0
fi

if [[ "$MODE" == "cli" ]]; then
  require_bin "$PYTHON_BIN" "python"
  log "启动交互式 CLI（cli/run.py）..."
  exec "$PYTHON_BIN" cli/run.py
fi

require_bin "$PYTHON_BIN" "python"
require_bin "$UVICORN_BIN" "uvicorn"

ensure_postgres_ready
print_runtime_summary

if [[ "$START_FEISHU" == "1" ]]; then
  log "启动模式: API + 飞书机器人"
else
  log "启动模式: 仅 API"
fi

log "停止旧进程（uvicorn / feishu_bot）…"
stop_all
PIDS=()
sleep 1

log "启动 uvicorn → ${API_BASE}（日志前缀 [api]，流水线 AGENT_PIPELINE_LOG=${AGENT_PIPELINE_LOG}）"
# shellcheck disable=SC2086
start_prefixed "api" "$UVICORN_BIN" app.api_server:app --host "$API_HOST" --port "$API_PORT" ${UVICORN_EXTRA:-}

if ! wait_for_health "$API_BASE"; then
  log "错误: ${API_BASE}/health 在约 10s 内未就绪。"
  exit 1
fi
log "✅ API /health 就绪"

if [[ "$START_FEISHU" == "1" ]]; then
  log "启动飞书机器人（日志前缀 [feishu]）"
  start_prefixed "feishu" "$PYTHON_BIN" cli/feishu_bot.py
fi

diagnose

log "========== 实时日志（Ctrl+C 结束全部）=========="
log "API 调用示例: curl -X POST ${API_BASE}/agent/run ..."
log "飞书消息会自动走 [feishu] → [AgentCore] pipeline"
log "仅 API: bash scripts/dev.sh --api-only"
log "再次诊断: bash scripts/dev.sh diagnose"
log "交互式 CLI: bash scripts/dev.sh --cli"
log "================================================"

wait
