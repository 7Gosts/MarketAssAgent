---
name: marketass-tdd
description: Use practical TDD for MarketAssAgent. Use when adding behavior where a focused regression or feature test is useful.
---

# MarketAssAgent TDD

Use TDD selectively. Do not write tests only to satisfy process.

## Process

1. Choose the seam.

   The seam should be a public interface already used by the project: service method, tool function, renderer, parser, or API route. Avoid private-method tests unless no better seam exists.

2. Write one behavior test.

   The expected value must come from the spec, fixture, or user-visible behavior. Do not recompute expected output using the same logic as production code.

3. Watch it fail when feasible.

   If the bug is already proven by CI or an existing failing test, you can use that as the red step.

4. Implement the minimum fix.

   Do not add future-proofing or speculative abstractions. Keep the slice vertical.

5. Refactor only after green.

   Keep refactors small and local. Re-run the focused test after each meaningful edit.

## Anti-Patterns

- Tests coupled to obsolete rendering formats or internal data layout.
- Large horizontal batches of tests before understanding the implementation.
- Heavy mocks that assert collaborator calls instead of observable behavior.
- Snapshot-like assertions for volatile LLM text.
