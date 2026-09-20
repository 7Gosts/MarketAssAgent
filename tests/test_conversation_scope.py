from __future__ import annotations

import asyncio

from core.conversation_scope import build_feishu_scope
from infrastructure.adapters.feishu_adapter import FeishuAdapter


def test_private_and_group_scopes_are_isolated_for_same_actor():
    private = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_alice",
        user_id="u_alice",
        chat_id="oc_private",
        chat_type="p2p",
    )
    group = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_alice",
        user_id="u_alice",
        chat_id="oc_team",
        chat_type="group",
    )

    assert private.visibility_scope == "private"
    assert group.visibility_scope == "group"
    assert private.scope_key != group.scope_key
    assert private.session_id != group.session_id


def test_group_scope_is_shared_by_members_but_keeps_actor_identity():
    alice = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_alice",
        user_id="u_alice",
        chat_id="oc_team",
        chat_type="group",
    )
    bob = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_bob",
        user_id="u_bob",
        chat_id="oc_team",
        chat_type="group",
    )

    assert alice.scope_key == bob.scope_key
    assert alice.session_id == bob.session_id
    assert alice.actor_id != bob.actor_id


def test_different_groups_have_different_scopes():
    first = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_alice",
        user_id="u_alice",
        chat_id="oc_team_a",
        chat_type="group",
    )
    second = build_feishu_scope(
        tenant_id="tenant-a",
        open_id="ou_alice",
        user_id="u_alice",
        chat_id="oc_team_b",
        chat_type="group",
    )

    assert first.scope_key != second.scope_key
    assert first.session_id != second.session_id


def test_feishu_adapter_routes_private_and_group_to_different_sessions():
    captured: list[dict[str, object]] = []
    adapter = FeishuAdapter(conversation_service=object())  # type: ignore[arg-type]

    async def capture(**kwargs: object) -> dict[str, object]:
        captured.append(kwargs)
        return {"code": 0}

    adapter._handle_text_message = capture  # type: ignore[method-assign]

    asyncio.run(
        adapter.handle_longconn_message(
            text="private",
            open_id="ou_alice",
            chat_id="oc_private",
            chat_type="p2p",
            tenant_id="tenant-a",
            message_id="om_private",
        )
    )
    asyncio.run(
        adapter.handle_longconn_message(
            text="group",
            open_id="ou_alice",
            chat_id="oc_team",
            chat_type="group",
            tenant_id="tenant-a",
            message_id="om_group",
        )
    )

    assert captured[0]["session_id"] != captured[1]["session_id"]
    assert captured[0]["extra_meta"]["visibility_scope"] == "private"  # type: ignore[index]
    assert captured[1]["extra_meta"]["visibility_scope"] == "group"  # type: ignore[index]
