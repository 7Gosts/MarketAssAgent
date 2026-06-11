from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from adapters.feishu_adapter import FeishuAdapter
from api.routes import router as api_router
from core.agent import MarketReActAgent
from core.router import Router
from core.writer import Writer
from memory.feishu_memory import FeishuMemory, FeishuMemoryConfig
from persistence.db import init_db
from utils.logging_utils import get_logger


logger = get_logger(__name__)


@dataclass
class RuntimeServices:
    agent: MarketReActAgent
    # @deprecated: feishu_memory 字段保留兼容，实际主路径已迁移至 SessionManager
    feishu_memory: FeishuMemory
    router: Router
    writer: Writer
    feishu_adapter: FeishuAdapter


def init_database_if_possible() -> None:
    try:
        init_db()
    except Exception as e:
        logger.warning("[DB] 初始化跳过: %s", e)


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
    web_dir = Path(__file__).resolve().parent / "web"

    app.state.agent = services.agent
    app.state.services = services

    app.include_router(api_router, prefix="/api", tags=["agent"])
    if web_dir.is_dir():
        app.mount("/web", StaticFiles(directory=web_dir), name="web")

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok", "message": "MarketReActAgent is running"}

    @app.get("/chat")
    async def chat_page() -> FileResponse:
        index_path = web_dir / "index.html"
        if not index_path.exists():
            from fastapi.responses import HTMLResponse
            return HTMLResponse(
                "<h1>MarketReActAgent</h1><p>web/index.html 不存在，请放置前端文件。</p>",
                status_code=200
            )
        return FileResponse(index_path)

    return app
