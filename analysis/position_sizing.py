"""按账户币种与单笔最大亏损比例计算纸交易数量。"""
from __future__ import annotations

import math
from typing import Any

from config.runtime_config import get_accounts_config
from persistence.db import get_sqlalchemy_engine

_RISK_EPS = 1e-12

_DEFAULT_STOP_PCT: dict[str, float] = {
    "CRYPTO": 0.012,
    "US": 0.012,
    "CN": 0.012,
    "PM": 0.008,
    "HK": 0.012,
}
_DEFAULT_STOP_PCT_FALLBACK = 0.012

# market（大写）→ 账本币种
MARKET_TO_CURRENCY: dict[str, str] = {
    "CN": "CNY",
    "US": "USD",
    "CRYPTO": "USD",
    "PM": "CNY",
    "HK": "USD",
}


def map_market_to_currency(market: str | None) -> str:
    m = str(market or "").strip().upper()
    return MARKET_TO_CURRENCY.get(m, "USD")


def _safe_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _entry_ref_from_idea(idea: dict[str, Any]) -> float | None:
    ep = _safe_float(idea.get("entry_price"))
    if ep is not None:
        return ep
    fp = _safe_float(idea.get("fill_price"))
    if fp is not None:
        return fp
    zone = idea.get("entry_zone")
    if isinstance(zone, list) and len(zone) == 2:
        a, b = _safe_float(zone[0]), _safe_float(zone[1])
        if a is not None and b is not None:
            return (a + b) / 2.0
    return None


def _floor_to_step(qty: float, step: float) -> float:
    if step <= _RISK_EPS:
        return qty
    n = math.floor(qty / step + 1e-15)
    return round(n * step, 12)


def _default_stop_for_market(entry_ref: float, direction: str, market: str | None) -> float:
    pct = _DEFAULT_STOP_PCT.get(str(market or "").upper(), _DEFAULT_STOP_PCT_FALLBACK)
    if str(direction or "long").strip().lower() == "short":
        return entry_ref * (1.0 + pct)
    return entry_ref * (1.0 - pct)


def calculate_qty_for_idea(idea: dict[str, Any], account_ledger: dict[str, Any] | None = None) -> tuple[float, dict[str, Any]]:
    """
    qty = (balance * max_loss_pct) / |entry - stop|，再按 qty_step 向下取整步长。
    返回 (qty, detail)；qty==0 表示头寸过小或参数不足，跳过纸交易；
    失败时不再降级 qty=1.0（过去 fallback=1 会导致 BTC 开 1 整币等极端名义），
    而统一返回 qty=0.0 + fallback=True + skip_reason，由调用方决定是否跳过。
    """
    accounts = get_accounts_config()
    market = idea.get("market")
    currency = map_market_to_currency(str(market) if market is not None else "")

    base_detail: dict[str, Any] = {
        "market": str(market or ""),
        "currency": currency,
        "fallback": False,
    }

    if not accounts:
        return 0.0, {
            **base_detail,
            "fallback": True,
            "reason": "no_accounts_config",
        }

    acct = accounts.get(currency) or accounts.get("USD") or {}
    if not acct:
        return 0.0, {
            **base_detail,
            "fallback": True,
            "reason": "no_account_for_currency",
        }

    # 显式传入 account_ledger.available 时优先；有 PG 引擎时用 get_available_balance，否则用 YAML accounts
    balance_source: str
    if account_ledger and isinstance(account_ledger.get("available"), (int, float)):
        balance = float(account_ledger.get("available"))
        balance_source = "caller"
    elif get_sqlalchemy_engine() is not None:
        from persistence import account_service as _acct_svc

        snap = _acct_svc.get_or_init_account(currency)
        if snap.get("ledger_missing"):
            return 0.0, {
                **base_detail,
                "fallback": True,
                "reason": "ledger_not_initialized",
                "hint": "请执行 alembic upgrade head（含 journal_004），按 YAML accounts 写入 account_ledger；或手工插入 reason=\"init\" 行。",
            }
        balance = float(snap.get("available") or 0.0)
        balance_source = "database"
    else:
        balance = _safe_float(acct.get("initial_balance") or acct.get("balance"))
        balance_source = "config"
    max_loss_pct = _safe_float(acct.get("max_loss_pct"))
    qty_step = _safe_float(acct.get("qty_step")) or 0.0001

    if balance is None or max_loss_pct is None or balance <= _RISK_EPS or max_loss_pct <= _RISK_EPS:
        return 0.0, {
            **base_detail,
            "fallback": True,
            "reason": "invalid_account_params",
            "balance": balance,
            "max_loss_pct": max_loss_pct,
        }

    entry_ref = _entry_ref_from_idea(idea)
    stop_ref = _safe_float(idea.get("stop_loss"))

    if entry_ref is None:
        return 0.0, {
            **base_detail,
            "fallback": True,
            "reason": "missing_entry",
            "entry_ref": entry_ref,
            "stop_ref": stop_ref,
        }

    stop_source = "idea"
    if stop_ref is None:
        direction = str(idea.get("direction") or "long").strip().lower()
        stop_ref = _default_stop_for_market(entry_ref, direction, market)
        stop_source = "default"

    risk_per_unit = abs(entry_ref - stop_ref)
    if risk_per_unit <= _RISK_EPS:
        return 0.0, {
            **base_detail,
            "fallback": True,
            "reason": "zero_risk_per_unit",
            "entry_ref": entry_ref,
            "stop_ref": stop_ref,
        }

    max_loss_amount = balance * max_loss_pct
    qty_raw = max_loss_amount / risk_per_unit
    qty_stepped = _floor_to_step(qty_raw, qty_step)

    detail: dict[str, Any] = {
        **base_detail,
        "balance_source": balance_source,
        "balance": balance,
        "max_loss_pct": max_loss_pct,
        "max_loss_amount": max_loss_amount,
        "entry_ref": entry_ref,
        "stop_ref": stop_ref,
        "stop_source": stop_source,
        "risk_per_unit": risk_per_unit,
        "qty_raw": qty_raw,
        "qty_step": qty_step,
        "qty_before_round": qty_raw,
    }

    if qty_stepped < qty_step - _RISK_EPS:
        return 0.0, {
            **detail,
            "qty": 0.0,
            "skip_reason": "below_min_step",
        }

    detail["qty"] = qty_stepped
    return float(qty_stepped), detail
