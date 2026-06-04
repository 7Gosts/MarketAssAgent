import uvicorn
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from core.agent import MarketReActAgent
from adapters.feishu_adapter import FeishuAdapter
from config.settings import settings
from api.routes import router as api_router
from persistence.db import init_db

app = FastAPI(title="MarketReActAgent", version="0.1.0")

# 初始化数据库
try:
    init_db()
except Exception as e:
    print(f"[DB] 初始化跳过: {e}")

# 初始化 LLM
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

# 初始化 Agent（注入 LLM）
agent = MarketReActAgent(llm=llm)

# 把 agent 挂到 app.state，供 api/routes 使用
app.state.agent = agent

# 初始化适配器
feishu_adapter = FeishuAdapter(agent=agent)

# 挂载 API 路由
app.include_router(api_router, prefix="/api", tags=["agent"])


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
