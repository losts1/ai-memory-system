# Submit Python 3.9 Compatibility Bug to Upstream

## Quick Links

### Create GitHub Issue (Easiest)
```
https://github.com/losts1/ai-memory-system/issues/new
```

### Create Pull Request (Recommended - includes documentation)
```
https://github.com/losts1/ai-memory-system/pull/new/lee-a-veal:bug/python39-type-syntax-incompatibility
```

---

## Issue Template (Copy-Paste Ready)

### Title
```
BUG: v1.3.3 uses Python 3.10+ type syntax, breaks on Python 3.9
```

### Labels
```
bug, python-3.9-compatibility
```

### Body

```markdown
## Issue

Version 1.3.3 cannot be imported on Python 3.9.25 due to use of Python 3.10+ union type syntax at runtime.

## Error

```
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

Full traceback:
```python
File "/home/lveal/ai-memory-system/ai_memory/__init__.py", line 38, in <module>
    from ai_memory.learn import (
File "/home/lveal/ai-memory-system/ai_memory/learn.py", line 407, in <module>
    assistant: str | None = None,
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

## Root Cause

File: `ai_memory/learn.py:407`

```python
def write_fact(
    topic: dict,
    *,
    assistant: str | None = None,  # ❌ Python 3.10+ syntax
    driver=None,
    workspace=None,
) -> bool:
```

The `|` union syntax requires Python 3.10+. Type hints evaluated at import time fail on Python 3.9.

## Environment

- Python: 3.9.25
- ai-memory-system: v1.3.3 (commit a5aa1c0)
- OS: Oracle Linux 8.x
- Tested: 2026-06-08

## Impact

- Cannot import `ai_memory` module on Python 3.9
- v1.3.3 completely unusable on Python 3.9 systems
- Regression from v1.3.2 (which works fine)

## Verification

**v1.3.2 (Working):**
```bash
python3.9 -c "from ai_memory import learn, search, graph; print('✓ Works')"
# ✓ Works
```

**v1.3.3 (Broken):**
```bash
python3.9 -c "from ai_memory import learn"
# TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

## Proposed Fix

Replace Python 3.10+ syntax with Python 3.9 compatible:

### Option 1: Use Optional (Recommended)
```python
from typing import Optional

def write_fact(
    topic: dict,
    *,
    assistant: Optional[str] = None,  # ✓ Python 3.6+
    driver=None,
    workspace=None,
) -> bool:
```

### Option 2: Use Union
```python
from typing import Union

def write_fact(
    topic: dict,
    *,
    assistant: Union[str, None] = None,  # ✓ Python 3.5+
    driver=None,
    workspace=None,
) -> bool:
```

### Option 3: String Annotation
```python
from __future__ import annotations

def write_fact(
    topic: dict,
    *,
    assistant: str | None = None,  # ✓ Works as string
    driver=None,
    workspace=None,
) -> bool:
```

## Recommendation

Use Option 1 (Optional) for clarity and compatibility.

## Next Steps

1. Search codebase for all `| None` patterns
2. Replace with `Optional[...]` syntax
3. Add Python 3.9 type checking to CI
4. Test on Python 3.9.25 and 3.10+
5. Release v1.3.4

See detailed analysis: https://github.com/lee-a-veal/ai-memory-system/blob/bug/python39-type-syntax-incompatibility/docs/BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md
```

---

## How to Submit

### Method 1: GitHub Web UI (Easiest)

1. Click: https://github.com/losts1/ai-memory-system/issues/new
2. Paste **Title** from above
3. Paste **Body** from above
4. Add **Labels**: `bug`, `python-3.9-compatibility`
5. Click "Submit new issue"

### Method 2: Create Pull Request (Includes Documentation)

1. Click: https://github.com/losts1/ai-memory-system/pull/new/lee-a-veal:bug/python39-type-syntax-incompatibility
2. GitHub auto-fills the comparison
3. Title: `docs: Report Python 3.9 compatibility issue in v1.3.3`
4. Body:
```markdown
## Summary

Documented Python 3.9 compatibility issue in v1.3.3.

## Issue

v1.3.3 uses Python 3.10+ union type syntax (`str | None`) which breaks at import time on Python 3.9.25.

## What's Included

- Comprehensive bug report (docs/BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md)
- Root cause analysis
- Test cases and verification steps
- Three proposed solutions with recommendations

## Files Changed

- docs/BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md

See documentation for complete details and implementation recommendations.
```

### Method 3: Direct Message

Message `losts1` on GitHub with:
- Reference to your fork's `bug/python39-type-syntax-incompatibility` branch
- Link to `docs/BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md`

---

## Supporting Documentation

Your fork contains:

```
lee-a-veal/ai-memory-system/
├── docs/
│   └── BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md (204 lines)
├── Branch: bug/python39-type-syntax-incompatibility
└── Commit: 7876fd7
```

**Full documentation URL:**
```
https://github.com/lee-a-veal/ai-memory-system/blob/bug/python39-type-syntax-incompatibility/docs/BUG_REPORT_v1_3_3_PYTHON39_INCOMPATIBILITY.md
```

---

## Status

- ✅ Bug confirmed on Python 3.9.25
- ✅ Detailed analysis completed
- ✅ Proposed solutions documented
- ✅ Branch pushed to fork
- ⏳ Ready for manual submission to upstream

Choose any method above to submit to losts1/ai-memory-system.

