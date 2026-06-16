#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATHS = [
    "app_factory.py",
    "api/routes.py",
    "memory/feishu_memory.py",
    "adapters",
    "renderers",
    "presenters",
    "formatters",
]

FORBIDDEN_IMPORT_PATTERNS = [
    r"\bfrom\s+app_factory\s+import\b",
    r"\bimport\s+app_factory\b",
    r"\bfrom\s+api\.routes\s+import\b",
    r"\bimport\s+api\.routes\b",
    r"\bfrom\s+adapters\.",
    r"\bimport\s+adapters\.",
    r"\bfrom\s+renderers\.",
    r"\bimport\s+renderers\.",
    r"\bfrom\s+presenters\.",
    r"\bimport\s+presenters\.",
    r"\bfrom\s+memory\.feishu_memory\s+import\b",
    r"\bimport\s+memory\.feishu_memory\b",
]

LEGACY_SESSION_PATTERNS = [
    "session_manager.get_recent_messages(",
    "session_manager.save_user_message(",
    "session_manager.save_reply(",
]

LEGACY_SESSION_ALLOWLIST = {
    "services/conversation_service.py",
    "memory/session_manager.py",
    "core/router.py",
    "tests/test_phase_c_memory_flow.py",
    "scripts/guard_no_legacy_memory_path.py",
}


def _iter_python_files() -> list[Path]:
    files = []
    for p in REPO_ROOT.rglob("*.py"):
        if ".venv" in p.parts or ".git" in p.parts:
            continue
        files.append(p)
    return files


def main() -> int:
    errors: list[str] = []

    for rel in FORBIDDEN_PATHS:
        p = REPO_ROOT / rel
        if p.exists():
            errors.append(f"forbidden legacy path exists: {rel}")

    compiled = [re.compile(pat) for pat in FORBIDDEN_IMPORT_PATTERNS]

    for file_path in _iter_python_files():
        rel = file_path.relative_to(REPO_ROOT).as_posix()
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"cannot read {rel}: {exc}")
            continue

        for pat in compiled:
            if pat.search(text):
                errors.append(f"forbidden legacy import in {rel}: {pat.pattern}")

        for token in LEGACY_SESSION_PATTERNS:
            if token in text and rel not in LEGACY_SESSION_ALLOWLIST:
                errors.append(f"legacy session access in non-allowlisted file {rel}: {token}")

    if errors:
        print("Legacy memory/path guard failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Legacy memory/path guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
