---
name: marketass-architecture
description: Design or improve MarketAssAgent module boundaries with Codex-native constraints. Use when changing prompts, memory, tools, Feishu adapters, or market analysis architecture.
---

# MarketAssAgent Architecture

Use this when a change affects module boundaries or data flow.

## Principles

- Prefer deep modules: small interface, substantial behavior hidden behind it.
- Put seams where behavior actually varies. Do not add an abstraction for a single implementation unless tests or runtime configuration need it.
- Keep prompt changes separate from data-flow changes when possible.
- Memory and context changes must define write path, read path, compact schema, failure mode, and test coverage.
- Feishu/external API changes need a fallback path and minimal local validation.

## Process

1. Map the current data flow.

   Identify entry point, service, tool, domain logic, persistence, and response rendering. Use concrete file paths.

2. Name the seam.

   State which module owns the behavior and which caller-facing interface should stay stable.

3. Prefer a minimal evolution.

   Extend existing schema or helper functions before adding new storage, tools, or orchestration.

4. Define verification.

   Add or update tests at the seam. For prompt-only changes, use smoke tests or focused prompt-contract tests where available.

5. Document only durable decisions.

   Update docs when the data flow or architecture changes, not for every small implementation detail.
