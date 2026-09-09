---
name: marketass-testing
description: Test design principles—focus on observable behavior, not implementation details. Use when designing test cases.
---

# Testing Principles: Behavior-Driven Verification

## Core Principle: Test the Contract, Not the Implementation

Tests should verify **observable behavior** (what the system does) not **internal details** (how it does it or what text it contains).

### The Anti-Pattern: String-Matching Tests

**Brittle example:**
```python
def test_prompt_uses_correct_interval():
    assert "加密货币用 4h" in SYSTEM_PROMPT  # ❌ Coupled to text
```

**Why it fails:**
- Breaks when you refactor the prompt (even if behavior stays the same)
- Does not verify the Agent actually *uses* the interval
- Confuses implementation artifact (prompt text) with contract (behavior)

**Academic term:** This violates **Contract/API Testing** and couples tests to **white-box implementation details**.

---

## The Correct Approach: Black-Box Behavioral Tests

Test through **public interfaces** using **observable inputs and outputs**, independent of internal representation.

### Example: Correct Test

```python
def test_agent_uses_default_4h_interval_for_crypto():
    """Verify crypto assets default to 4h analysis when no interval specified."""
    result = agent.analyze(symbol="BTC_USDT", user_query="看下行情")
    
    # Observable assertion: Did it analyze 4h? (via tool call or result metadata)
    assert result.analysis_interval == "4h"
    # OR: Check the tool was called with 4h
    assert mock_analyze_market.called_with(interval="4h")
```

**Why this is better:**
- Behavior is what matters; text is an implementation detail
- Prompts can be revised, test still passes if behavior is correct
- Catches actual bugs (e.g., agent ignores interval logic)
- Uses the public interface (tool calls, API return values, system output)

---

## Test Design Hierarchy (Prefer Earlier)

1. **End-to-End Behavioral Test** — API call → observable result. Slowest, most realistic.
   ```python
   assert agent.run("BTC 4h") returns ANALYSIS_4H
   ```

2. **Unit Test via Public Interface** — Service method → return value (not internal state).
   ```python
   assert market_service.get_default_interval("crypto") == "4h"
   ```

3. **Fixture-Based Verification** — Given known input, assert expected output (not recomputed from production code).
   ```python
   assert parse_interval(raw_data) == expected_interval  # where expected is from spec/user data
   ```

4. ❌ **Implementation-Detail Test** — Check text, private methods, data layout.
   ```python
   assert "4h" in SYSTEM_PROMPT  # ❌ Don't do this
   ```

---

## When String Assertions Are OK

Use string matching **only** when testing a *renderer* or *serializer*, and the output itself is the contract:

```python
def test_error_message_is_user_readable():
    """Error messages must be in Chinese, not error codes."""
    result = prepare_order(invalid_symbol="xxx")
    assert "无法识别的标的代码" in result.message  # ✅ OK: message format IS the contract
```

But even here, prefer:
```python
assert result.error_code == ErrorType.INVALID_SYMBOL  # ✅ Better: test the semantic
```

---

## Checklist: Is This a Good Test?

- [ ] Tests **observable behavior** (return value, side effect, API call)?
- [ ] Would the test break if internal code is refactored but behavior stays the same? If yes, reconsider.
- [ ] Does the expected value come from **spec, fixture, or user-visible contract** (not recomputed from production code)?
- [ ] Could this test catch a real bug (e.g., Agent actually fails to use the rule)?
- [ ] Is the test independent of volatile implementation (prompt text, LLM output formatting)?

---

## References

- **Black-box vs. White-box Testing** — IEEE/ISO 29119. Black-box tests treat the system as a sealed unit; white-box tests inspect internals.
- **Contract-First Testing** — Meyer, *Design by Contract*. Tests verify the contract (precondition, postcondition, invariant), not implementation.
- **Test-Driven Development** — Beck, *Test Driven Development: By Example*. Red → Green → Refactor. The red step writes against the public interface.
- **Fragile Test Problem** — Marick, *The Craft of Software Testing*. Tests tightly coupled to implementation details become "brittle"—they fail on refactoring even when behavior is correct.

---

## When to Remove a Test

If a test:
1. Checks if specific text/format exists in an internal artifact (config, prompt, data structure)
2. Does not verify the system **actually behaves** according to that text
3. Would break if internal representation changes (even if behavior stays the same)

→ **It should be replaced with a behavioral test or deleted.**

Example:
```python
# ❌ REMOVE this:
assert "黄金和其他非加密标的用 1d" in SYSTEM_PROMPT

# ✅ REPLACE with this:
def test_agent_uses_default_1d_interval_for_gold():
    result = agent.analyze(symbol="AU9999", user_query="看下行情")
    assert result.analysis_interval == "1d"  # Behavioral verification
```
