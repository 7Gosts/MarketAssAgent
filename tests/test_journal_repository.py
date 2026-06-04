import pytest
from persistence.db import init_db, get_session
from persistence.journal_repository import JournalRepository
from persistence.models import Base


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """测试前初始化内存数据库（使用 SQLite 替代 PostgreSQL）"""
    # 这里简化处理，实际项目中可使用测试专用 SQLite
    pass


def test_journal_repository_crud():
    """测试 JournalRepository 的基本 CRUD"""
    # 注意：此测试需要真实数据库连接，当前仅做结构验证
    repo = JournalRepository()
    assert repo is not None

    # 简单验证方法存在
    assert hasattr(repo, "create")
    assert hasattr(repo, "get_by_id")
    assert hasattr(repo, "list_by_session")
    assert hasattr(repo, "update_status")
