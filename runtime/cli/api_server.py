import os
import sys
from pathlib import Path

import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (REPO_ROOT / "runtime", REPO_ROOT / "src", REPO_ROOT):
    raw = str(path)
    if raw not in sys.path:
        sys.path.insert(0, raw)

from app.factory import create_app
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
