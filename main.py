import uvicorn
from fastapi import FastAPI, Request
from core.agent import MarketReActAgent
from core.router import Router
from core.writer import Writer
from adapters.feishu_adapter import FeishuAdapter
from config.settings import settings
from api.routes import router as api_router
from persistence.db import init_db
from memory.feishu_memory import FeishuMemory, FeishuMemoryConfig

app = FastAPI(title="MarketReActAgent", version="0.1.0")

# 初始化数据库
try:
    init_db()
except Exception as e:
    print(f"[DB] 初始化跳过: {e}")

# 初始化 Agent（内部按 runtime_config 创建 LLM）
agent = MarketReActAgent()

# 把 agent 挂到 app.state，供 api/routes 使用
app.state.agent = agent

# 初始化飞书对话记忆
feishu_memory = FeishuMemory(FeishuMemoryConfig.from_yaml())

# 初始化意图路由器 + 撰稿模块
router = Router()
writer = Writer()

# 初始化适配器（注入记忆 + 路由 + 撰稿）
feishu_adapter = FeishuAdapter(
    agent=agent,
    memory=feishu_memory,
    router=router,
    writer=writer,
)

# 挂载 API 路由
app.include_router(api_router, prefix="/api", tags=["agent"])


@app.get("/")
async def root():
    return {"status": "ok", "message": "MarketReActAgent is running"}


@app.post("/webhook/feishu")
async def feishu_webhook(payload: dict, request: Request):
    """飞书机器人 webhook 入口"""
    return await feishu_adapter.handle_message(payload, request)


if __name__ == "__main__":
    print("🚀 MarketReActAgent 启动中...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
