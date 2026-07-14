from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_CFG_DIR = Path(__file__).resolve().parent
_DEFAULT_CFG_PATH = _CFG_DIR / "analysis_defaults.yaml"
_EXAMPLE_CFG_PATH = _CFG_DIR / "analysis_defaults.example.yaml"
_CFG_CACHE: dict[str, Any] | None = None


def _resolve_cfg_path() -> Path:
    # Docker/Compose 可挂载到其他路径，用环境变量覆盖；本地默认读同目录 YAML。
    override = os.getenv("STOCK_ANALYSIS_CRYPTO_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_CFG_PATH


def _require_cfg_path() -> Path:
    path = _resolve_cfg_path()
    if path.is_file():
        return path
    raise FileNotFoundError(
        f"缺少本地配置文件: {path}\n"
        f"请先复制模板后再填写密钥与个性化参数：\n"
        f"  cp {_EXAMPLE_CFG_PATH} {_DEFAULT_CFG_PATH}"
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    if not path.is_file():
        return {}
    try:
        obj = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def get_analysis_config(*, force_reload: bool = False) -> dict[str, Any]:
    global _CFG_CACHE
    if force_reload or _CFG_CACHE is None:
        _CFG_CACHE = _load_yaml(_require_cfg_path())
    return _CFG_CACHE


def get_llm_config() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("llm")
    return node if isinstance(node, dict) else {}


def get_default_llm_provider(default: str = "deepseek") -> str:
    llm_cfg = get_llm_config()
    provider = str(llm_cfg.get("default_provider") or "").strip().lower()
    if provider:
        return provider
    return default


def get_llm_provider_config(provider: str | None = None) -> dict[str, Any]:
    provider_name = str(provider or get_default_llm_provider()).strip().lower()
    llm_cfg = get_llm_config()
    providers = llm_cfg.get("providers") if isinstance(llm_cfg.get("providers"), dict) else {}
    node = providers.get(provider_name) if isinstance(providers.get(provider_name), dict) else {}
    out = dict(node)
    out.setdefault("provider", provider_name)
    out.setdefault("env_prefix", provider_name.upper())
    out.setdefault("openai_compatible", True)
    return out


def get_llm_runtime_settings(provider: str | None = None) -> dict[str, Any]:
    node = get_llm_provider_config(provider)
    env_prefix = str(node.get("env_prefix") or node.get("provider") or "LLM").strip().upper()

    provider_name = str(node.get("provider") or get_default_llm_provider()).strip().lower()
    model = str(node.get("model") or "").strip()
    base_url = str(node.get("base_url") or "").strip()
    api_key = str(node.get("api_key") or "").strip()
    node_has_temperature = "temperature" in node and node.get("temperature") not in (None, "")
    raw_temperature: Any = node.get("temperature")
    temperature: float | None = None
    if raw_temperature not in (None, ""):
        try:
            temperature = float(raw_temperature)
        except (TypeError, ValueError):
            temperature = None
    temperature_is_explicit = bool(node_has_temperature)

    return {
        "provider": provider_name,
        "env_prefix": env_prefix,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": temperature,
        "temperature_is_explicit": temperature_is_explicit,
        "openai_compatible": bool(node.get("openai_compatible", True)),
    }


def require_llm_model(llm_settings: dict[str, Any], *, context: str = "LLM") -> str:
    """返回已配置的 model；未配置时抛出明确错误。"""
    model = str(llm_settings.get("model") or "").strip()
    if model:
        return model

    provider = str(llm_settings.get("provider") or "").strip() or "default_provider"
    env_prefix = str(llm_settings.get("env_prefix") or "LLM").strip().upper()
    raise RuntimeError(
        f"{context} model 未配置，请在 analysis_defaults.yaml -> llm.providers.{provider}.model "
        f"中设置（env_prefix={env_prefix}）。"
    )


def resolve_llm_temperature(
    llm_settings: dict[str, Any],
    *,
    fallback: float,
) -> float:
    """优先使用 provider/env 中显式配置的 temperature；否则回退到调用方默认值。"""
    if llm_settings.get("temperature_is_explicit"):
        value = llm_settings.get("temperature")
        if isinstance(value, (int, float)):
            return float(value)
    return float(fallback)


def get_ma_system() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("ma_system")
    return node if isinstance(node, dict) else {}


def get_min_journal_rr(default: float = 1.2) -> float:
    cfg = get_analysis_config()
    v = cfg.get("min_journal_rr", default)
    try:
        x = float(v)
        return x if x > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def get_journal_quality() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("journal_quality")
    return node if isinstance(node, dict) else {}


def get_journal_action_thresholds() -> tuple[float, float]:
    cfg = get_analysis_config()
    node = cfg.get("journal_action_thresholds")
    if not isinstance(node, dict):
        return 1.45, 1.2
    worth = node.get("worth_doing_rr")
    observe = node.get("observe_rr")
    worth_v = float(worth) if isinstance(worth, (int, float)) else 1.45
    observe_v = float(observe) if isinstance(observe, (int, float)) else 1.2
    if worth_v < observe_v:
        worth_v = observe_v
    return worth_v, observe_v


def get_database_config() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("database")
    return node if isinstance(node, dict) else {}


def get_postgres_dsn() -> str:
    db = get_database_config()
    pg = db.get("postgres") if isinstance(db.get("postgres"), dict) else {}
    return str(pg.get("dsn") or "").strip()


def get_accounts_config() -> dict[str, dict[str, Any]]:
    """多币种账户：键为大写币种代码（CNY/USD），值为 balance / max_loss_pct / qty_step 等。"""
    cfg = get_analysis_config()
    node = cfg.get("accounts")
    if not isinstance(node, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in node.items():
        ck = str(k).strip().upper()
        if isinstance(v, dict) and ck:
            out[ck] = dict(v)
    return out


def get_account_system_config() -> dict[str, Any]:
    """account_system 子树（无 enabled 开关；账本逻辑始终启用，无 PG 时内部 no-op）。"""
    cfg = get_analysis_config()
    node = cfg.get("account_system")
    return node if isinstance(node, dict) else {}


def get_account_initial_balance(currency: str) -> float:
    ac = get_accounts_config()
    c = str(currency).strip().upper()
    if c in ac:
        val = ac[c].get("initial_balance") or ac[c].get("balance")
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def get_feature_flags() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("feature_flags")
    return node if isinstance(node, dict) else {}


def get_memory_config() -> dict[str, Any]:
    """读取 MemoryAPI / FactStore 配置（memory.backend 等）。"""
    cfg = get_analysis_config()
    node = cfg.get("memory")
    return node if isinstance(node, dict) else {}


def get_agent_context_config() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("agent_context")
    return node if isinstance(node, dict) else {}


def _coerce_positive_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return num if num >= minimum else default


def get_agent_context_limits() -> dict[str, int]:
    cfg = get_agent_context_config()
    return {
        "max_chars": _coerce_positive_int(cfg.get("max_chars"), 13434, minimum=1200),
        "max_summary_chars": _coerce_positive_int(cfg.get("max_summary_chars"), 1000, minimum=240),
    }


def is_feature_enabled(name: str, *, default: bool = False) -> bool:
    key = str(name).strip()
    if not key:
        return bool(default)

    env_key = f"MARKETASSAGENT_FEATURE_{key.upper()}"
    env_val = os.getenv(env_key, "").strip().lower()
    if env_val in {"1", "true", "yes", "on"}:
        return True
    if env_val in {"0", "false", "no", "off"}:
        return False

    flags = get_feature_flags()
    raw = flags.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(default)


def reload_accounts_config() -> None:
    """Force reload of YAML config (accounts and other parameters).

    Call this after editing `runtime/config/analysis_defaults.yaml` during runtime.
    """
    global _CFG_CACHE
    _CFG_CACHE = _load_yaml(_require_cfg_path())
