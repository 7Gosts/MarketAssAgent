"""测试 get_full_prompt 能正确注入 storage_key。"""

from core.planner import ResponsePlan
from core.prompts import get_full_prompt
from core.planner_prompt import PLANNER_SYSTEM_PROMPT


def test_get_full_prompt_injects_storage_key_from_user_id():
    plan = ResponsePlan(task_type="chat")
    ctx = {"user_id": "feishu_u123456"}

    prompt = get_full_prompt(plan, "你好", context=ctx)

    assert "当前用户画像 storage_key: feishu_u123456" in prompt


def test_get_full_prompt_injects_storage_key_from_session_id():
    plan = ResponsePlan(task_type="chat")
    ctx = {"session_id": "session_abc"}

    prompt = get_full_prompt(plan, "你好", context=ctx)

    assert "当前用户画像 storage_key: session_abc" in prompt


def test_get_full_prompt_injects_storage_key_directly():
    plan = ResponsePlan(task_type="profile_update", required_tools=["profile"])
    ctx = {"storage_key": "web_user_001"}

    prompt = get_full_prompt(plan, "remember my style", context=ctx)

    assert "当前用户画像 storage_key: web_user_001" in prompt
    assert "必须使用该 storage_key" in prompt


def test_get_full_prompt_no_storage_key_when_missing():
    plan = ResponsePlan(task_type="chat")
    ctx = {}

    prompt = get_full_prompt(plan, "你好", context=ctx)

    assert "当前用户画像 storage_key" not in prompt


def test_planner_prompt_contains_profile_update():
    """planner prompt 文本应包含 profile_update / profile 工具说明。"""
    assert "profile_update" in PLANNER_SYSTEM_PROMPT
    assert 'required_tools 可填 ["profile"]' in PLANNER_SYSTEM_PROMPT or "required_tools" in PLANNER_SYSTEM_PROMPT and "profile" in PLANNER_SYSTEM_PROMPT


def test_system_prompt_default_interval_rules():
    from core.prompt import SYSTEM_PROMPT

    assert "4h" in SYSTEM_PROMPT
    assert "只调用一次" in SYSTEM_PROMPT
    assert "黄金" in SYSTEM_PROMPT

