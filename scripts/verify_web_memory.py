#!/usr/bin/env python3
"""
验证 Web 记忆是否生效的脚本

用法：
    python scripts/verify_web_memory.py

要求：
- 服务必须已通过 bash scripts/web_dev.sh 启动
- 使用同一个 session_id 连续发送两轮消息
- 第二轮回复应能引用第一轮中要求记住的内容
"""

import sys
import time

try:
    import httpx
except ImportError:
    print("错误：缺少 httpx 依赖，请先执行：pip install httpx")
    sys.exit(1)


API_URL = "http://127.0.0.1:8000/api/agent/run"
SESSION_ID = "verify_memory_test_001"
TIMEOUT = 60.0


def call_agent(text: str) -> dict:
    """调用真实 /api/agent/run 接口"""
    payload = {
        "text": text,
        "session_id": SESSION_ID,
    }
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(API_URL, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        print("❌ 错误：无法连接到服务 (127.0.0.1:8000)")
        print("请先在另一个终端执行：bash scripts/web_dev.sh")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP 错误：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 请求失败：{e}")
        sys.exit(1)


def extract_reply(response: dict) -> str:
    envelope = response.get("envelope") if isinstance(response, dict) else {}
    if not isinstance(envelope, dict):
        return ""
    return str(envelope.get("reply_text") or "")


def main():
    print(f"开始验证 Web 记忆（session_id = {SESSION_ID}）\n")

    # 第一轮：让 Agent 记住一个事实
    msg1 = "请记住：黄金目前处于震荡区间，建议观望。"
    print(f"第 1 轮发送：{msg1}")
    res1 = call_agent(msg1)
    reply1 = extract_reply(res1)
    print(f"第 1 轮回复：{reply1[:120]}...\n")
    time.sleep(1)

    # 第二轮：追问上一轮内容
    msg2 = "根据你之前说的内容，黄金目前是什么情况？"
    print(f"第 2 轮发送：{msg2}")
    res2 = call_agent(msg2)
    reply2 = extract_reply(res2)
    print(f"第 2 轮回复：{reply2[:200]}...\n")

    # 关键词检查
    keywords = ["震荡区间", "建议观望", "震荡", "观望"]
    found = any(kw in reply2 for kw in keywords)

    if found:
        print("✅ 验证通过：第二轮回复中成功引用了第一轮要求记住的内容！")
        print(f"   命中关键词：{[k for k in keywords if k in reply2]}")
    else:
        print("❌ 验证失败：第二轮回复未包含第一轮要求记住的关键词")
        print(f"   期望包含以下任意关键词：{keywords}")
        print(f"   实际回复：{reply2[:150]}...")
        sys.exit(1)


if __name__ == "__main__":
    main()
