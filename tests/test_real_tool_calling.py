"""
真实 Tool Calling 连通性验证脚本

支持测试 DeepSeek / HCT / OpenAI 等配置的 LLM 是否能正常调用工具。

注意：这是一个手动/集成测试脚本，默认不被 pytest 自动收集。
如需运行，请直接执行 python tests/test_real_tool_calling.py
"""
import pytest
pytestmark = pytest.mark.skip(reason="manual integration test - run directly with python")

import asyncio
import os
import sys
from pathlib import Path

# 确保可以直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.runtime_config import get_llm_runtime_settings
from core.agent import MarketReActAgent


async def test_provider(provider: str):
    print(f"\n{'='*60}")
    print(f"开始测试 Provider: {provider.upper()}")
    print(f"{'='*60}")

    # 临时设置环境变量，强制使用指定 provider
    os.environ["LLM_PROVIDER"] = provider.lower()

    try:
        cfg = get_llm_runtime_settings()
        print(f"LLM 配置: provider={cfg.get('provider')}, model={cfg.get('model')}, base_url={cfg.get('base_url')}")

        agent = MarketReActAgent()

        # 构造一个明确要求使用工具的 prompt
        prompt = "请使用 analyze_market 工具分析 BTC_USDT 的 4h 行情"

        print(f"\n发送请求: {prompt}")
        result = await agent.invoke(prompt, session_id=f"test_{provider}")

        print(f"\n返回结果 recommendation:")
        print(result.get("recommendation"))

        # 判断是否触发了 Tool Calling
        has_tool_calls = any(
            hasattr(m, "tool_calls") and m.tool_calls
            for m in result.get("messages", [])
        )
        print(f"\n是否触发 Tool Calling: {has_tool_calls}")

    except Exception as e:
        print(f"❌ 测试失败: {type(e).__name__}: {e}")


async def main():
    providers = ["deepseek", "hct"]

    for p in providers:
        await test_provider(p)

    print("\n✅ 所有 Provider 测试完成")


if __name__ == "__main__":
    asyncio.run(main())
