from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CFG_PATH = _REPO_ROOT / "config" / "analysis_defaults.yaml"
_DEFAULT_EXAMPLE_CFG_PATH = _REPO_ROOT / "config" / "analysis_defaults.example.yaml"
_CFG_CACHE: dict[str, Any] | None = None


def _resolve_cfg_path() -> Path:
    override = os.getenv("STOCK_ANALYSIS_CRYPTO_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _DEFAULT_CFG_PATH.is_file():
        return _DEFAULT_CFG_PATH
    if _DEFAULT_EXAMPLE_CFG_PATH.is_file():
        return _DEFAULT_EXAMPLE_CFG_PATH
    return _DEFAULT_CFG_PATH


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
        _CFG_CACHE = _load_yaml(_resolve_cfg_path())
    return _CFG_CACHE


def get_llm_config() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("llm")
    return node if isinstance(node, dict) else {}


def get_default_llm_provider(default: str = "deepseek") -> str:
    env_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if env_provider:
        return env_provider
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
    model = (
        os.getenv("LLM_MODEL", "").strip()
        or os.getenv(f"{env_prefix}_MODEL", "").strip()
        or str(node.get("model") or "").strip()
    )
    base_url = (
        os.getenv("LLM_BASE_URL", "").strip()
        or os.getenv(f"{env_prefix}_BASE_URL", "").strip()
        or str(node.get("base_url") or "").strip()
    )
    api_key = (
        os.getenv("LLM_API_KEY", "").strip()
        or os.getenv(f"{env_prefix}_API_KEY", "").strip()
        or str(node.get("api_key") or "").strip()
    )
    global_temp = os.getenv("LLM_TEMPERATURE", "").strip()
    provider_temp = os.getenv(f"{env_prefix}_TEMPERATURE", "").strip()
    node_has_temperature = "temperature" in node and node.get("temperature") not in (None, "")
    raw_temperature: Any = global_temp or provider_temp or node.get("temperature")
    temperature: float | None = None
    if raw_temperature not in (None, ""):
        try:
            temperature = float(raw_temperature)
        except (TypeError, ValueError):
            temperature = None
    temperature_is_explicit = bool(global_temp or provider_temp or node_has_temperature)

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

    env_prefix = str(llm_settings.get("env_prefix") or "LLM").strip().upper()
    raise RuntimeError(
        f"{context} model 未配置，请在 analysis_defaults.yaml 或环境变量 "
        f"LLM_MODEL / {env_prefix}_MODEL 中设置。"
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

    Call this after editing `config/analysis_defaults.yaml` during runtime.
    """
    global _CFG_CACHE
    _CFG_CACHE = _load_yaml(_resolve_cfg_path())


def get_tickflow_api_key() -> str:
    """tickflow API key：仅从环境变量读取（YAML data_sources 已废弃）"""
    return os.getenv("TICKFLOW_API_KEY", "").strip()
