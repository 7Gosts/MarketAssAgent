import uvicorn
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from core.agent import MarketReActAgent
from adapters.feishu_adapter import FeishuAdapter
from config.settings import settings

app = FastAPI(title="MarketReActAgent", version="0.1.0")

# 初始化 LLM（可通过环境变量或 settings 扩展）
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

# 初始化 Agent（注入 LLM）
agent = MarketReActAgent(llm=llm)

# 初始化适配器
feishu_adapter = FeishuAdapter(agent=agent)


@app.get("/")
async def root():
    return {"status": "ok", "message": "MarketReActAgent is running"}


@app.post("/webhook/feishu")
async def feishu_webhook(payload: dict):
    """飞书机器人 webhook 入口"""
    return await feishu_adapter.handle_message(payload)


if __name__ == "__main__":
    print("🚀 MarketReActAgent 启动中...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
