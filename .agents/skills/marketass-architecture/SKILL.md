---
name: marketass-architecture
description: Design or improve MarketAssAgent module boundaries when a request genuinely changes data flow, ownership, persistence, prompts, memory, tools, or adapters. Do not use for a local rename, mapping, or output-format edit.
---

# MarketAssAgent Architecture

Use this only when a change actually affects module boundaries or data flow. First check whether the request fits one existing function or output boundary.

## Principles

- Prefer deep modules: small interface, substantial behavior hidden behind it.
- Put seams where behavior actually varies. Do not add an abstraction for a single implementation unless tests or runtime configuration need it.
- Treat new classes, schemas, storage, and orchestration as costs that require a concrete long-term invariant; a request for type safety does not imply a model per field.
- Keep stable internal codes and storage semantics unless the requirement targets them. Put localization or presentation conversion at one consumer-facing boundary.
- Do not add compatibility paths without an identified caller, persisted data format, or rollout requirement.
- Keep prompt changes separate from data-flow changes when possible.
- Memory and context changes must define write path, read path, compact schema, failure mode, and test coverage.
- Feishu/external API changes need a fallback path and minimal local validation.

## Process

1. Map the current data flow.

   Identify entry point, service, tool, domain logic, persistence, and response rendering. Use concrete file paths.

2. Normalize the requirement.

   State the target behavior, owning boundary, non-goals, acceptance signal, and rough change size. If a local edit satisfies them, stop doing architecture work.

3. Name the seam.

   State which module owns the behavior and which caller-facing interface should stay stable.

4. Prefer a minimal evolution.

   Prefer an existing helper or one boundary conversion. Add a schema or abstraction only when multiple real consumers need the same validated contract.

5. Define verification.

   Verify observable behavior at the seam. For prompt-only changes, use a focused smoke run or evaluation; do not assert prompt wording.

6. Check proportionality.

   If the diff grows materially beyond the estimate, pause and reconsider the seam instead of completing the larger design by inertia.

7. Document only durable decisions.

   Update docs when the data flow or architecture changes, not for every small implementation detail.
