from __future__ import annotations

import os
from pathlib import Path


def _default_data_root() -> Path:
    return Path("~/.marketassagent").expanduser()


def get_data_root(*, repo_root: Path | None = None) -> Path:
    """Return runtime data root.

    Priority:
    1) MARKETASSAGENT_DATA_DIR
    2) ~/.marketassagent
    """
    override = os.getenv("MARKETASSAGENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _default_data_root()


def get_sessions_dir(*, repo_root: Path | None = None) -> Path:
    return get_data_root(repo_root=repo_root) / "sessions"


def get_debug_dir(*, repo_root: Path | None = None) -> Path:
    return get_data_root(repo_root=repo_root) / "debug"


def get_output_dir(*, repo_root: Path | None = None) -> Path:
    return get_data_root(repo_root=repo_root) / "output"

