from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProfileUpdateAudit(BaseModel):
    """画像更新审计记录。"""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["user_explicit", "llm_inference", "manual"]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    changed_fields: list[str] = Field(default_factory=list)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class UserProfile(BaseModel):
    """用户长期交易画像（跨会话持久化）"""

    user_id: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # 交易风格
    preferred_style: Literal["left_side", "right_side", "swing", "scalping", "unknown"] = "unknown"

    # 风险偏好
    risk_profile: Literal["conservative", "balanced", "aggressive", "unknown"] = "unknown"

    # 常用标的
    favorite_symbols: list[str] = Field(default_factory=list)

    # 仓位控制
    max_position_ratio: float = 0.25
    preferred_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h"])

    # 自定义备注
    notes: str = ""

    # 新增：市场偏向（支持风格反转）
    market_bias: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"

    # 新增：LLM 观察记录（支持动态演化）
    observations: list[dict[str, Any]] = Field(default_factory=list)

    # 新增：风格演化轨迹
    style_history: list[dict[str, Any]] = Field(default_factory=list)

    # 审计记录（按时间顺序追加）
    audit_log: list[ProfileUpdateAudit] = Field(default_factory=list)

    model_config = {
        "extra": "forbid",
        "json_encoders": {datetime: lambda v: v.isoformat()},
    }
