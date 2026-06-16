from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.feishu_adapter import FeishuAdapter
from app.adapters.web_adapter import WebAdapter
from app.api.routes import router as api_router
from core.agent import MarketReActAgent
from core.memory_api import DefaultMemoryAPI, create_default_memory_api
from memory.session_manager import MarketSessionManager
from tools.user_profile import set_user_profile_memory_api
from persistence.db import init_db
from services.assistant_orchestrator import AssistantOrchestrator
from services.conversation_service import ConversationService
from core.planner import ResponsePlanner
from utils.logging_utils import get_logger


logger = get_logger(__name__)


@dataclass
class RuntimeServices:
    repo_root: Path
    agent: MarketReActAgent
    session_manager: MarketSessionManager
    memory_api: DefaultMemoryAPI
    conversation_service: ConversationService
    feishu_adapter: FeishuAdapter
    web_adapter: WebAdapter | None = None


def init_database_if_possible() -> None:
    try:
        init_db()
    except Exception as e:
        logger.warning("[DB] 初始化跳过: %s", e)


def create_runtime_services() -> RuntimeServices:
    init_database_if_possible()

    repo_root = Path(__file__).resolve().parents[1]

    agent = MarketReActAgent(
        checkpointer=None,
        store=None,
    )
    session_manager = MarketSessionManager(repo_root=repo_root)
    memory_api = create_default_memory_api(repo_root=repo_root)
    set_user_profile_memory_api(memory_api)
    conversation_service = ConversationService(
        agent=agent,
        session_manager=session_manager,
        memory_api=memory_api,
        planner=ResponsePlanner(),
        orchestrator=AssistantOrchestrator(
            agent_graph=agent,
            chat_llm=agent.llm,
            tools_registry=agent.tools,
            memory_api=memory_api,
        ),
    )

    feishu_adapter = FeishuAdapter(
        agent=agent,
        conversation_service=conversation_service,
    )

    web_adapter = WebAdapter(conversation_service=conversation_service)

    return RuntimeServices(
        repo_root=repo_root,
        agent=agent,
        session_manager=session_manager,
        memory_api=memory_api,
        conversation_service=conversation_service,
        feishu_adapter=feishu_adapter,
        web_adapter=web_adapter,
    )


def create_app() -> FastAPI:
    services = create_runtime_services()
    app = FastAPI(title="MarketReActAgent", version="0.1.0")
    web_dir = Path(__file__).resolve().parents[1] / "web"

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
