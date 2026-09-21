---
name: marketass-implement
description: Implement a MarketAssAgent change end-to-end with Codex-native workflow. Use when the user asks to modify code, fix behavior, or add a small feature in this repo.
---

# MarketAssAgent Implement

Use this workflow for code changes in this repository.

## Process

1. Inspect the current state first.

   Run `git status --short`, then use `rg` / `sed` to locate the call chain, key functions, and key parameters. Do not guess from file names alone.

2. Normalize and size the request.

   State the target behavior, owning boundary, non-goals, acceptance signal, and expected files or functions. Treat examples as examples unless the user makes them the full contract.

   Prefer the user's current, concrete intent over previous attempts. Do not preserve rejected designs as compatibility paths unless there is a real caller or data migration need.

3. State the concrete change and risk.

   Before editing, explain which files will change and why. If the change touches Feishu, memory, prompts, persistence, auth, deletion, or external APIs, state the failure mode and fallback.

4. Make the smallest coherent edit.

   Prefer existing functions and module boundaries. Use `apply_patch` for manual edits. For presentation or localization changes, prefer one output-boundary conversion over changing internal storage or building a new type hierarchy. Do not introduce new dependencies or architecture unless the current seam cannot support the request.

   When the affected field path is known, read and assign that path directly. Do not implement recursive object walkers, generic field-name translation systems, compatibility layers, or per-field schema/model classes for a local mapping or formatting need.

   If the implementation materially exceeds the estimate or starts requiring new modules, schemas, or abstraction layers, stop and reassess before adding more code.

5. Verify narrowly first.

   Run the smallest relevant command, such as `python -m py_compile <files>` or a focused `pytest` file. Run broader tests only when the touched path warrants it.

6. Review the whole diff.

   Compare it with the normalized request. Remove unrelated cleanup, speculative compatibility, duplicated conversion, and remnants of approaches the user rejected.

   Check `git diff --stat` and the key diff before finishing. If a simple request produces a large diff, more abstraction, or broader files than expected, reduce it before reporting.

7. Show the useful diff.

   Provide the key `git diff` fragment or summarize the exact changed behavior. If a file is untracked, say that ordinary `git diff` will not show it.

8. Commit only when requested.

   When committing, stage only relevant files, use a clear message, and never amend or rewrite history unless explicitly asked.

## Repo Rules

- Default language is Simplified Chinese.
- Protect user changes in a dirty worktree.
- Avoid broad refactors, renames, or cleanup unrelated to the request.
- Keep tests behavior-oriented and aligned with existing public seams. Do not add tests that only pin prompt text, display wording, localization labels, schema fields, or class shape.
