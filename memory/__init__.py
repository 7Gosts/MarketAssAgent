"""MarketAssAgent — 会话与快照管理。

公共 API：
- MarketSessionManager: 会话管理器（恢复/持久化 snapshot + 对话历史）
- extract_snapshot: 从分析结果提取轻量摘要
- snapshot_to_context_str: 将摘要转为可读上下文字符串

过渡层 re-export（# TODO(legacy): 迁移后改为本地定义）：
- SessionState: 会话状态数据类
- SessionStateStore: 会话状态存储
"""

from memory.session_manager import MarketSessionManager
from memory.snapshot import extract_snapshot, snapshot_to_context_str

# # TODO(legacy): 迁移 SessionState 到 memory/ 后改为本地导入
from app.session_state import SessionState, SessionStateStore  # noqa: F401

__all__ = [
    "MarketSessionManager",
    "extract_snapshot",
    "snapshot_to_context_str",
    "SessionState",
    "SessionStateStore",
]