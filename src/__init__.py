from __future__ import annotations

import importlib
import sys


# Keep one module identity while the filesystem is under src/.
for name in ("application", "core", "domain", "infrastructure", "tools", "utils"):
    try:
        module = importlib.import_module(name)
    except Exception:
        continue
    sys.modules.setdefault(f"src.{name}", module)
