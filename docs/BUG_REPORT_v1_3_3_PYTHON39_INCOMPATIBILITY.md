# Bug Report: v1.3.3 Python 3.9 Compatibility

**Date:** 2026-06-08  
**Version:** v1.3.3 (commit a5aa1c0)  
**Severity:** CRITICAL (Import fails)  
**Python:** 3.9.25 (system requirement)  
**Status:** CONFIRMED

## Issue Summary

Version 1.3.3 cannot be imported on Python 3.9.25 due to use of Python 3.10+ union type syntax at runtime.

## Error

```
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

### Full Traceback

```python
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/home/lveal/ai-memory-system/ai_memory/__init__.py", line 38, in <module>
    from ai_memory.learn import (
  File "/home/lveal/ai-memory-system/ai_memory/learn.py", line 407, in <module>
    assistant: str | None = None,
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

## Root Cause

### Affected Code

**File:** `ai_memory/learn.py:407`

```python
async def write_fact(
    topic: dict,
    *,
    assistant: str | None = None,  # ❌ Python 3.10+ syntax
    driver=None,
    workspace=None,
) -> bool:
    """Write a single Fact node to Neo4j."""
```

### Why It Fails

- The `|` union syntax for type hints is **only supported in Python 3.10+**
- Type hints are evaluated at import time (not deferred)
- Python 3.9 does not support this syntax at runtime
- This is a **hard requirement** for compatibility with Python 3.9

### When This Was Introduced

This issue was introduced in v1.3.3 with the new **provenance feature**:
- Commit: a5aa1c0 "fix(provenance): 5 QA-found bugs — 151/151 tests pass"
- Previous version v1.3.2 (c9f9545) works fine on Python 3.9.25

## Environment

| Item | Value |
|------|-------|
| Python | 3.9.25 |
| ai-memory-system | v1.3.3 (commit a5aa1c0) |
| OS | Oracle Linux 8.x |
| Tested | 2026-06-08 |

## Verification

**v1.3.2 (Working):**
```bash
$ python3.9 -c "from ai_memory import learn, search, graph; print('✓ Works')"
✓ Works
```

**v1.3.3 (Broken):**
```bash
$ python3.9 -c "from ai_memory import learn"
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "/home/lveal/ai-memory-system/ai_memory/learn.py", line 407, in <module>
    assistant: str | None = None,
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

## Impact

| Aspect | Impact |
|--------|--------|
| **Severity** | CRITICAL |
| **Scope** | All Python 3.9.x systems |
| **Usability** | v1.3.3 is completely unusable on Python 3.9 |
| **Regression** | Yes - v1.3.2 works fine |
| **Workaround** | Use v1.3.2 until fixed |

## Proposed Solutions

### Solution 1: Use Optional (RECOMMENDED)

**Pros:**
- Standard library (typing module)
- Python 3.6+ compatible
- Clear intent
- Most readable

**Implementation:**
```python
from typing import Optional

async def write_fact(
    topic: dict,
    *,
    assistant: Optional[str] = None,  # ✓ Python 3.6+
    driver=None,
    workspace=None,
) -> bool:
    """Write a single Fact node to Neo4j."""
```

### Solution 2: Use Union

**Pros:**
- Standard library (typing module)
- Python 3.5+ compatible
- Explicit about multiple types

**Implementation:**
```python
from typing import Union

async def write_fact(
    topic: dict,
    *,
    assistant: Union[str, None] = None,  # ✓ Python 3.5+
    driver=None,
    workspace=None,
) -> bool:
    """Write a single Fact node to Neo4j."""
```

### Solution 3: String Annotation with `from __future__ import annotations`

**Pros:**
- Allows Python 3.10+ syntax in Python 3.9
- Defers type hint evaluation
- Future-proof

**Implementation:**
```python
from __future__ import annotations

async def write_fact(
    topic: dict,
    *,
    assistant: str | None = None,  # ✓ Works as string in 3.9+
    driver=None,
    workspace=None,
) -> bool:
    """Write a single Fact node to Neo4j."""
```

**Note:** Requires adding `from __future__ import annotations` at the top of all affected files.

## Recommendation

**Use Solution 1 (Optional)** for maximum clarity and compatibility.

**Steps:**
1. Add `from typing import Optional` to `ai_memory/learn.py`
2. Search for all `| None` patterns in the codebase
3. Replace with `Optional[...]` syntax
4. Add Python 3.9 type checking to CI/tests
5. Release v1.3.4 with fix

## Files Requiring Investigation

- `ai_memory/learn.py` (confirmed issue at line 407)
- `ai_memory/provenance.py` (likely other instances)
- Other new files added in v1.3.3

## Testing Checklist

- [ ] Fix type syntax in all affected files
- [ ] Test on Python 3.9.25
- [ ] Test on Python 3.10+
- [ ] Add CI check for Python 3.9 compatibility
- [ ] Verify all imports work
- [ ] Run full test suite
- [ ] Update CHANGELOG
- [ ] Release v1.3.4

## Additional Context

This appears to be a Python version oversight during the provenance feature development. The codebase previously supported Python 3.9+ but v1.3.3 introduced syntax that requires 3.10+.

The pyproject.toml may also need review to ensure the Python version constraint is accurate.

## Related Issues

- API Parameter Mismatch (MemoryStateManager) — Separate issue
- Multi-platform support in prompt-guard — Related security work

