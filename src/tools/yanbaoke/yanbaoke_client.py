from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCH_SCRIPT = _REPOSITORY_ROOT / "tools" / "yanbaoke" / "scripts" / "search.mjs"
MAX_CONTENT_CHARS = 4000


def _slugify(text: str, *, max_len: int = 80) -> str:
    raw = text.strip()
    if not raw:
        return "query"
    s = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af._-]+", "_", raw, flags=re.UNICODE)
    s = s.strip("_")
    if not s:
        s = "query"
    return s[:max_len]


def run_node_script(script_path: Path, args: list[str], *, timeout_sec: float = 60.0) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 node，请先安装 Node.js")
    cmd = [node, str(script_path), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"node 脚本失败({proc.returncode}): {err}")
    return proc.stdout


def search_reports_markdown(
    keyword: str,
    *,
    n: int = 5,
    search_type: str = "title",
    script_path: Path | None = None,
    timeout_sec: float = 60.0,
) -> str:
    sp = script_path or DEFAULT_SEARCH_SCRIPT
    if not sp.is_file():
        raise FileNotFoundError(f"search.mjs 不存在: {sp}")
    args = [keyword, "-n", str(max(1, min(int(n), 500))), "--type", search_type]
    return run_node_script(sp, args, timeout_sec=timeout_sec)


def parse_search_markdown(md: str) -> dict[str, Any]:
    lines = [ln.rstrip() for ln in (md or "").splitlines()]
    total = None
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    for ln in lines:
        if ln.startswith("Total:"):
            m = re.search(r"Total:\s*(\d+)\s*reports", ln)
            if m:
                total = int(m.group(1))
            continue

        m_title = re.match(r"^- \*\*(.+)\*\*\s*$", ln)
        if m_title:
            if cur:
                items.append(cur)
            cur = {"title": m_title.group(1).strip()}
            continue

        if not cur:
            continue

        m_pub = re.match(r"^\s*Publisher:\s*(.+)\s*$", ln)
        if m_pub:
            cur["org_name"] = m_pub.group(1).strip()
            continue

        m_uuid = re.match(r"^\s*UUID:\s*(.+)\s*$", ln)
        if m_uuid:
            cur["uuid"] = m_uuid.group(1).strip()
            continue

        m_url = re.match(r"^\s*(https?://\S+)\s*$", ln)
        if m_url:
            cur["url"] = m_url.group(1).strip()
            continue

    if cur:
        items.append(cur)

    return {"total": total, "items": items, "raw_md": md}


def search_reports_json(
    keyword: str,
    *,
    n: int = 5,
    search_type: str = "title",
    script_path: Path | None = None,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    sp = script_path or DEFAULT_SEARCH_SCRIPT
    if not sp.is_file():
        raise FileNotFoundError(f"search.mjs 不存在: {sp}")
    args = [keyword, "-n", str(max(1, min(int(n), 500))), "--type", search_type, "--json"]
    output = run_node_script(sp, args, timeout_sec=timeout_sec)
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("研报搜索返回了无效 JSON") from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(str(payload.get("message") if isinstance(payload, dict) else "研报搜索返回格式错误"))

    raw_items = payload.get("data")
    if not isinstance(raw_items, list):
        raw_items = []
    items: list[dict[str, Any]] = []
    fields = ("uuid", "title", "url", "time", "pagenum", "org_name", "rtype_name", "formats")
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = {field: raw_item[field] for field in fields if field in raw_item}
        content = str(raw_item.get("content") or "")
        item["content"] = content[:MAX_CONTENT_CHARS]
        item["content_truncated"] = len(content) > MAX_CONTENT_CHARS
        items.append(item)

    total = payload.get("total", len(items))
    if not isinstance(total, int) or isinstance(total, bool):
        total = len(items)
    if total <= 0:
        items = []
    return {"total": max(total, 0), "items": items}
