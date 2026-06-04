# Bug Report: v1.3.2 API Issues

**Date:** 2026-06-04  
**Version:** v1.3.2 (commit c9f9545)  
**Reported by:** Lee Veal (lee-a-veal)  
**Status:** Ready for triage

## Summary

The `MemoryStateManager` class has a constructor parameter mismatch that breaks code expecting `agent_id=` parameter. The current implementation uses `session_id=` instead, which conflicts with multi-agent usage patterns documented elsewhere in the API.

## Issues Identified

### 1. MemoryStateManager Constructor Signature Mismatch

**Severity:** High (Breaking Change)

**Current Implementation:**
```python
# ai_memory/state.py
class MemoryStateManager:
    def __init__(self, workspace=None, session_id: Optional[str] = None):
        ...
```

**Failing Code:**
```python
from ai_memory import state

# Expected to work (based on multi-agent patterns elsewhere)
manager = state.MemoryStateManager(agent_id="test-agent")

# Error:
# TypeError: __init__() got an unexpected keyword argument 'agent_id'
```

**Root Cause:**
Parameter named `session_id` instead of `agent_id`, despite the class being used in multi-agent contexts (see `traverse(..., assistant=...)`, `search_files(..., assistant=...)`).

**Impact:**
- Breaks backwards compatibility for code using `agent_id=`
- Inconsistent naming with other multi-agent APIs
- Confusing for multi-agent implementations

---

### 2. API Naming Consistency Issue

**Severity:** Medium (Design Inconsistency)

The codebase uses `assistant=` parameter for agent-scoping in:
- `graph.traverse(..., assistant=...)`
- `search.search_files(..., assistant=...)`
- `learn.sync_facts(..., assistant=...)`

But `MemoryStateManager` uses `session_id=` instead of `agent_id=` or `assistant=`.

**Recommendation:**
Standardize parameter naming across the API:
- Option A: Use `agent_id=` everywhere (clearer intent)
- Option B: Use `assistant=` everywhere (already used in graph/search/learn)
- Option C: Document the distinction between `session_id` and `assistant` clearly

---

### 3. Documentation Gap

**Severity:** Low

The README and migration docs don't clearly document:
- The distinction between `session_id` and `assistant` parameters
- Which APIs support multi-agent scoping
- Migration path for `agent_id=` to `session_id=`

---

## Testing & Verification

### Environment
- Python: 3.9.25
- neo4j: 5.28.4
- python-dotenv: 1.2.1
- pytz: 2026.2

### Test Case
```python
from ai_memory import state
import inspect

# Check actual signature
sig = inspect.signature(state.MemoryStateManager)
print(f"Signature: {sig}")
# Output: (workspace=None, session_id: Optional[str] = None)

# Attempt to use expected API
try:
    manager = state.MemoryStateManager(agent_id="test")
    print("✓ Works with agent_id=")
except TypeError as e:
    print(f"✗ Fails: {e}")
    # Output: ✗ Fails: __init__() got an unexpected keyword argument 'agent_id'
```

### Other APIs Tested (✓ Working)
- `graph.traverse(..., assistant="name")` ✓
- `search.search_files(..., assistant="name")` ✓
- `learn.sync_facts(..., assistant="name")` ✓
- `state.MemoryStateManager(session_id="...")` ✓

---

## Proposed Solutions

### Solution 1: Add `agent_id=` Parameter (Backwards Compatible)
```python
def __init__(self, workspace=None, session_id: Optional[str] = None, agent_id: Optional[str] = None):
    # Accept both, prefer agent_id if provided
    final_id = agent_id or session_id
    self.session_id = final_id
```

**Pros:** Backwards compatible, clear intent  
**Cons:** Two parameter names for same concept

---

### Solution 2: Rename to `agent_id=` (Breaking Change)
```python
def __init__(self, workspace=None, agent_id: Optional[str] = None):
    self.agent_id = agent_id
    self.session_id = agent_id  # Alias for backwards compat
```

**Pros:** Consistent with multi-agent patterns  
**Cons:** Breaking change, requires migration guide

---

### Solution 3: Standardize on `assistant=` (Design Alignment)
```python
def __init__(self, workspace=None, assistant: Optional[str] = None):
    self.assistant = assistant
```

**Pros:** Aligns with graph/search/learn APIs  
**Cons:** Breaking change across all modules

---

## Recommendation

**Preferred:** Solution 1 (Add `agent_id=` parameter)
- Maintains backwards compatibility
- Aligns with multi-agent usage patterns
- Allows future migration to `assistant=` if preferred
- Clear intent in code: `MemoryStateManager(agent_id="Claude")`

**Timeline:**
- v1.3.3: Add `agent_id=` parameter (soft deprecate `session_id=`)
- v1.4.0: Consider standardizing on single parameter name
- CHANGELOG: Document the change and migration path

---

## Files Affected

- `ai_memory/state.py` — MemoryStateManager class definition
- `docs/MIGRATION.md` — Add version-specific migration guide
- `CHANGELOG.md` — Document the API change
- Examples and tests — Update usage patterns

---

## Additional Notes

This appears to be a Phase 2/Phase 3 refactoring artifact where multi-agent support was added (`assistant=` parameter) but `MemoryStateManager` naming wasn't updated for consistency.

The core functionality works correctly; this is purely a naming/API consistency issue.

