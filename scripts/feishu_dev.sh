#!/usr/bin/env bash
# bash scripts/feishu_dev.sh
# MARKET_AGENT_LOG_LEVEL=INFO PYTHONUNBUFFERED=1 bash scripts/feishu_dev.sh 2>&1 | tee /tmp/feishu.log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DISABLE_PROXY_ON_START="${DISABLE_PROXY_ON_START:-1}"

if [[ "$DISABLE_PROXY_ON_START" == "1" ]]; then
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
  unset http_proxy https_proxy all_proxy
  echo "Starting Feishu bot without proxy env"
fi

export PYTHONPATH="${ROOT}/runtime:${ROOT}/src:${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# 启动 Feishu bot 前先检查数据库连通性；本机 PostgreSQL 未启动时尝试拉起。
DB_WAIT_TIMEOUT_SEC="${DB_WAIT_TIMEOUT_SEC:-30}"
DB_WAIT_INTERVAL_SEC="${DB_WAIT_INTERVAL_SEC:-1}"

read -r DB_CONFIGURED DB_HOST DB_PORT DB_NAME <<<"$(python3 - <<'PY'
from config.runtime_config import get_postgres_dsn
from sqlalchemy.engine import make_url

dsn = get_postgres_dsn()
if not dsn:
    print("0 - - -")
else:
    url = make_url(dsn)
    host = str(url.host or "")
    port = str(url.port or "")
    name = str(url.database or "")
    print(f"1 {host or '-'} {port or '-'} {name or '-'}")
PY
)"

check_db_ready() {
  python3 - <<'PY'
import sys

from sqlalchemy import text

from infrastructure.persistence.db import get_engine

try:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("select 1"))
except Exception as exc:
    print(f"[db-preflight] database not ready: {exc}", file=sys.stderr)
    raise SystemExit(1)

print("[db-preflight] database ready")
PY
}

start_local_postgres_service() {
  if command -v pg_lsclusters >/dev/null 2>&1 && command -v pg_ctlcluster >/dev/null 2>&1; then
    local cluster_info
    cluster_info="$(pg_lsclusters --no-header | awk -v target_port="${DB_PORT}" '$3 == target_port {print $1 " " $2; exit}')"
    if [[ -z "$cluster_info" ]]; then
      cluster_info="$(pg_lsclusters --no-header | awk 'NR == 1 {print $1 " " $2; exit}')"
    fi
    if [[ -n "$cluster_info" ]]; then
      local version cluster
      read -r version cluster <<<"$cluster_info"
      echo "[db-preflight] 尝试通过 pg_ctlcluster 启动 PostgreSQL 集群 ${version}/${cluster}"
      if pg_ctlcluster "$version" "$cluster" start; then
        return 0
      fi
    fi
  fi

  if command -v systemctl >/dev/null 2>&1; then
    echo "[db-preflight] 尝试通过 systemctl 启动 postgresql 服务"
    if systemctl start postgresql; then
      return 0
    fi
  fi

  if command -v service >/dev/null 2>&1; then
    echo "[db-preflight] 尝试通过 service 启动 postgresql 服务"
    if service postgresql start; then
      return 0
    fi
  fi

  return 1
}

is_local_host() {
  case "$DB_HOST" in
    127.0.0.1|localhost|::1|-)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [[ "$DB_CONFIGURED" != "1" ]]; then
  echo "[db-preflight] database.postgres.dsn 未配置，跳过数据库检查。"
else
  if ! check_db_ready; then
    echo "[db-preflight] 检测到数据库未就绪: host=${DB_HOST} port=${DB_PORT} db=${DB_NAME}"
    if ! is_local_host; then
      echo "[db-preflight] 当前 DSN 指向非本机数据库，无法自动启动远端实例。" >&2
      exit 1
    fi

    if ! start_local_postgres_service; then
      echo "[db-preflight] 无法自动启动本机 PostgreSQL，请先启动数据库服务。" >&2
      exit 1
    fi

    deadline=$((SECONDS + DB_WAIT_TIMEOUT_SEC))
    while (( SECONDS < deadline )); do
      sleep "$DB_WAIT_INTERVAL_SEC"
      if check_db_ready >/dev/null 2>&1; then
        break
      fi
    done

    if ! check_db_ready; then
      echo "[db-preflight] 等待 ${DB_WAIT_TIMEOUT_SEC}s 后数据库仍未就绪，请检查 PostgreSQL 服务。" >&2
      exit 1
    fi
  fi
fi

exec python3 "$ROOT/runtime/cli/feishu_bot.py" "$@"
