---
name: marketass-debug
description: Diagnose and fix a MarketAssAgent bug using a tight local feedback loop. Use when something is broken, duplicated, failing in CI, slow, or producing wrong output.
---

# MarketAssAgent Debug

Debug by building evidence before changing code.

## Process

1. Reproduce or identify the failing signal.

   Use the user's log, GitHub Actions output, a focused pytest command, a script, or a minimal local command. Prefer a command that can go red on the exact symptom.

2. Locate the path.

   Use `rg` to find entry points, call sites, and tests. Read the relevant files before editing.

3. Form a narrow hypothesis.

   State what is likely wrong and what would prove it. Avoid changing code based only on vibes.

4. Patch the smallest cause.

   Use `apply_patch`. Avoid incidental cleanup. Keep fallback behavior consistent with existing code.

   For known-field formatting or mapping bugs, patch the explicit field path directly. Do not add recursive object walkers, generic translation systems, broad schema layers, or compatibility branches unless the bug proves that general behavior is required.

5. Verify the original failure path.

   Run the exact failing command when available, plus the smallest related regression test. For CI failures, run the CI-equivalent command locally when feasible.

6. Clean up debugging artifacts.

   Remove temporary logs, scripts, and `[DEBUG-...]` instrumentation before finishing.

7. Check the diff against the user's preferences.

   Review `git diff --stat` and the key changed hunk. If the fix is much larger than the bug, reduce it before reporting.

## Output

Report:

- Root cause.
- Files changed.
- Verification commands and results.
- Remaining risks or validation gaps.
