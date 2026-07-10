---
name: marketass-review
description: Review MarketAssAgent changes in Codex style. Use when the user asks for a review, asks what changed, or wants to know whether a diff is safe.
---

# MarketAssAgent Review

Use a code-review mindset. Findings come first.

## Process

1. Identify the review range.

   If the user gives a commit, branch, or PR range, use it. Otherwise review the working tree with `git diff` and `git status --short`.

2. Read project rules.

   Apply `AGENTS.md`, existing docs, and local conventions. Do not enforce generic style preferences that the repo does not use.

3. Inspect behavior risks.

   Prioritize bugs, regressions, missing tests, hidden behavior changes, data loss, auth issues, external API risk, and CI breakage.

4. Check tests against behavior.

   Prefer public seams. Flag tests that lock obsolete implementation details or miss the real behavior.

5. Report concisely.

   Findings first, ordered by severity. Include file and line references where possible. If there are no findings, say so and mention residual risks.

## Review Output

Use this structure:

- Findings.
- Open questions or assumptions.
- Verification gaps.

Do not bury findings under summaries.
