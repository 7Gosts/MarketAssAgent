---
name: marketass-implement
description: Implement a MarketAssAgent change end-to-end with Codex-native workflow. Use when the user asks to modify code, fix behavior, or add a small feature in this repo.
---

# MarketAssAgent Implement

Use this workflow for code changes in this repository.

## Process

1. Inspect the current state first.

   Run `git status --short`, then use `rg` / `sed` to locate the call chain, key functions, and key parameters. Do not guess from file names alone.

2. State the concrete change and risk.

   Before editing, explain which files will change and why. If the change touches Feishu, memory, prompts, persistence, auth, deletion, or external APIs, state the failure mode and fallback.

3. Make the smallest coherent edit.

   Prefer existing functions and module boundaries. Use `apply_patch` for manual edits. Do not introduce new dependencies or architecture unless the current seam cannot support the request.

4. Verify narrowly first.

   Run the smallest relevant command, such as `python -m py_compile <files>` or a focused `pytest` file. Run broader tests only when the touched path warrants it.

5. Show the useful diff.

   Provide the key `git diff` fragment or summarize the exact changed behavior. If a file is untracked, say that ordinary `git diff` will not show it.

6. Commit only when requested.

   When committing, stage only relevant files, use a clear message, and never amend or rewrite history unless explicitly asked.

## Repo Rules

- Default language is Simplified Chinese.
- Protect user changes in a dirty worktree.
- Avoid broad refactors, renames, or cleanup unrelated to the request.
- Keep tests behavior-oriented and aligned with existing public seams.
