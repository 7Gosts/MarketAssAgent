---
name: marketass-testing
description: Design focused tests for stable, observable MarketAssAgent behavior. Use when deciding whether a change needs a regression test and where that test belongs.
---

# MarketAssAgent Testing

Test behavior that can regress, not text or structure that merely describes the implementation.

## Decide Whether to Add a Test

Add a persistent test only when all are true:

- The requirement defines stable behavior visible through a public module boundary.
- A defect in that behavior could recur without the test.
- The assertion can verify an outcome or side effect rather than restating source code.

Otherwise use a focused smoke command, fixture run, syntax check, or manual invocation and do not leave a low-value test behind.

## Good Test Boundaries

- Service or tool return values that drive business behavior.
- Persisted state, idempotency, retries, and externally visible side effects.
- Request parameters sent across an external API boundary when those parameters are the behavior.
- Parsing and decision logic with expected values taken from a requirement or fixed fixture.

Use mocks at external boundaries only when needed. Avoid assertions about incidental internal calls.

## Do Not Test

- Prompt text, documentation wording, or generated LLM prose.
- Display phrases, localization labels, translation tables, or enum spelling in isolation.
- Class shape, schema field lists, private helpers, or internal data layout.
- One-time requirement corrections or remnants of discarded designs.
- A value recomputed with the same production logic being tested.
- Tests whose only value is proving that a local implementation used a particular class, field loop, helper split, or wording choice.

When exact text is a real external protocol, test the serializer or protocol boundary as a whole rather than searching source strings.

## Workflow

1. Identify the observable failure and the module that owns it.
2. Choose the narrowest public seam that proves the behavior.
3. Add one focused test only if it has durable regression value.
4. Run that test first, then expand verification in proportion to the risk.
