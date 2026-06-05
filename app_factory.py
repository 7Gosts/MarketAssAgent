from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request

from adapters.feishu_adapter import FeishuAdapter
from api.routes import router as api_router
from core.agent import MarketReActAgent
from core.router import Router
from core.writer import Writer
from memory.feishu_memory import FeishuMemory, FeishuMemoryConfig
from persistence.db import init_db


@dataclass
class RuntimeServices:
    agent: MarketReActAgent
    feishu_memory: FeishuMemory
    router: Router
    writer: Writer
    feishu_adapter: FeishuAdapter


def init_database_if_possible() -> None:
    try:
        init_db()
    except Exception as e:
        print(f"[DB] 初始化跳过: {e}")


def create_runtime_services() -> RuntimeServices:
    init_database_if_possible()

    agent = MarketReActAgent()
    feishu_memory = FeishuMemory(FeishuMemoryConfig.from_yaml())
    router = Router()
    writer = Writer()
    feishu_adapter = FeishuAdapter(
        agent=agent,
        memory=feishu_memory,
        router=router,
        writer=writer,
    )

    return RuntimeServices(
        agent=agent,
        feishu_memory=feishu_memory,
        router=router,
        writer=writer,
        feishu_adapter=feishu_adapter,
    )


def create_app() -> FastAPI:
    services = create_runtime_services()
    app = FastAPI(title="MarketReActAgent", version="0.1.0")

    app.state.agent = services.agent
    app.state.services = services

    app.include_router(api_router, prefix="/api", tags=["agent"])

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "message": "MarketReActAgent is running"}

    @app.post("/webhook/feishu")
    async def feishu_webhook(payload: dict, request: Request) -> dict:
        """飞书机器人 webhook 入口"""
        return await services.feishu_adapter.handle_message(payload, request)

    return app
