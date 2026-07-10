from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent

for path in (REPO_ROOT / "runtime", REPO_ROOT / "src", REPO_ROOT):
    raw = str(path)
    if raw not in sys.path:
        sys.path.insert(0, raw)
