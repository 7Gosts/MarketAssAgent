"""真实 Tool Calling 连通性验证脚本

支持测试 DeepSeek / HCT / OpenAI 等配置的 LLM 是否能正常调用工具。

用法：
    python scripts/real_tool_calling_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "runtime", ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from core.agent import MarketReActAgent
from core.llm_client import OpenAICompatibleLLMClient


async def test_provider(provider: str):
    print(f"\n{'='*60}")
    print(f"开始测试 Provider: {provider.upper()}")
    print(f"{'='*60}")

    try:
        cfg = get_llm_runtime_settings(provider)
        print(f"LLM 配置: provider={cfg.get('provider')}, model={cfg.get('model')}, base_url={cfg.get('base_url')}")

        llm = OpenAICompatibleLLMClient(
            model=require_llm_model(cfg, context="IntegrationTest"),
            temperature=resolve_llm_temperature(cfg, fallback=0.2),
            base_url=str(cfg.get("base_url") or ""),
            api_key=str(cfg.get("api_key") or ""),
        )
        agent = MarketReActAgent(llm=llm)

        prompt = "请调用 get_response_guidance 工具读取 trade_plan 指导，并简短复述。"

        print(f"\n发送请求: {prompt}")
        result = await agent.invoke(
            prompt,
            session_id=f"test_{provider}",
            allowed_tools=["get_response_guidance"],
        )

        print(f"\n返回结果 recommendation:")
        print(result.get("recommendation"))

        has_tool_calls = any(
            hasattr(m, "tool_calls") and m.tool_calls
            for m in result.get("messages", [])
        )
        print(f"\n是否触发 Tool Calling: {has_tool_calls}")

    except Exception as e:
        print(f"❌ 测试失败: {type(e).__name__}: {e}")


async def main():
    for provider in ("deepseek", "hct"):
        await test_provider(provider)
    print("\n✅ 所有 Provider 测试完成")


if __name__ == "__main__":
    asyncio.run(main())
