#!/usr/bin/env python3
"""Response style smoke test — 不经过飞书，直接调用 ConversationService。

用法:
    python scripts/smoke_response_style.py           # 真实 LLM
    python scripts/smoke_response_style.py --mock    # 跳过 LLM，用内置样例验证检查器

输出: ~/.marketassagent/output/response_style_smoke.md（可用 --output 覆盖）
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.runtime_paths import get_output_dir


MAX_CN_CHARS = 1200
MAX_TABLE_LINES = 8
MAX_TABLE_LINES_DETAIL = 40  # 「详细分析」类允许多品种分节表格

TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "multi_detail",
        "input": "帮我详细分析一下 eth sol 和黄金",
        "multi_symbol": True,
        "detail_analysis": True,
    },
    {
        "id": "btc_trade_plan",
        "input": "BTC 现在能不能开多，给我入场止损止盈",
        "multi_symbol": False,
    },
    {
        "id": "sol_eth_compare",
        "input": "SOL 和 ETH 哪个更强",
        "multi_symbol": True,
    },
    {
        "id": "gold_short",
        "input": "黄金还能空吗",
        "multi_symbol": False,
    },
    {
        "id": "preference_note",
        "input": "我现在偏多，以后按短线风格看",
        "multi_symbol": False,
        "profile_case": True,
    },
]

# --mock 模式样例：符合当前 prompt 风格要求
MOCK_RESPONSES: dict[str, str] = {
    "multi_detail": """【总判断】
ETH、SOL 与黄金当前结构分化：ETH/SOL 偏震荡，黄金日线偏空但未确认破位。

【关键点】
- ETHUSDT（4h）：均线纠缠，关键阻力需放量突破方可转强
- SOLUSDT（4h）：跟随 ETH，波动略大
- 黄金（1d）：若跌破近期支撑，则空头延续概率上升

【我的建议】
若 ETH 4h 收盘站稳 MA21 上方且放量，则可考虑轻仓试多；否则以观望为主。黄金空单需等待反弹至阻力区再评估。

> **风险提示**：以上基于当前结构推演，未考虑突发消息与流动性变化；任何入场需设止损，单仓不宜过重。""",
    "btc_trade_plan": """【方向判断】
BTC 当前结构中性偏多，但尚未给出明确突破确认。

【入场条件】
若 4h 收盘站稳关键阻力且量能配合，则可考虑分批试多。

【止损止盈】
- 止损：跌破近期回踩低点
- 止盈：第一目标前高附近，第二目标延伸位需边走边看

> **风险提示**：杠杆与隔夜风险需自控；若宏观数据超预期波动，应优先减仓而非加仓。""",
    "sol_eth_compare": """【总判断】
短线相对强度：ETH 结构略稳，SOL 弹性更大但回撤也更深。

【差异】
- ETH：均线排列更整齐，关键位更清晰
- SOL：若 BTC 走强，SOL 往往放大波动

【我的建议】
若追求相对稳健，可优先观察 ETH；若接受更高波动且趋势确认，再评估 SOL。

> **风险提示**：对比结论随行情快速变化，需结合仓位与止损，不宜机械套用。""",
    "gold_short": """【行情结论】
黄金日线仍处偏空结构，但接近支撑区，追空性价比下降。

【我的建议】
若价格反弹至阻力区且出现滞涨信号，则可考虑轻仓试空；若支撑有效反弹，则应放弃追空思路。

> **风险提示**：黄金受汇率与宏观数据影响大，止损必须前置，避免在数据公布前重仓。""",
    "preference_note": """已记录您的偏好：当前偏多、后续按短线风格观察。

后续给交易计划前，我会结合您的风险偏好与周期偏好做条件化建议。

> **风险提示**：风格偏好会随市场变化调整，任何建议仍需您自行确认仓位与止损。""",
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    user_input: str
    reply: str
    checks: list[CheckResult] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)


def _count_chinese_chars(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _count_table_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"^\|.+\|$", line.strip()))


def _has_conditional_expression(text: str) -> bool:
    """条件化表达：若/则、表格条件列、需先/等待/突破后再等。"""
    patterns = [
        r"若.{1,60}则",
        r"如果.{1,60}则",
        r"若.{1,60}可",
        r"当.{1,60}时",
        r"需先.{1,40}",
        r"等待.{1,40}(后|再)",
        r"优先.{1,40}",
        # 表格「条件」列或条件化小节
        r"\|\s*条件\s*\|",
        r"入场条件",
        r"失效条件",
        r"条件化",
        # 动作链：突破/回踩/站稳/跌破 … 再/方可/才
        r"突破.{1,40}再",
        r"回踩.{1,40}再",
        r"站稳.{1,40}再",
        r"跌破.{1,40}(再|后)",
        r"受阻.{1,30}(可|则|再|后)",
        r".{1,30}方可",
        r"才考虑",
        r"再评估",
        r"再.{0,6}(空|多|做|进|加)",
        # 否定/暂缓类条件
        r"不宜.{1,30}",
        r"不建议",
        r"不是.{0,6}入场",
        r"不等于.{1,20}",
        r"暂不",
        r"观望",
    ]
    return any(re.search(p, text) for p in patterns)


def _has_substantive_risk_warning(text: str) -> bool:
    """风险提示不能只有一句空泛免责声明。"""
    vague_only = re.fullmatch(
        r"[\s\S]{0,80}(投资有风险|入市需谨慎|仅供参考|不构成投资建议)[\s\S]{0,20}",
        text.strip(),
    )
    if vague_only:
        return False

    risk_section = ""
    for marker in ("【风险提示】", "**风险提示**", "> **风险提示**", "风险提示"):
        idx = text.find(marker)
        if idx >= 0:
            risk_section = text[idx : idx + 300]
            break

    if not risk_section:
        # 引用块也算
        blocks = re.findall(r"^>\s*.+$", text, flags=re.MULTILINE)
        risk_section = "\n".join(blocks[-3:]) if blocks else text[-200:]

    if len(risk_section.strip()) < 25:
        return False

    substantive_keywords = ["止损", "仓位", "波动", "失效", "减仓", "数据", "流动性", "结构"]
    return any(kw in risk_section for kw in substantive_keywords)


def run_style_checks(
    reply: str,
    *,
    multi_symbol: bool,
    profile_case: bool = False,
    max_table_lines: int = MAX_TABLE_LINES,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    _ = multi_symbol  # 保留参数兼容调用方

    checks.append(
        CheckResult(
            "no_biran",
            "必然" not in reply,
            "不允许出现「必然」",
        )
    )

    table_lines = _count_table_lines(reply)
    checks.append(
        CheckResult(
            "avoid_large_table",
            table_lines <= max_table_lines,
            f"表格行数 {table_lines} 超过建议上限 {max_table_lines}",
        )
    )

    cn_len = _count_chinese_chars(reply)
    checks.append(
        CheckResult(
            "length_limit",
            cn_len <= MAX_CN_CHARS,
            f"中文字数 {cn_len} 超过上限 {MAX_CN_CHARS}",
        )
    )

    if not profile_case:
        checks.append(
            CheckResult(
                "conditional_expression",
                _has_conditional_expression(reply),
                "应包含条件化表达（若/则、条件列、需先/突破后再等）",
            )
        )

    checks.append(
        CheckResult(
            "substantive_risk_warning",
            _has_substantive_risk_warning(reply),
            "风险提示需有实质内容，不能只有空泛免责声明",
        )
    )

    return checks


def _max_table_lines_for_case(case: dict[str, Any]) -> int:
    if case.get("detail_analysis"):
        return int(case.get("max_table_lines") or MAX_TABLE_LINES_DETAIL)
    return int(case.get("max_table_lines") or MAX_TABLE_LINES)


async def _run_live_case(conversation_service: Any, case: dict[str, Any]) -> CaseResult:
    session_id = f"smoke_style_{case['id']}"
    try:
        envelope = await conversation_service.run(
            text=case["input"],
            session_id=session_id,
            history_limit=4,
        )
        reply = str(envelope.reply_text or "").strip()
        if not reply:
            return CaseResult(
                case_id=case["id"],
                user_input=case["input"],
                reply="",
                error="空回复",
            )
        checks = run_style_checks(
            reply,
            multi_symbol=bool(case.get("multi_symbol")),
            profile_case=bool(case.get("profile_case")),
            max_table_lines=_max_table_lines_for_case(case),
        )
        return CaseResult(case_id=case["id"], user_input=case["input"], reply=reply, checks=checks)
    except Exception as e:
        return CaseResult(
            case_id=case["id"],
            user_input=case["input"],
            reply="",
            error=str(e),
        )


def _run_mock_case(case: dict[str, Any]) -> CaseResult:
    reply = MOCK_RESPONSES.get(case["id"], "")
    if not reply:
        return CaseResult(
            case_id=case["id"],
            user_input=case["input"],
            reply="",
            error=f"mock 样例缺失: {case['id']}",
        )
    checks = run_style_checks(
        reply,
        multi_symbol=bool(case.get("multi_symbol")),
        profile_case=bool(case.get("profile_case")),
        max_table_lines=_max_table_lines_for_case(case),
    )
    return CaseResult(case_id=case["id"], user_input=case["input"], reply=reply, checks=checks)


def _format_markdown(results: list[CaseResult], *, mock: bool) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Response Style Smoke Report",
        "",
        f"- 生成时间: {ts}",
        f"- 模式: {'mock' if mock else 'live (ConversationService)'}",
        f"- 用例数: {len(results)}",
        f"- 通过: {sum(1 for r in results if r.ok)} / {len(results)}",
        "",
    ]

    for i, r in enumerate(results, 1):
        status = "✅ PASS" if r.ok else "❌ FAIL"
        lines.extend([
            f"## {i}. {r.case_id} {status}",
            "",
            f"**输入**: {r.user_input}",
            "",
        ])
        if r.error:
            lines.extend([f"**错误**: {r.error}", ""])
        else:
            lines.append("**检查项**:")
            lines.append("")
            for c in r.checks:
                mark = "✅" if c.passed else "❌"
                detail = f" — {c.detail}" if c.detail and not c.passed else ""
                lines.append(f"- {mark} `{c.name}`{detail}")
            lines.extend(["", "**回复**:", "", r.reply, ""])

    return "\n".join(lines).rstrip() + "\n"


async def _main_async(args: argparse.Namespace) -> int:
    results: list[CaseResult] = []

    if args.mock:
        for case in TEST_CASES:
            results.append(_run_mock_case(case))
    else:
        from app.factory import create_runtime_services

        services = create_runtime_services()
        for case in TEST_CASES:
            print(f"[live] 运行: {case['input'][:40]}...")
            results.append(await _run_live_case(services.conversation_service, case))

    report = _format_markdown(results, mock=args.mock)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"\n报告已保存: {output_path}")
    passed = sum(1 for r in results if r.ok)
    print(f"结果: {passed}/{len(results)} 通过")

    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"  [{mark}] {r.case_id}")
        if not r.ok:
            for c in r.checks:
                if not c.passed:
                    print(f"         - {c.name}: {c.detail or 'failed'}")
            if r.error:
                print(f"         - error: {r.error}")

    return 0 if passed == len(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Response style smoke test")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="跳过真实 LLM，用内置样例验证检查规则",
    )
    parser.add_argument(
        "--output",
        default=str(get_output_dir(repo_root=ROOT) / "response_style_smoke.md"),
        help="Markdown 报告输出路径",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
