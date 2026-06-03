from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.runtime_config import get_analysis_config, get_llm_runtime_settings
from analysis.beijing_time import default_review_time_for_interval, now_beijing_str


class LLMClientError(RuntimeError):
    """LLM client 调用异常（provider-agnostic）。"""
    pass


# OpenAI-compatible API：使用 response_format=json_object 时，messages 全文须出现子串 "json"（见 API 报错 invalid_request_error）
_JSON_OBJECT_SYSTEM_SUFFIX = "\n\n(json: Your entire reply must be one JSON object.)"


def _base_url() -> str:
    settings = get_llm_runtime_settings()
    url = str(settings.get("base_url") or "").strip()
    if url:
        return url.rstrip("/")
    provider = str(settings.get("provider") or "deepseek").strip().lower()
    if provider == "deepseek":
        return "https://api.deepseek.com"
    raise LLMClientError("缺少 LLM base_url（可通过 LLM_BASE_URL、<PROVIDER>_BASE_URL 或 YAML llm.providers.<provider>.base_url 配置）。")


def _model_name() -> str:
    settings = get_llm_runtime_settings()
    model = str(settings.get("model") or "").strip()
    if model:
        return model
    provider = str(settings.get("provider") or "deepseek").strip().lower()
    if provider == "deepseek":
        return "deepseek-v4-flash"
    raise LLMClientError("缺少 LLM model（可通过 LLM_MODEL、<PROVIDER>_MODEL 或 YAML llm.providers.<provider>.model 配置）。")


def _api_key() -> str:
    settings = get_llm_runtime_settings()
    key = str(settings.get("api_key") or "").strip()
    if not key:
        raise LLMClientError(
            "缺少 LLM API Key（可通过 LLM_API_KEY、<PROVIDER>_API_KEY 或 YAML llm.providers.<provider>.api_key 配置）。"
        )
    return key


def _resolved_temperature(default: float) -> float:
    settings = get_llm_runtime_settings()
    provider_temperature = settings.get("temperature")
    if provider_temperature is None:
        return float(default)
    return float(provider_temperature)


def _intent_router_prompt_cfg() -> dict[str, Any]:
    """意图路由 LLM 的 system / temperature（优先 agent，兼容旧 feishu.*）。"""
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    if isinstance(agent, dict) and str(agent.get("router_system_prompt") or "").strip():
        system_prompt = str(agent.get("router_system_prompt")).strip()
    else:
        system_prompt = str(fei.get("llm_router_system_prompt") or "").strip()
    if isinstance(agent, dict) and "router_temperature" in agent:
        temperature = float(agent.get("router_temperature") or 0.0)
    else:
        temperature = float(fei.get("llm_router_temperature") or 0.0)
    return {"llm_router_system_prompt": system_prompt, "llm_router_temperature": temperature}


# Router policy 常量（不再从 YAML 配置读取）
ROUTER_POLICY = {
    "allowed_intervals": ["15m", "30m", "1h", "4h", "1d"],
    "default_interval": "4h",
}


def _feishu_router_interval_instruction() -> str:
    return (
        "\n\n周期（interval）约定：用户若没有明确写出具体 K 线周期（15m/30m/1h/4h/1d），"
        "先按资产大类使用默认周期，而不是优先 clarify_intent。"
        "加密货币（CRYPTO / gateio）默认 4h；股票、ETF、贵金属（CN / US / PM）默认 1d。"
        "只有在用户明显要做更细的技术结构、交易计划，或标的本身不明确时，才调用 clarify_intent。"
    )


def _feishu_router_context_hierarchy_instruction() -> str:
    return (
        "\n\ncontext_hierarchy 使用规则："
        "current_query.intent_type=new_analysis 时按 user_message 重新 analyze_market，勿因旧会话误判 followup；"
        "followup/execution_question 从 short_term.recent_analyses 或 conversation_context.last_symbols 承接，execution 用 followup(followup_type=execution)；"
        "history_policy=minimal 或 transcript 为空是预期，不得因此改判 followup；"
        "long_term 仅作默认周期/偏好参考，不是价格事实源。"
    )



# 未配置 agent.router_system_prompt（或兼容旧 feishu.llm_router_system_prompt）时使用；以 tools 为主，无 tool_calls 时见 decide_feishu_route 对 assistant 正文的兜底。
DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT = """你是飞书行情分析机器人的路由器（股票 tickflow、贵金属 goldapi、加密 gateio；可选研报/板块/归属/概念检索；模拟账户余额/持仓/订单/成交查看）。
优先调用提供的工具之一完成意图；不要编造成交、主力资金、交易所逐笔资金流、仓位或「已下单」类结论。
闲聊、致谢或引导用户发起行情分析时，请使用 reply_chat：message 可写多段完整中文，可简要归类列出用户 JSON 里 tradable_assets 相关标的与示例问法（不必抄全表名，分类说明即可）。
若模型接口未返回 tool_calls、仅在 assistant 正文中输出内容，后端也会把正文交给用户；但仍应优先用 reply_chat(message=...) 一次性给出可读回复。
用户消息 JSON 顶层字段：user_message（最新一句）、context_hierarchy（long_term/short_term/current_query/history_policy/intent_confidence）、
conversation_transcript（最近若干轮 user/assistant 文本，可能为空）、transcript_suppressed（true 表示后端刻意不传历史）、
conversation_context（结构化线索：last_task_type、last_symbols 等，非价格事实源）、
policy_injection（内含 tradable_assets、default_symbol、default_interval）。
context_hierarchy 使用规则：
- current_query.intent_type=new_analysis → 按 user_message 重新 analyze_market，勿因旧会话误判 followup。
- intent_type=followup 或 execution_question → 从 short_term.recent_analyses / conversation_context.last_symbols 承接；execution 用 followup(followup_type=execution)。
- history_policy=minimal 或 transcript 为空 → 预期行为，不得因缺历史而改判 followup。
- long_term 仅默认周期/偏好参考，不是价格事实源。
行情分析必须调用 analyze_market：symbols 只能从 policy_injection.tradable_assets 里的 symbol 选取；单标的也必须传长度为 1 的 symbols 列表，不要传 symbol 单数字段。
如用户问题涉及"查研报""查板块""查归属""查概念""查主题"或类似表达，优先调用 search_research 或 query_concept_board 工具（如仅有关键词可只填 keyword，若有 symbol 可一并填写）。
如用户问题涉及"余额""资金""账户""持仓""订单""成交""仓位""模拟账户"或类似表达，优先调用 view_sim_account 工具。scope 默认 overview（综合），用户明确只问持仓/订单/成交等时可指定 scope。
如用户问题同时涉及行情分析与研报/板块/归属检索，需分别调用 analyze_market 与 search_research/query_concept_board，并分栏输出。
不得将研报检索或板块归属混入行情分析主流程。
若用户只是泛问"行情 / 现价 / 报价 / 看看走势 / 最近怎么样"，优先走行情快照语义；后端会再落为 quote 或 analysis。
interval 仅 15m/30m/1h/4h/1d；用户未明确周期时，优先按资产大类给默认周期：CRYPTO=4h，CN/US/PM=1d，而不是先 clarify_intent。
若用户说"看看虚拟币行情""看看加密货币行情""看看币圈行情"但未指明具体币种，默认选择 symbols=["BTC_USDT","ETH_USDT","SOL_USDT"]，interval=4h。
若用户说"看看股票行情""看看美股行情""看看A股行情"但未指明具体标的，不要编造股票代码；此时可以 reply_chat 请用户补充标的，或按已有上下文承接。
用户明确指定多个周期（如"4h 和 1h"）时，使用 intervals 数组：intervals=["4h","1h"]；单周期仍用 interval 字段。
如用户追问上一轮分析的具体方面（"它的止损呢""入场位在哪""有没有触发"），使用 followup 工具，从 conversation_context 获取上一轮标的和周期。
如用户要求调整展示格式（"精确2位小数""简短版""再说一遍"），使用 display_adjustment 工具，不需新分析。
如用户提及不在 tradable_assets 中的金融资产（如新币种、小盘股），使用 discover_asset 工具，query_text 传原文。
只有在用户明显要求更细的结构分析、交易计划、或标的/周期本身存在歧义时，才调用 clarify_intent。
provider 须与所选标的在 tradable_assets 中的 provider 一致。
with_research：用户明确要看研报/机构观点时为 true。
信息不足无法选合法标的或周期时，请调用 reply_chat 自然地反问用户。

示例：
- "看看 eth 的行情" -> analyze_market(symbols=["ETH_USDT"], interval="4h", question="看看 eth 的行情")
- "看看 ETH 的 4h 和 1h 线" -> analyze_market(symbols=["ETH_USDT"], intervals=["4h","1h"], question="看看 ETH 的 4h 和 1h 线")
- "看看虚拟币行情" -> analyze_market(symbols=["BTC_USDT","ETH_USDT","SOL_USDT"], interval="4h", question="看看虚拟币行情")
- "看看英伟达行情" -> analyze_market(symbols=["NVDA"], interval="1d", question="看看英伟达行情")
- "看看黄金行情" -> analyze_market(symbols=["AU9999"], interval="1d", question="看看黄金行情")
- "它的止损呢" -> followup(symbol="BTC_USDT", interval="4h", followup_type="stop", question="它的止损呢")
- "精确2位小数" -> display_adjustment(precision=2)
- "看看 DOGE 行情" -> discover_asset(query_text="DOGE", hint_market="CRYPTO", question="看看 DOGE 行情")
"""


def _feishu_router_tool_definitions() -> list[dict[str, Any]]:
    """OpenAI-compatible tool list for chat/completions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "analyze_market",
                "description": "用户要行情分析：拉 K 线并生成技术结论。标的必须来自 tradable_assets。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "兼容旧字段：单标的代码，如 BTC_USDT、NVDA；内部会被折叠为 symbols=[symbol]，新输出应优先使用 symbols",
                        },
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标的代码列表；单标的也必须传单元素列表，如 [\"BTC_USDT\"]",
                        },
                        "interval": {
                            "type": "string",
                            "description": "K 线周期：15m、30m、1h、4h、1d",
                        },
                        "intervals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "多周期列表（如 ['4h','1h']）；单周期时仍用 interval 字段即可",
                        },
                        "provider": {
                            "type": "string",
                            "description": "tickflow | gateio | goldapi；须与标的表中一致，可省略由后端推断",
                        },
                        "question": {"type": "string", "description": "用户想问的简短中文"},
                        "with_research": {"type": "boolean", "description": "是否附带研报检索"},
                        "research_keyword": {
                            "type": "string",
                            "description": "研报检索关键词，可选",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_research",
                "description": "研报/机构观点/主题/概念/板块检索，支持关键词和可选 symbol。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "检索关键词，如行业、主题、概念、板块等，可为空（如只查 symbol）",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "可选，标的代码，如 BTC_USDT、NVDA",
                        },
                        "provider": {
                            "type": "string",
                            "description": "可选，数据源，如 yanbaoke、tickflow、gateio、goldapi",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_concept_board",
                "description": "查询标的所属概念/板块归属，支持 symbol 或关键词。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "可选，标的代码，如 BTC_USDT、NVDA",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "可选，概念/板块/主题关键词",
                        },
                        "provider": {
                            "type": "string",
                            "description": "可选，数据源，如 yanbaoke、market_data 等",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clarify_intent",
                "description": "当用户查询行情但缺少必要参数（如 K 线周期 interval）或者意图含糊时，向用户提问以澄清意图。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "clarify_message": {
                            "type": "string",
                            "description": "反问用户的文本消息，如：请问您需要看什么周期的 K 线？(15m/1h/4h/1d)",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "如果能识别出标的，请附带在此，以作暂存",
                        },
                    },
                    "required": ["clarify_message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reply_chat",
                "description": "寒暄、致谢、引导用户发起行情分析；用 message 写完整可读回复（可多段，可概括 tradable_assets 中的标的分类与示例问法）。回答尽量简短时不强行压缩：首访寒暄可把支持的资产类型与示例一句话列清。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "回复正文"},
                    },
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "view_sim_account",
                "description": "查看模拟账户状态：余额、持仓、挂单、成交、活动想法、对账统计。用户问「余额/资金/账户/持仓/订单/成交/仓位/模拟账户」时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "查询范围：overview（综合）、positions（持仓）、active_ideas（活动想法）、orders（委托）、fills（成交）、health（对账统计）",
                        },
                        "account_id": {
                            "type": "string",
                            "description": "可选，指定账户 ID 如 CNY/USD",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "可选，指定标的代码",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "followup",
                "description": "用户追问上一轮分析的具体方面（止损、入场、触发条件等）。从 conversation_context 获取上一轮标的/周期。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "followup_type": {
                            "type": "string",
                            "description": "追问类型：entry/stop/tp/risk_reward/status/rationale/general",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "上一轮的标的代码（从 conversation_context.last_symbols 获取）",
                        },
                        "interval": {
                            "type": "string",
                            "description": "上一轮的周期（从 conversation_context 获取）",
                        },
                        "question": {
                            "type": "string",
                            "description": "用户追问的原文",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "display_adjustment",
                "description": "用户要求调整展示格式（小数位数、简短/详细、重复上轮），不需要新分析。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "precision": {
                            "type": "integer",
                            "description": "保留几位小数（0-8）",
                        },
                        "compact": {
                            "type": "boolean",
                            "description": "是否要求简短展示",
                        },
                        "repeat": {
                            "type": "boolean",
                            "description": "是否要求重复上轮要点",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "discover_asset",
                "description": "用户提及不在 tradable_assets 中的金融资产，需先发现标的代码、市场和数据源再做分析。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_text": {
                            "type": "string",
                            "description": "用户原始查询文本",
                        },
                        "hint_market": {
                            "type": "string",
                            "description": "候选市场：CRYPTO/US/CN/PM",
                        },
                        "question": {
                            "type": "string",
                            "description": "用户意图简述",
                        },
                    },
                    "required": ["query_text"],
                },
            },
        },
    ]


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not (raw or "").strip():
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _dedupe_str_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _single_tool_call_to_routed_dict(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        raise LLMClientError("路由 tool_call 结构异常")
    fn = tool_call.get("function")
    if not isinstance(fn, dict):
        raise LLMClientError("路由 tool_call.function 缺失")
    name = str(fn.get("name") or "").strip()
    raw_args = str(fn.get("arguments") or "")
    args = _parse_tool_arguments(raw_args)

    if name == "analyze_market":
        sym = args.get("symbol")
        syms = args.get("symbols")
        out: dict[str, Any] = {"action": "analyze"}
        normalized_symbols: list[str] = []
        if isinstance(syms, list):
            normalized_symbols.extend(str(x).strip() for x in syms if str(x).strip())
        if isinstance(sym, str) and sym.strip():
            normalized_symbols.append(sym.strip())
        if normalized_symbols:
            out["symbols"] = _dedupe_str_list(normalized_symbols)
        iv = str(args.get("interval") or "").strip().lower()
        if iv:
            out["interval"] = iv
        ivs = args.get("intervals")
        if isinstance(ivs, list) and ivs:
            out["intervals"] = [str(x).strip().lower() for x in ivs if str(x).strip()]
        pv = str(args.get("provider") or "").strip().lower()
        if pv:
            out["provider"] = pv
        q = args.get("question")
        if isinstance(q, str) and q.strip():
            out["question"] = q.strip()
        if "with_research" in args:
            out["with_research"] = bool(args.get("with_research"))
        rk = args.get("research_keyword")
        if isinstance(rk, str) and rk.strip():
            out["research_keyword"] = rk.strip()
        if normalized_symbols:
            out["action"] = "analyze_multi"
        return out

    if name == "search_research":
        out: dict[str, Any] = {"action": "research"}
        kw = args.get("keyword")
        sym = args.get("symbol")
        pv = args.get("provider")
        if isinstance(kw, str) and kw.strip():
            out["keyword"] = kw.strip()
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        if isinstance(pv, str) and pv.strip():
            out["provider"] = pv.strip()
        return out

    if name == "query_concept_board":
        out: dict[str, Any] = {"action": "concept_board"}
        sym = args.get("symbol")
        kw = args.get("keyword")
        pv = args.get("provider")
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        if isinstance(kw, str) and kw.strip():
            out["keyword"] = kw.strip()
        if isinstance(pv, str) and pv.strip():
            out["provider"] = pv.strip()
        return out

    if name == "clarify_intent":
        msg = args.get("clarify_message")
        sym = args.get("symbol")
        out: dict[str, Any] = {"action": "clarify", "clarify_message": msg}
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        return out

    if name == "reply_chat":
        msg = args.get("message")
        if isinstance(msg, str) and msg.strip():
            return {"action": "chat", "chat_reply": msg.strip()}
        return {"action": "chat"}

    if name == "view_sim_account":
        out: dict[str, Any] = {"action": "sim_account"}
        scope = args.get("scope")
        if isinstance(scope, str) and scope.strip():
            out["scope"] = scope.strip()
        aid = args.get("account_id")
        if isinstance(aid, str) and aid.strip():
            out["account_id"] = aid.strip()
        sym = args.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        return out

    if name == "followup":
        out: dict[str, Any] = {"action": "followup"}
        ft = args.get("followup_type")
        if isinstance(ft, str) and ft.strip():
            out["followup_type"] = ft.strip()
        sym = args.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        iv = str(args.get("interval") or "").strip().lower()
        if iv:
            out["interval"] = iv
        q = args.get("question")
        if isinstance(q, str) and q.strip():
            out["question"] = q.strip()
        return out

    if name == "display_adjustment":
        out: dict[str, Any] = {"action": "display_adjustment"}
        p = args.get("precision")
        if isinstance(p, int):
            out["precision"] = p
        c = args.get("compact")
        if isinstance(c, bool):
            out["compact"] = c
        r = args.get("repeat")
        if isinstance(r, bool):
            out["repeat"] = r
        return out

    if name == "discover_asset":
        qt = args.get("query_text")
        out: dict[str, Any] = {"action": "discover_analyze"}
        if isinstance(qt, str) and qt.strip():
            out["query_text"] = qt.strip()
        hm = args.get("hint_market")
        if isinstance(hm, str) and hm.strip():
            out["hint_market"] = hm.strip()
        q = args.get("question")
        if isinstance(q, str) and q.strip():
            out["question"] = q.strip()
        return out

    raise LLMClientError(f"未知路由工具: {name!r}")


def _merge_tool_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    if not routes:
        raise LLMClientError("路由 tool_calls 为空")
    if len(routes) == 1:
        return routes[0]

    actionable = [dict(r) for r in routes if str(r.get("action") or "") != "chat"]
    if not actionable:
        return routes[0]

    # 优先级：followup / display_adjustment / discover_analyze 优先于 analyze
    priority_actions = {"followup", "display_adjustment", "discover_analyze"}
    priority_step = next((r for r in actionable if str(r.get("action") or "") in priority_actions), None)
    if priority_step:
        priority_step["plan_steps"] = [dict(step) for step in routes]
        return priority_step

    analyze_steps = [dict(r) for r in actionable if str(r.get("action") or "") == "analyze"]
    research_steps = [
        dict(r) for r in actionable
        if str(r.get("action") or "") in {"research", "concept_board"}
    ]

    if analyze_steps:
        merged = dict(analyze_steps[0])

        merged_symbols: list[str] = []
        for step in analyze_steps:
            symbols = step.get("symbols")
            if isinstance(symbols, list):
                merged_symbols.extend(str(item).strip() for item in symbols if str(item).strip())
        if merged_symbols:
            merged["symbols"] = _dedupe_str_list(merged_symbols)

        if research_steps or any(bool(step.get("with_research")) for step in analyze_steps):
            merged["with_research"] = True

        if not str(merged.get("research_keyword") or "").strip():
            candidate_keywords: list[str] = []
            for step in research_steps + analyze_steps:
                for key in ("keyword", "research_keyword", "symbol"):
                    value = str(step.get(key) or "").strip()
                    if value:
                        candidate_keywords.append(value)
                        break
            if candidate_keywords:
                merged["research_keyword"] = candidate_keywords[0]

        merged["plan_steps"] = [dict(step) for step in routes]
        return merged

    primary = dict(actionable[0])
    primary["plan_steps"] = [dict(step) for step in routes]
    return primary


def _tool_calls_to_routed_dict(tool_calls: Any) -> dict[str, Any]:
    """将 tool_calls 转为 route_user_message 所需的 dict（必要时做兼容性合并）。"""
    if not isinstance(tool_calls, list) or not tool_calls:
        raise LLMClientError("路由 tool_calls 为空")
    routes = [_single_tool_call_to_routed_dict(tc) for tc in tool_calls]
    return _merge_tool_routes(routes)


def _extract_router_assistant_text(message: dict[str, Any]) -> str:
    """从 chat/completions 的 assistant message 取出可读正文（兼容字符串或多段 content）。"""
    if not isinstance(message, dict):
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for p in c:
            if isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif isinstance(p.get("content"), str):
                    parts.append(p["content"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts).strip()
    return ""


def _post_json(url: str, payload: dict[str, Any], timeout_sec: float = 30.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
        },
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        snippet = (err_body or str(exc.reason or "")).strip()
        raise LLMClientError(f"LLM HTTP {exc.code}: {snippet[:2000]}") from exc
    except URLError as exc:
        raise LLMClientError(f"LLM 网络请求失败: {exc}") from exc
    except Exception as exc:
        raise LLMClientError(f"LLM 请求失败: {exc}") from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"LLM 返回非 JSON: {raw[:240]!r}") from exc
    if isinstance(obj, dict) and obj.get("error"):
        raise LLMClientError(f"LLM 返回错误: {obj.get('error')}")
    return obj if isinstance(obj, dict) else {"raw": obj}



def _build_route_context_hierarchy(conversation_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = dict(conversation_context or {})
    agent_ctx = ctx.get("agent_context") if isinstance(ctx.get("agent_context"), dict) else {}
    history_policy = str(
        agent_ctx.get("history_policy")
        or ctx.get("history_policy")
        or "minimal"
    ).strip().lower()
    return {
        "long_term": dict(agent_ctx.get("long_term") or {}),
        "short_term": dict(agent_ctx.get("short_term") or {}),
        "current_query": dict(agent_ctx.get("current_query") or {}),
        "history_policy": history_policy,
        "intent_confidence": float(agent_ctx.get("intent_confidence") or ctx.get("intent_confidence") or 0.0),
    }



def _build_feishu_route_payload(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None,
    tradable_assets: list[dict[str, Any]] | None,
    conversation_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    prompt_cfg = _intent_router_prompt_cfg()
    system_prompt = str(prompt_cfg.get("llm_router_system_prompt") or "").strip()
    if not system_prompt:
        system_prompt = DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT
    temperature = _resolved_temperature(float(prompt_cfg.get("llm_router_temperature") or 0.0))
    transcript = list(recent_messages or [])[-20:]
    conv_ctx = dict(conversation_context or {})
    hierarchy = _build_route_context_hierarchy(conv_ctx)
    history_policy = str(hierarchy.get("history_policy") or "minimal").strip().lower()
    prompt_obj: dict[str, Any] = {
        "user_message": text or "",
        "context_hierarchy": hierarchy,
        "conversation_transcript": transcript,
        "transcript_suppressed": history_policy in {"minimal", "none"},
        "conversation_context": conv_ctx,
        "policy_injection": {
            "default_symbol": default_symbol,
            "default_interval": default_interval,
            "tradable_assets": list(tradable_assets or []),
        },
    }
    url = f"{_base_url()}/chat/completions"
    system_with_hint = system_prompt + _feishu_router_interval_instruction() + _feishu_router_context_hierarchy_instruction()
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_with_hint},
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
        "tools": _feishu_router_tool_definitions(),
        "tool_choice": "auto",
    }
    return url, base_payload


def _feishu_completion_response_to_route(res: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = res["choices"][0]["message"]
    except Exception as exc:
        raise LLMClientError(f"LLM 路由(tool)响应结构异常: {res}") from exc
    if not isinstance(msg, dict):
        raise LLMClientError(f"LLM 路由 message 非对象: {msg!r}")
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        return _tool_calls_to_routed_dict(tool_calls)
    raw_text = _extract_router_assistant_text(msg)
    if raw_text:
        return {"action": "chat", "chat_reply": raw_text}
    raise LLMClientError("LLM 路由未返回 tool_calls，且无 assistant 正文")


def decide_feishu_route(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """飞书路由：OpenAI-compatible chat/completions + tools；优先 tool_calls；无 tool_calls 时若有 assistant 正文则视为闲聊（action=chat）。

    注：实际调用 provider 由 runtime config 的 llm.default_provider 决定。
    """
    url, payload = _build_feishu_route_payload(
        text=text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        recent_messages=recent_messages,
        tradable_assets=tradable_assets,
        conversation_context=conversation_context,
    )
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    return _feishu_completion_response_to_route(res)


def generate_general_chat_reply(
    *,
    text: str,
    recent_messages: list[dict[str, str]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> str:
    transcript = list(recent_messages or [])[-12:]
    prompt_obj = {
        "user_message": text or "",
        "conversation_transcript": transcript,
        "conversation_context": dict(conversation_context or {}),
        "constraints": [
            "这是普通聊天模式，不要把用户强行导向金融分析。",
            "正常回答笑话、诗句、日常问题、泛化问答。",
            "除非用户主动转向投资/行情，否则不要提标的、周期、研报、配置文件。",
            "保持自然、友好、简洁，使用简体中文。",
        ],
    }
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(0.7),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个自然、友好的中文聊天助手。"
                    "当前任务不是金融分析，而是承接用户的普通闲聊、玩笑、诗句、泛化问题。"
                    "不要使用金融系统口吻，不要说分析失败，不要要求用户补标的或周期。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
    try:
        msg = res["choices"][0]["message"]
    except Exception as exc:
        raise LLMClientError(f"普通聊天响应结构异常: {res}") from exc
    text_out = _extract_router_assistant_text(msg)
    if text_out:
        return text_out
    raise LLMClientError("普通聊天未返回正文")


_CONTEXT_REPLY_SYSTEM_PROMPT = (
    "你是金融分析助手。对话历史包含了你之前与用户的全部交互。\n"
    "根据对话历史中的上下文，判断用户的意图并回复：\n"
    "- 如果历史中有相关分析（趋势、关键位、entry/stop），用户追问执行类问题（「适合买入吗」「怎么做空」）"
    " → 极短四段回复，总字数≤180字。四段标题用 **加粗**：\n"
    "  1. **结论**：直接答「可以/不建议/需等待触发」（≤30字）\n"
    "  2. **理由**：最多 2 条 bullet，引用之前分析中的趋势/关键位/触发条件\n"
    "  3. **风险**：1 条失效条件或方向冲突\n"
    "  4. **建议**：观察/等待/小仓试探（不得给具体手数；triggered≠成交）\n"
    "- 如果历史中有相关分析，用户追问单点问题（「止损呢」「触发了吗」）"
    " → 中等篇幅（80～150字），只回答指向的单点\n"
    "- 如果历史中没有相关分析，用户问执行类问题 → 回复「我还没有分析过这只股票，要先看看行情吗？」\n"
    "- 如果是普通闲聊 → 自然友好地回复，不要强行导向金融分析\n"
    "- 如果用户要求换个格式/精度重述 → 按要求重述历史中已有的分析内容\n\n"
    "通用规则：\n"
    "- 只引用对话历史中已有的内容，不要编造技术数据\n"
    "- 关键数值用 **加粗** 突出\n"
    "- 禁止输出编程字段名或英文键名\n"
    "- 禁止具体手数或「已下单」口径\n"
    "- neutral/方向不清时写「需等待触发」，不强推开仓\n"
    "- 文末：仅供技术分析与程序化演示，不构成投资建议。不要代码围栏。"
)


def generate_context_reply(
    *,
    user_question: str,
    conversation_history: list[dict[str, str]] | None = None,
    session_context: dict[str, Any] | None = None,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    """统一上下文回复：LLM 从对话历史理解上下文，自行判断回复类型。
    替代 generate_general_chat_reply + followup 磁盘读取路径。"""

    history = list(conversation_history or [])[-8:]

    prompt_obj: dict[str, Any] = {
        "user_question": str(user_question or "").strip(),
        "conversation_history": history,
        "session_context": dict(session_context or {}),
    }

    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    wt = agent.get("writer_temperature", 0.35) if isinstance(agent, dict) else 0.35
    if wt is None:
        wt = 0.45
    temperature = _resolved_temperature(float(wt))
    model = str(agent.get("writer_model") or "").strip() or _model_name()

    payload: dict[str, Any] = {
        "model": model,
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": _CONTEXT_REPLY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }

    res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"context_reply 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"context_reply content 非字符串: {content!r}")
    text = content.strip()
    if not text:
        raise LLMClientError("context_reply 返回空正文")
    return {"text": text, "sections": [{"title": "正文", "content": text}], "style": "quick"}


def discover_market_targets(
    *,
    text: str,
    recent_messages: list[dict[str, str]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    known_assets = []
    for asset in list(tradable_assets or [])[:80]:
        if not isinstance(asset, dict):
            continue
        known_assets.append(
            {
                "symbol": asset.get("symbol"),
                "name": asset.get("name"),
                "market": asset.get("market"),
                "provider": asset.get("provider"),
                "aliases": list(asset.get("aliases") or [])[:4],
            }
        )
    prompt_obj = {
        "user_message": text or "",
        "conversation_transcript": list(recent_messages or [])[-10:],
        "conversation_context": dict(conversation_context or {}),
        "known_assets": known_assets,
        "requirements": [
            "判断这是不是金融市场相关请求。",
            "若是，请尽可能给出 1 到 3 个候选资产。",
            "候选字段必须包含 symbol、market、provider、name、confidence。",
            "provider 仅允许 tickflow、gateio、goldapi。",
            "market 建议使用 CN、US、CRYPTO、PM。",
            "如果像是新币种或未配置股票，也可以给出候选。",
            "如果不确定，请降低 confidence，不要编造高置信度。",
            "整个回复必须是 JSON 对象。",
        ],
    }
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(0.1),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是金融资产发现器。你负责从自然语言中识别股票、贵金属、加密货币，并推断标准 symbol。"
                    "优先复用已知资产；若用户提到未配置的新资产，也可以输出候选 symbol / market / provider。"
                    "输出 JSON，格式为 {\"domain\":\"finance|chat|unknown\",\"candidates\":[...],\"reason\":\"...\"}."
                    + _JSON_OBJECT_SYSTEM_SUFFIX
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
    except LLMClientError as err:
        if "HTTP 400" in str(err):
            payload.pop("response_format", None)
            res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
        else:
            raise
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"资产发现响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"资产发现 content 非字符串: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"资产发现返回非 JSON: {content[:240]!r}") from exc
    if not isinstance(parsed, dict):
        raise LLMClientError(f"资产发现返回 JSON 非对象: {parsed!r}")
    return parsed


def feishu_route_deepseek_raw_and_routed(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
    timeout_sec: float = 30.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """真实调用 LLM：返回 (chat/completions 完整 JSON, 路由解析 dict)。供调试脚本打印原始响应。

    注：函数名保留 deepseek 是历史兼容，实际调用 provider 由 runtime config 决定。
    """
    url, payload = _build_feishu_route_payload(
        text=text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        recent_messages=recent_messages,
        tradable_assets=tradable_assets,
        conversation_context=None,
    )
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    return res, _feishu_completion_response_to_route(res)


def generate_decision(
    *,
    symbol: str,
    interval: str,
    question: str | None,
    technical_snapshot: dict[str, Any],
    evidence_sources: list[dict[str, Any]],
    temperature: float = 0.2,
) -> dict[str, Any]:
    bj = now_beijing_str()
    review_example = default_review_time_for_interval(interval)
    prompt_obj = {
        "symbol": symbol,
        "interval": interval,
        "question": question or "",
        "current_time_beijing": bj,
        "technical_snapshot": technical_snapshot,
        "evidence_sources": evidence_sources[:8],
        "constraints": [
            "只依据提供的技术快照与证据，不编造成交、资金流、未提供的价格。",
            "输出必须是 JSON 对象。",
            "必须输出字段: 综合倾向,关键位(Fib),触发条件,失效条件,风险点,下次复核时间。",
            "风险点必须是数组；其余字段用简洁中文。",
            f"当前北京时间（UTC+8）为 {bj}；字段「下次复核时间」必须写具体日期与时刻（北京时间 UTC+8），"
            f"格式与本轮 interval 对齐，示例：{review_example}。禁止仅用「下一根收盘后」等无时间点的模糊句。",
        ],
    }
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(float(temperature)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是交易分析Agent。你只能基于输入证据给出技术结论，"
                    "禁止杜撰成交、主力资金或官方未提供数据。"
                    + _JSON_OBJECT_SYSTEM_SUFFIX
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    url = f"{_base_url()}/chat/completions"
    try:
        res = _post_json(url, {**base_payload, "response_format": {"type": "json_object"}}, timeout_sec=120.0)
    except LLMClientError as err:
        if "HTTP 400" in str(err):
            res = _post_json(url, base_payload, timeout_sec=120.0)
        else:
            raise
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM content 非字符串: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"LLM content 不是 JSON: {content[:240]!r}") from exc
    if not isinstance(parsed, dict):
        raise LLMClientError(f"LLM content JSON 非对象: {parsed!r}")
    return parsed


# 飞书：将已锁事实 JSON 写作结构化正文（不得 invent 价位；与 guardrails 禁止口径一致）
DEFAULT_FEISHU_NARRATIVE_SYSTEM = (
    "你是行情分析撰稿人。用户 JSON 中 facts 为程序算好的事实快照（含 fixed_template、均线、威科夫 123 等）。\n"
    "要求：\n"
    "1) 先结论后依据；不写「保证盈利」类表述。\n"
    "2) 只使用 facts 中已出现的数字、区间与条件；禁止编造价格、成交状态或「已可下单」类结论。\n"
    "3) 禁止输出编程字段名（如 triggered、preferred_side、entry=None、aligned）；用自然中文替代。\n"
    "4) 123 结构只按 P1/P2/P3、entry、stop、tp1/tp2 解读；未触发写「待触发」，不得写成「已入场」；观察位 ≠ 入场位。\n"
    "5) neutral 或方向不清时写「无明显方向」，只给观察位与等待条件，不强推开仓方案。\n"
    "6) 多标的时先逐标的再跨标的总结；每标的需要：综合倾向、关键位（含 Fib）、触发/失效条件、风险点。\n"
    "7) 禁止口径：已成交、成交回报、主力资金净流入、交易所逐笔资金流。\n"
    "8) 文末一句免责：仅供技术分析与程序化演示，不构成投资建议。\n"
    "写法：自然分段，关键数值用 **加粗** 突出，段间空行分隔。不要代码围栏。"
)


def generate_feishu_narrative(
    *,
    facts: dict[str, Any],
    user_question: str | None = None,
    timeout_sec: float = 120.0,
) -> str:
    """基于工具锁事实生成飞书可读长文；不负责拉行情。"""
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    wt = agent.get("writer_temperature", 0.35) if isinstance(agent, dict) else 0.35
    if wt is None:
        wt = float(fei.get("narrative_temperature", 0.35))
    temperature = _resolved_temperature(float(wt))
    custom = str(agent.get("legacy_narrative_system_prompt") or fei.get("narrative_system_prompt") or "").strip()
    system_prompt = custom if custom else DEFAULT_FEISHU_NARRATIVE_SYSTEM
    user_obj: dict[str, Any] = {"facts": facts}
    if user_question and str(user_question).strip():
        user_obj["user_question"] = str(user_question).strip()
    url = f"{_base_url()}/chat/completions"
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM 叙事响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM 叙事 content 非字符串: {content!r}")
    text = content.strip()
    if not text:
        raise LLMClientError("LLM 叙事返回空正文")
    return text


def _format_display_preferences_hint(dp: dict[str, Any]) -> str:
    parts: list[str] = []
    p = dp.get("precision")
    if isinstance(p, int) and p >= 0:
        parts.append(f"数值展示：金额与数量统一保留 {p} 位小数（四舍五入），以可读为先。")
    if dp.get("compact"):
        parts.append("篇幅：尽量简短，列表式要点即可。")
    if dp.get("detailed"):
        parts.append("篇幅：可适当展开说明，仍不得编造未提供的数据。")
    if dp.get("repeat"):
        parts.append("用户要求重复上一轮要点：在事实不变前提下简要复述。")
    return "\n".join(parts)


GROUNDED_WRITER_SYSTEM_BY_MODE: dict[str, str] = {
    "quick": (
        "你是金融简报撰稿人。用户 JSON 内含 task_type、response_mode 与 facts_bundle。\n"
        "要求：\n"
        "1) 只引用 facts_bundle 中出现的数值与中文描述；禁止编造价格、成交或资金流。\n"
        "2) 回答要短（现价类几句话即可），禁止输出代码字段名或英文键名（如 triggered、preferred_side、entry=None、aligned）。\n"
        "3) 禁止口径：已成交、成交回报、主力资金净流入、交易所逐笔资金流。\n"
        "4) 文末一句：仅供技术分析与程序化演示，不构成投资建议。\n"
        "写法：先结论后依据，关键数值用 **加粗** 突出，段间空行分隔。不要代码围栏。"
    ),
    "compare": (
        "你是多资产对比撰稿人。依据 facts_bundle 中的多标的事实（含 compare_facts.rows）做排序或强弱判断说明。\n"
        "只使用已给出的价格、趋势、共振等字段；禁止编造；禁止输出编程字段名；禁止具体下单指令。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "写法：先结论后依据，关键数值用 **加粗** 突出，段间空行分隔。不要代码围栏。"
    ),
    "analysis": (
        "你是行情分析撰稿人。facts_bundle.market_facts.analysis_facts 为程序算好的技术快照（含 fixed_template、均线、威科夫摘要）。\n"
        "先结论后依据；不得编造未出现的数据；禁止将编程字段名原样输出给用户。\n"
        "123 结构只按 P1/P2/P3、entry、stop、tp1/tp2 解读；未触发写「待触发」，不得写成「已入场」；观察位 ≠ 入场位。\n"
        "neutral 或方向不清时写「无明显方向」，只给观察位与等待条件，不强推开仓。\n"
        "多标的时先逐标的再跨标的总结；每标的需要：综合倾向、关键位（含 Fib）、触发/失效条件、风险点。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "写法：自然分段，关键数值用 **加粗** 突出，段间空行分隔。不要代码围栏。"
    ),
    "narrative": (
        "你是研报线索撰稿人。facts_bundle.research_facts 为检索摘要，不得写成「已验证价格触发」或交易指令。\n"
        "不写具体 entry/stop/tp；不编造机构已确认成交；可列观点分歧与需二次验证之处。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "写法：先结论后依据；小标题单独成行用 **加粗**；列表前空一行，列表项以 `- ` 开头；段间空行分隔。不要代码围栏与 # 标题。"
    ),
    "sim_account": (
        "你是模拟账户数据播报员。facts_bundle.sim_account_facts 为程序查询结果（含 metrics/tables/summary），"
        "只使用其中已出现的数字与账户字段；禁止编造成交回报、主力资金、交易所逐笔资金流。\n"
        "将余额、持仓、委托、成交等信息用自然中文分段说明；勿输出英文键名或 JSON。\n"
        "若用户要求小数位数或简短/详细，严格按 user JSON 中的 display_preferences 执行。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "写法：先结论后依据，关键数值用 **加粗** 突出，段间空行分隔。不要代码围栏。"
    ),
    "followup": (
        "你是技术追问撰稿人。facts_bundle.followup_facts.overview 为上一轮分析的结构快照（含 stats、wyckoff_123_v1）。\n"
        "只回答 user_question 指向的单点（止损/入场/触发/状态/理由等）；禁止重复完整行情分析或多标的总结。\n"
        "123 结构只按 entry/stop/tp1/tp2、triggered 解读；未触发写「待触发」，不得写成「已入场」；triggered≠成交。\n"
        "禁止输出编程字段名或英文键名；禁止具体手数或「已下单」口径。\n"
        "篇幅中等（通常 80～150 字）；先结论后依据，关键数值用 **加粗** 突出。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。不要代码围栏。"
    ),
    "followup_execution": (
        "你是执行可行性审稿人。facts_bundle.followup_facts.overview 为技术快照；只引用其中已出现的字段。\n"
        "用户问的是「能否开多/开空/现在买」类执行问题；必须极短回答，总字数不超过 180 字。\n"
        "强制四段（每段标题用 **加粗**）：\n"
        "1. **结论**：直接答「可以/不建议/需等待触发」（一句话，≤30字）\n"
        "2. **理由**：最多 2 条 bullet，引用 triggered/entry/stop/trend/regime\n"
        "3. **风险**：1 条失效条件或方向冲突\n"
        "4. **建议**：观察/等待/小仓试探（不得给具体手数；triggered≠成交）\n"
        "neutral 或方向不清时必须写「需等待触发」，不强推开仓。\n"
        "禁止重复上一轮完整分析；禁止 JSON 键名。\n"
        "文末一句：仅供技术分析与程序化演示，不构成投资建议。不要代码围栏。"
    ),
}


def _resolve_writer_mode_key(*, task_type: str, response_mode: str, facts_bundle: dict) -> str:
    tt = str(task_type or "").strip().lower()
    rm = str(response_mode or "").strip().lower()
    ft = str(facts_bundle.get("followup_type") or "").strip().lower()
    if tt == "followup" and ft == "execution":
        return "followup_execution"
    if rm == "followup_execution":
        return "followup_execution"
    if tt == "followup" or rm == "followup":
        return "followup"
    if rm in GROUNDED_WRITER_SYSTEM_BY_MODE:
        return rm
    if tt == "sim_account":
        return "sim_account"
    return "analysis"


def generate_grounded_answer(
    *,
    facts_bundle: dict[str, Any],
    user_question: str | None,
    task_type: str,
    response_mode: str,
    display_preferences: dict[str, Any] | None = None,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    """基于 facts_bundle 的 grounded 撰稿；返回 text/sections/style。"""
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    wt = agent.get("writer_temperature", 0.35) if isinstance(agent, dict) else 0.35
    if wt is None:
        wt = float(fei.get("narrative_temperature", 0.35))
    temperature = _resolved_temperature(float(wt))
    custom = str(agent.get("writer_system_prompt") or "").strip()
    mode_key = _resolve_writer_mode_key(
        task_type=task_type,
        response_mode=response_mode,
        facts_bundle=facts_bundle if isinstance(facts_bundle, dict) else {},
    )
    system_prompt = custom if custom else GROUNDED_WRITER_SYSTEM_BY_MODE[mode_key]
    model = str(agent.get("writer_model") or "").strip() or _model_name()
    user_obj: dict[str, Any] = {
        "task_type": task_type,
        "response_mode": response_mode,
        "facts_bundle": facts_bundle,
    }
    ft = str(facts_bundle.get("followup_type") or "").strip() if isinstance(facts_bundle, dict) else ""
    if ft:
        user_obj["followup_type"] = ft
    if display_preferences and isinstance(display_preferences, dict) and display_preferences:
        user_obj["display_preferences"] = display_preferences
        dp_hint = _format_display_preferences_hint(display_preferences)
        if dp_hint:
            system_prompt = system_prompt + "\n\n" + dp_hint
    if user_question and str(user_question).strip():
        user_obj["user_question"] = str(user_question).strip()
    url = f"{_base_url()}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM grounded 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM grounded content 非字符串: {content!r}")
    text = content.strip()
    if not text:
        raise LLMClientError("LLM grounded 返回空正文")
    return {"text": text, "sections": [{"title": "正文", "content": text}], "style": response_mode}


def generate_session_compact_summary(
    *,
    history: list[dict[str, Any]],
    prior_summary: str | None = None,
    timeout_sec: float = 45.0,
) -> str:
    """会话压缩摘要：只提取意图/标的/决策，禁止编造价格事实。"""
    transcript = []
    for item in history[-80:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            transcript.append({"role": role, "text": text[:500]})
    prompt_obj = {
        "prior_summary": (prior_summary or "").strip() or None,
        "conversation_transcript": transcript,
        "constraints": [
            "只提取用户意图、提到的标的、关键决策与用户偏好。",
            "不得编造价格、触发条件、entry/stop/tp 等交易事实。",
            "不得把聊天内容表述为已验证行情或机构结论。",
            "使用简体中文，200-400 字以内。",
        ],
    }
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(0.2),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是会话摘要助手。你的任务是为后续路由提供语境，"
                    "只总结对话中明确出现的意图与标的线索，绝不编造金融事实。"
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
    try:
        msg = res["choices"][0]["message"]
    except Exception as exc:
        raise LLMClientError(f"会话压缩响应结构异常: {res}") from exc
    text_out = _extract_router_assistant_text(msg)
    if text_out:
        return text_out.strip()
    raise LLMClientError("会话压缩未返回正文")


_DEFAULT_PRE_JUDGE_SYSTEM_PROMPT = """你是一个专业、高效的交易分析 Agent 的意图预判器（Intent Pre-Judge）。

你的任务是：仅根据用户最新一条输入 + 少量必要历史摘要，快速准确判断本次查询的核心意图。

### 输出要求
必须返回严格的 JSON 对象，不要添加任何解释、markdown 代码块或额外文字。

字段 schema：
{
  "intent_type": "new_analysis | followup | execution_question | knowledge_chat | research | clarification | general_chat",
  "confidence": 0.88,
  "reasoning": "一句话简要说明判断依据",
  "mentioned_symbols": ["ETH_USDT", "BTC_USDT"],
  "suggested_interval": "1h",
  "history_policy": "minimal | recent_4 | full",
  "needs_clarification": false,
  "is_research_intent": false,
  "research_keyword": null,
  "research_keywords": []
}

### 意图定义（严格遵守）
- new_analysis：用户想要新的行情/技术分析。典型词：看看、查一下、分析、现在行情、目前走势、短线如何等 + 明确或隐含标的。
- execution_question：用户询问交易决策（能不能开、是否推荐、止损在哪里、开空/开多可以吗等）。
- followup：普通追问、延续上一轮讨论（刚才、那个、然后呢、止盈呢、再说一遍等）。
- knowledge_chat：询问概念、原理、方法论（什么是量化分析法、MACD 怎么用、了解…吗等）。
- research：用户想看研报/机构观点/板块叙事/行业配置线索（含「用研报工具」「查研报」「XX板块/概念/行业观点」等）；须同时给出 research_keyword 与 research_keywords（1～3 个简洁检索词）。
- clarification：需要澄清（哪个标的、什么周期等）。
- general_chat：闲聊、感谢、寒暄等。

### 判断规则（优先级从高到低）
1. 用户输入含「看看」「查一下」「现在」「目前」「分析一下」等，且没有明显追问词（刚才、那个、接着、然后、它的）→ 强烈倾向 new_analysis。
2. 用户明确询问「可以开空吗」「能买吗」「推荐吗」「止损在哪里」等 → execution_question（若 short_term 有 recent_analyses 或明显承接上一轮，confidence 应更高）。
3. 明显知识性问题（「了解」「什么是」「怎么看」「原理」「怎么用」）且无新分析触发词 dominating → knowledge_chat。
4. 混合句（如「了解量化分析法吗我想看看」）：若明确要看研报/机构观点/板块线索 → research；若主体是概念/方法论且无研报信号 → knowledge_chat；若「看看/查一下」指向行情/K线 → new_analysis。
5. 历史很长（recent_analyses 多条）且无明确新分析信号时，倾向 followup。

### research 字段规则
- intent_type=research 时 is_research_intent=true，research_keywords 必填（最多 3 个）；research_keyword 取第一个。
- 多主题用数组拆分（如「芯片、电力」→ ["芯片","电力"]）；去除「用研报工具看看」「帮我」等前缀。
- 非 research 意图时 is_research_intent=false，research_keyword/research_keywords 可为 null/[]。

### history_policy 建议
- new_analysis / research → minimal（尽量少给历史，减少污染）
- execution_question / followup → recent_4
- knowledge_chat / general_chat / clarification → minimal
- confidence < 0.6 时一律 minimal

### 重要约束
- 永远不要编造价格、触发条件、买卖建议；你只做意图判断。
- 优先考虑用户最新输入的字面意图，历史摘要仅供参考。
- confidence 要客观：非常明确时 ≥0.85，模糊时 0.6~0.8。
- suggested_interval 仅 15m/30m/1h/4h/1d；无法判断时返回 null（JSON null）。"""


_DEFAULT_RESEARCH_KEYWORD_SYSTEM_PROMPT = """你是一个精准的研报关键词提取器。

用户会说一些自然语言，请提取最适合用于研报搜索的核心关键词（板块、概念、行业、主题、方法论名称等）。

输出严格 JSON，不要解释、不要 markdown 代码块：

{
  "keyword": "主搜索词，尽量简洁",
  "keywords": ["词1", "词2"],
  "confidence": 0.85,
  "reasoning": "一句话说明提取依据",
  "is_research_intent": true
}

规则：
- 用户明确想看研报/机构观点/板块叙事/行业分析 → is_research_intent=true
- keywords 最多 3 个；多主题拆分（「芯片、电力」→ ["芯片","电力"]）
- 去除前缀如「用研报工具看看」「帮我」「请问」等
- 纯闲聊或无明显研报意图 → is_research_intent=false，keywords=[]
- 不要编造未出现的实体；keyword 取 keywords 第一个"""


def _research_keyword_system_prompt() -> str:
    from tools.legacy_bridge import load_agent_runtime_config

    custom = str(load_agent_runtime_config().research_keyword_system_prompt or "").strip()
    return custom or _DEFAULT_RESEARCH_KEYWORD_SYSTEM_PROMPT


def extract_research_keyword(
    text: str,
    *,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """LLM 智能提取研报搜索关键词；失败由调用方降级。"""
    from tools.legacy_bridge import load_agent_runtime_config

    rt = load_agent_runtime_config()
    t_sec = float(timeout_sec if timeout_sec is not None else rt.research_keyword_timeout_sec)
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(float(rt.research_keyword_temperature)),
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _research_keyword_system_prompt()},
            {"role": "user", "content": f"用户输入：{(text or '').strip()}"},
        ],
    }
    res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=t_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"Research keyword 响应结构异常: {res}") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMClientError("Research keyword 返回空正文")
    try:
        obj = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"Research keyword 非 JSON: {content[:240]!r}") from exc
    if not isinstance(obj, dict):
        raise LLMClientError("Research keyword 返回非对象 JSON")
    return obj


def _normalize_pre_judge_research_fields(obj: dict[str, Any]) -> dict[str, Any]:
    from tools.legacy_bridge import normalize_research_keywords

    is_research = bool(obj.get("is_research_intent", False))
    intent = str(obj.get("intent_type") or "").strip().lower()
    if intent == "research":
        is_research = True
    raw_kws = obj.get("research_keywords")
    keywords: list[str] = []
    if isinstance(raw_kws, list):
        keywords = normalize_research_keywords([str(x).strip() for x in raw_kws if str(x).strip()])
    primary = str(obj.get("research_keyword") or "").strip()
    if primary and primary not in keywords:
        keywords = normalize_research_keywords([primary, *keywords])
    elif not keywords and primary:
        keywords = [primary]
    kw = keywords[0] if keywords else ""
    return {
        "is_research_intent": is_research and bool(keywords),
        "research_keyword": kw or None,
        "research_keywords": keywords,
    }



def _pre_judge_system_prompt() -> str:
    from tools.legacy_bridge import load_agent_runtime_config

    custom = str(load_agent_runtime_config().pre_judge_system_prompt or "").strip()
    return custom or _DEFAULT_PRE_JUDGE_SYSTEM_PROMPT



def _build_pre_judge_user_payload(
    *,
    text: str,
    long_term: dict[str, Any],
    short_term: dict[str, Any],
) -> dict[str, Any]:
    """构造 Pre-Judge user JSON：最新输入 + 少量 short_term 摘要。"""
    summary_lines: list[str] = []
    compacted = short_term.get("compacted_summary") if isinstance(short_term.get("compacted_summary"), str) else ""
    if compacted.strip():
        summary_lines.append(compacted.strip())
    for entry in list(short_term.get("recent_analyses") or [])[:4]:
        if not isinstance(entry, dict):
            continue
        sym = entry.get("symbol") or ((entry.get("symbols") or [None])[0])
        iv = entry.get("interval") or ""
        q = str(entry.get("question") or "").strip()[:100]
        bit = " ".join(x for x in [str(sym or "").strip(), str(iv).strip(), q] if x)
        if bit:
            summary_lines.append(f"- {bit}")
    short_summary = "\n".join(summary_lines) if summary_lines else "（无）"
    lt = dict(long_term or {})
    return {
        "user_latest_input": str(text or "").strip(),
        "short_term_summary": short_summary,
        "long_term_hints": {
            k: lt.get(k)
            for k in ("preferred_symbols", "default_interval", "risk_profile", "trading_style")
            if lt.get(k) is not None
        },
    }



def _normalize_pre_judge_interval(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in {"null", "none", "n/a", "unknown"}:
        return None
    if s in {"15m", "30m", "1h", "4h", "1d"}:
        return s
    return None


def pre_judge_query_intent(
    *,
    text: str,
    long_term: dict[str, Any],
    short_term: dict[str, Any],
    timeout_sec: float = 12.0,
) -> dict[str, Any]:
    """轻量意图预判（P2）；返回 intent_type / confidence / history_policy 等。"""
    prompt_obj = _build_pre_judge_user_payload(
        text=text,
        long_term=long_term if isinstance(long_term, dict) else {},
        short_term=short_term if isinstance(short_term, dict) else {},
    )
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(0.1),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _pre_judge_system_prompt()},
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(f"{_base_url()}/chat/completions", payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"Pre-Judge 响应结构异常: {res}") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMClientError("Pre-Judge 返回空正文")
    try:
        obj = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"Pre-Judge 非 JSON: {content[:240]!r}") from exc
    if not isinstance(obj, dict):
        raise LLMClientError("Pre-Judge 返回非对象 JSON")
    out = {
        "intent_type": str(obj.get("intent_type") or "unknown").strip(),
        "confidence": float(obj.get("confidence") or 0.7),
        "reasoning": str(obj.get("reasoning") or "").strip(),
        "mentioned_symbols": list(obj.get("mentioned_symbols") or []),
        "suggested_interval": _normalize_pre_judge_interval(obj.get("suggested_interval")),
        "history_policy": str(obj.get("history_policy") or "minimal").strip(),
        "needs_clarification": bool(obj.get("needs_clarification")),
    }
    out.update(_normalize_pre_judge_research_fields(obj))
    return out