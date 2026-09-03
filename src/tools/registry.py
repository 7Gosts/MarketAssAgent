"""原生工具注册中心：统一管理模型 schema、上下文注入和副作用分类。"""

from __future__ import annotations

from typing import Any

from core.tool_protocol import ToolContext, ToolSpec
from utils.logging_utils import get_logger


logger = get_logger(__name__)


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]) -> None:
        self._by_name = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._by_name.values())

    def schemas(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        specs = self.all()
        if allowed_names is not None:
            specs = [spec for spec in specs if spec.name in allowed_names]
        return [spec.openai_schema() for spec in specs]


def _object_schema(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


def _string(description: str, *, enum: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if enum:
        schema["enum"] = enum
    return schema


def _number(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def _integer(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _load_tools() -> dict[str, Any]:
    from domain.market.analysis_service import (
        analyze_fibonacci,
        analyze_market,
        evaluate_structure,
        get_key_levels,
    )
    from domain.profile.user_profile import get_user_profile, update_user_profile
    from tools.context_memory import (
        get_last_snapshot,
        get_previous_analysis_snapshot,
        get_recent_tool_observations,
        search_conversation_summaries,
    )
    from tools.market_data import fetch_market_data
    from tools.research import search_research_reports
    from tools.response_guidance import get_response_guidance
    from tools.sim_account import (
        cancel_paper_order,
        get_journal_status,
        prepare_simulated_order,
        reconcile_paper_orders,
        simulate_open_position,
    )

    return locals()


def _build_specs() -> list[ToolSpec]:
    tools = _load_tools()

    def call(name: str):
        return tools[name]

    def analyze_market_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("analyze_market")(
            **kwargs,
            session_id=context.session_id,
            request_id=context.request_id,
        )

    def previous_snapshot_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        exclude_request_id = str(kwargs.pop("exclude_request_id", "") or context.request_id)
        return call("get_previous_analysis_snapshot")(
            **kwargs,
            session_id=context.session_id,
            request_id=context.request_id,
            exclude_request_id=exclude_request_id,
        )

    def last_snapshot_with_context(*, context: ToolContext, **_kwargs: Any) -> Any:
        return call("get_last_snapshot")(session_id=context.session_id)

    def observations_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("get_recent_tool_observations")(session_id=context.session_id, **kwargs)

    def summaries_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("search_conversation_summaries")(session_id=context.session_id, **kwargs)

    def prepare_order_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("prepare_simulated_order")(
            **kwargs,
            session_id=context.session_id,
            request_id=context.request_id,
        )

    def create_order_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("simulate_open_position")(
            **kwargs,
            session_id=context.session_id,
            request_id=context.request_id,
        )

    def cancel_order_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("cancel_paper_order")(
            **kwargs,
            session_id=context.session_id,
            request_id=context.request_id,
        )

    def reconcile_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("reconcile_paper_orders")(session_id=context.session_id, **kwargs)

    def journal_with_context(*, context: ToolContext, **kwargs: Any) -> Any:
        return call("get_journal_status")(session_id=context.session_id, **kwargs)

    symbol_interval = {
        "symbol": _string("规范交易代码，例如 ETHUSDT、600519.SH、NVDA"),
        "interval": _string("K 线周期，例如 1h、4h、1d"),
    }
    order_properties = {
        "symbol": _string("规范交易代码"),
        "direction": _string("交易方向", enum=["long", "short"]),
        "entry_price": _number("入场或触发价格"),
        "stop_loss": _number("止损价格"),
        "take_profit": _number("止盈价格"),
        "position_size": _number("正数仓位数量"),
        "interval": _string("订单观察周期"),
        "source_snapshot_id": _string("来源分析快照 ID"),
        "order_type": _string("订单类型"),
        "position_state": _string("pending 表示待触发，open 表示已成交", enum=["pending", "open"]),
        "valid_until": _string("订单有效期 ISO 时间"),
        "strategy_reason": _string("策略理由"),
    }
    prepare_properties = dict(order_properties)
    prepare_properties["asset_text"] = prepare_properties.pop("symbol")

    return [
        ToolSpec(
            name="analyze_market",
            description="统一行情分析入口，支持单标的或多组 symbol/interval 请求。",
            parameters=_object_schema({
                **symbol_interval,
                "force_refresh": _boolean("是否强制刷新"),
                "requests": {
                    "type": "array",
                    "description": "多标的或多周期请求",
                    "items": {"type": "object"},
                },
            }),
            execute=analyze_market_with_context,
            side_effect="write",
            requires_context=True,
        ),
        ToolSpec(
            name="get_key_levels",
            description="获取标的关键支撑位和阻力位。",
            parameters=_object_schema(symbol_interval, required=["symbol"]),
            execute=call("get_key_levels"),
        ),
        ToolSpec(
            name="evaluate_structure",
            description="评估市场结构、趋势和量价关系。",
            parameters=_object_schema({
                "symbol": symbol_interval["symbol"],
                "snapshot": {"type": "object", "description": "可选分析快照"},
            }, required=["symbol"]),
            execute=call("evaluate_structure"),
        ),
        ToolSpec(
            name="analyze_fibonacci",
            description="根据行情或指定高低点计算斐波那契水平。",
            parameters=_object_schema({
                **symbol_interval,
                "swing_high": _number("摆动高点"),
                "swing_low": _number("摆动低点"),
            }, required=["symbol"]),
            execute=call("analyze_fibonacci"),
        ),
        ToolSpec(
            name="search_research_reports",
            description="搜索研报或概念板块信息。",
            parameters=_object_schema({
                "keyword": _string("搜索关键词"),
                "top_k": _integer("返回条数"),
            }, required=["keyword"]),
            execute=call("search_research_reports"),
        ),
        ToolSpec(
            name="prepare_simulated_order",
            description="解析并校验模拟订单草稿，不写入账本。",
            parameters=_object_schema(
                prepare_properties,
                required=["asset_text", "direction", "entry_price", "stop_loss", "take_profit"],
            ),
            execute=prepare_order_with_context,
            requires_context=True,
        ),
        ToolSpec(
            name="simulate_open_position",
            description="创建模拟跟踪单并写入正式账本。",
            parameters=_object_schema(
                order_properties,
                required=["symbol", "direction", "entry_price", "stop_loss", "take_profit"],
            ),
            execute=create_order_with_context,
            side_effect="write",
            requires_context=True,
        ),
        ToolSpec(
            name="cancel_paper_order",
            description="取消当前会话中精确指定的 pending_trigger 模拟订单，保留历史记录。",
            parameters=_object_schema({
                "order_id": {**_string("精确订单 ID"), "minLength": 1},
                "reason": _string("取消原因"),
            }, required=["order_id"]),
            execute=cancel_order_with_context,
            side_effect="write",
            requires_context=True,
        ),
        ToolSpec(
            name="reconcile_paper_orders",
            description="根据最新行情同步当前会话的活跃模拟订单状态。",
            parameters=_object_schema(symbol_interval),
            execute=reconcile_with_context,
            side_effect="write",
            requires_context=True,
        ),
        ToolSpec(
            name="get_journal_status",
            description="查询当前会话的模拟挂单、持仓、关闭订单和事件。",
            parameters=_object_schema(symbol_interval),
            execute=journal_with_context,
            requires_context=True,
        ),
        ToolSpec(
            name="fetch_market_data",
            description="获取标的 K 线行情数据。",
            parameters=_object_schema(symbol_interval, required=["symbol"]),
            execute=call("fetch_market_data"),
        ),
        ToolSpec(
            name="get_user_profile",
            description="读取指定用户标识的画像。",
            parameters=_object_schema({"storage_key": _string("用户唯一标识")}, required=["storage_key"]),
            execute=call("get_user_profile"),
        ),
        ToolSpec(
            name="update_user_profile",
            description="更新指定用户标识的画像。",
            parameters=_object_schema({
                "storage_key": _string("用户唯一标识"),
                "updates": {"type": "object", "description": "画像字段更新"},
                "reason": _string("更新原因"),
                "confidence": _number("0 到 1 的置信度"),
            }, required=["storage_key", "updates"]),
            execute=call("update_user_profile"),
            side_effect="write",
        ),
        ToolSpec(
            name="get_response_guidance",
            description="按需获取行情、交易计划等回答的短输出规范。",
            parameters=_object_schema({
                "guidance_type": _string(
                    "指导类型",
                    enum=["market_view", "trade_plan", "position_review", "research_view", "source_explain"],
                ),
            }, required=["guidance_type"]),
            execute=call("get_response_guidance"),
        ),
        ToolSpec(
            name="get_last_snapshot",
            description="读取当前会话最近的市场分析快照。",
            parameters=_object_schema({}),
            execute=last_snapshot_with_context,
            requires_context=True,
        ),
        ToolSpec(
            name="get_previous_analysis_snapshot",
            description="读取当前会话同标的同周期的上一条分析快照。",
            parameters=_object_schema({
                **symbol_interval,
                "exclude_request_id": _string("可选的排除请求 ID"),
                "limit": _integer("最大扫描条数"),
            }, required=["symbol", "interval"]),
            execute=previous_snapshot_with_context,
            requires_context=True,
        ),
        ToolSpec(
            name="get_recent_tool_observations",
            description="读取当前会话最近的工具观察摘要。",
            parameters=_object_schema({"limit": _integer("返回条数")}),
            execute=observations_with_context,
            requires_context=True,
        ),
        ToolSpec(
            name="search_conversation_summaries",
            description="读取当前会话最近多轮对话摘要。",
            parameters=_object_schema({
                "limit": _integer("返回轮数"),
                "max_chars": _integer("最大字符预算"),
            }),
            execute=summaries_with_context,
            requires_context=True,
        ),
    ]


def get_tool_registry() -> ToolRegistry:
    try:
        return ToolRegistry(_build_specs())
    except Exception as exc:
        logger.exception("[registry] tool registration failed: %s", exc)
        raise


def get_all_tools() -> list[ToolSpec]:
    return get_tool_registry().all()
