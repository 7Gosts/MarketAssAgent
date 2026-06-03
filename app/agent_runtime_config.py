"""Agent 运行时开关：简化配置 + 启动校验 + 向后兼容旧 yaml 键。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

_STARTUP_WARNED = False


@dataclass(frozen=True)
class AgentRuntimeConfig:
    context_enabled: bool = True
    pre_judge_enabled: bool = True
    writer_grounded: bool = True
    expose_context_in_response: bool = False
    log_context_explain: bool = True
    followup_transcript_rounds: int = 4
    new_analysis_transcript_rounds: int = 0
    long_history_pre_judge_threshold: int = 6
    pre_judge_system_prompt: str = ""
    research_keyword_enabled: bool = True
    research_keyword_timeout_sec: float = 8.0
    research_keyword_temperature: float = 0.0
    research_keyword_system_prompt: str = ""

    def effective_pre_judge(self) -> bool:
        return bool(self.context_enabled and self.pre_judge_enabled)

    def effective_research_keyword_llm(self) -> bool:
        return bool(self.context_enabled and self.research_keyword_enabled)

    def summary_line(self) -> str:
        return (
            f"context_enabled={self.context_enabled} "
            f"pre_judge={self.effective_pre_judge()} "
            f"writer_grounded={self.writer_grounded}"
        )


def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _int(v: Any, default: int, *, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def load_agent_runtime_config() -> AgentRuntimeConfig:
    from config.runtime_config import get_analysis_config

    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    ctx = agent.get("context") if isinstance(agent.get("context"), dict) else {}
    pj = agent.get("pre_judge") if isinstance(agent.get("pre_judge"), dict) else {}
    rk = agent.get("research_keyword") if isinstance(agent.get("research_keyword"), dict) else {}
    wr = agent.get("writer") if isinstance(agent.get("writer"), dict) else {}
    dbg = agent.get("debug") if isinstance(agent.get("debug"), dict) else {}

    context_enabled = _bool(ctx.get("enabled"), True)
    pre_judge_enabled = _bool(pj.get("enabled"), _bool(ctx.get("pre_judge_enabled"), True))
    writer_grounded = _bool(wr.get("grounded"), _bool(agent.get("enable_grounded_writer"), True))
    expose = _bool(dbg.get("expose_context_in_response"), _bool(ctx.get("expose_in_response"), False))
    log_explain = _bool(dbg.get("log_context_explain"), _bool(ctx.get("log_context_explain"), True))
    pj_prompt = str(pj.get("system_prompt") or ctx.get("pre_judge_system_prompt") or "").strip()
    rk_prompt = str(rk.get("system_prompt") or "").strip()
    try:
        rk_timeout = float(rk.get("timeout_sec") or 8.0)
    except (TypeError, ValueError):
        rk_timeout = 8.0
    try:
        rk_temp = float(rk.get("temperature") if rk.get("temperature") is not None else 0.0)
    except (TypeError, ValueError):
        rk_temp = 0.0

    return AgentRuntimeConfig(
        context_enabled=context_enabled,
        pre_judge_enabled=pre_judge_enabled,
        writer_grounded=writer_grounded,
        expose_context_in_response=expose,
        log_context_explain=log_explain,
        followup_transcript_rounds=_int(ctx.get("followup_transcript_rounds"), 4, lo=0, hi=12),
        new_analysis_transcript_rounds=_int(ctx.get("new_analysis_transcript_rounds"), 0, lo=0, hi=12),
        long_history_pre_judge_threshold=_int(ctx.get("long_history_pre_judge_threshold"), 6, lo=2, hi=50),
        pre_judge_system_prompt=pj_prompt,
        research_keyword_enabled=_bool(rk.get("enabled"), True),
        research_keyword_timeout_sec=max(2.0, min(30.0, rk_timeout)),
        research_keyword_temperature=rk_temp,
        research_keyword_system_prompt=rk_prompt,
    )


def validate_agent_runtime_config(cfg: AgentRuntimeConfig) -> list[str]:
    warnings: list[str] = []
    if not cfg.context_enabled:
        warnings.append(
            "context.enabled=false：已彻底回退固定 llm_memory_rounds 历史；Pre-Judge / agent_context 不生效。"
        )
        if cfg.pre_judge_enabled:
            warnings.append(
                "context.enabled=false 但 pre_judge.enabled=true：Pre-Judge 将被忽略，请关闭 pre_judge 或开启 context。"
            )
    if not cfg.writer_grounded:
        warnings.append("writer.grounded=false：最终回复走模板 fallback，不用 LLM 撰稿。")
    return warnings


def ensure_agent_runtime_startup_logged() -> AgentRuntimeConfig:
    global _STARTUP_WARNED
    cfg = load_agent_runtime_config()
    if _STARTUP_WARNED:
        return cfg
    _STARTUP_WARNED = True
    for msg in validate_agent_runtime_config(cfg):
        logger.warning("[AgentRuntime] {}", msg)
    if not cfg.context_enabled:
        logger.warning(
            "[AgentRuntime] 智能上下文已关闭 (context.enabled=false)，使用旧版固定历史逻辑；"
            "排查完成后建议重新开启 context.enabled=true。"
        )
    else:
        logger.info("[AgentRuntime] 智能路径已启用 {}", cfg.summary_line())
    return cfg
