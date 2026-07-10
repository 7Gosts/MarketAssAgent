from __future__ import annotations

import importlib
import sys


# Map legacy runtime.* imports onto the public top-level package names.
for name in ("app", "cli", "config", "web"):
    try:
        module = importlib.import_module(name)
    except Exception:
        continue
    sys.modules.setdefault(f"runtime.{name}", module)
