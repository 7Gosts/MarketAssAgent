import os
import sys
from pathlib import Path

import uvicorn

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in map(str, sys.path):
    sys.path.insert(0, str(ROOT_DIR))

from app_factory import create_app
from utils.logging_utils import get_logger, get_uvicorn_log_config


app = create_app()
logger = get_logger(__name__)


if __name__ == "__main__":
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8000"))

    logger.info("MarketAssAgent API 启动中")
    uvicorn.run(
        "cli.api_server:app",
        host=host,
        port=port,
        reload=False,
        log_config=get_uvicorn_log_config(),
    )
