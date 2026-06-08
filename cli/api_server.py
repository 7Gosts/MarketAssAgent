import uvicorn

from app_factory import create_app
from utils.logging_utils import get_logger, get_uvicorn_log_config


app = create_app()
logger = get_logger(__name__)


if __name__ == "__main__":
    logger.info("MarketAssAgent API 启动中")
    uvicorn.run(
        "cli.api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_config=get_uvicorn_log_config(),
    )
