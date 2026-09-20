from __future__ import annotations

import json

import tools.research as research_module
import tools.yanbaoke.yanbaoke_client as yanbaoke_client


def test_search_reports_json_preserves_real_report_fields(monkeypatch) -> None:
    payload = {
        "success": True,
        "total": 1,
        "data": [
            {
                "uuid": "report-1",
                "title": "半导体行业周报",
                "url": "https://pc.yanbaoke.cn/info/report-1",
                "time": "2026-09-14",
                "pagenum": 12,
                "org_name": "测试证券",
                "rtype_name": "行业周报",
                "content": "核心观点" * 2500,
            }
        ],
    }
    captured: dict[str, object] = {}

    def fake_run(_script_path, args, *, timeout_sec):
        captured.update(args=args, timeout_sec=timeout_sec)
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(yanbaoke_client, "run_node_script", fake_run)

    result = yanbaoke_client.search_reports_json("半导体", n=3)

    assert result["total"] == 1
    assert result["items"][0]["uuid"] == "report-1"
    assert result["items"][0]["time"] == "2026-09-14"
    assert result["items"][0]["rtype_name"] == "行业周报"
    assert len(result["items"][0]["content"]) <= yanbaoke_client.MAX_CONTENT_CHARS
    assert result["items"][0]["content_truncated"] is True
    assert captured["args"] == ["半导体", "-n", "3", "--type", "title", "--json"]


def test_search_research_reports_discards_results_when_total_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        research_module,
        "search_reports_json",
        lambda *_args, **_kwargs: {
            "total": 0,
            "items": [{"title": "示例研报", "uuid": "example-uuid-123"}],
        },
    )

    result = research_module.search_research_reports("半导体")

    assert result["total"] == 0
    assert result["results"] == []
    assert result["message"] == "未检索到与 半导体 相关的研报信息"
