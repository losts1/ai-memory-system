# GitHub Issue Templates - For Manual Submission to losts1/ai-memory-system

Submit these issues at: https://github.com/losts1/ai-memory-system/issues/new

---

## Issue 1: API Parameter Mismatch

**Title:**
```
BUG: MemoryStateManager constructor parameter mismatch (v1.3.2)
```

**Labels:** `bug`, `api`

**Body:**
```markdown
## Issue

The `MemoryStateManager` class has a constructor parameter naming inconsistency that breaks multi-agent implementations.

## Current Behavior

```python
from ai_memory import state

# Expected usage (breaks):
manager = state.MemoryStateManager(agent_id="test-agent")
# TypeError: __init__() got an unexpected keyword argument 'agent_id'

# Current usage (works):
manager = state.MemoryStateManager(session_id="test")
```

## Root Cause

API inconsistency between modules:

- `graph.traverse(..., assistant="Claude")` ✓
- `search.search_files(..., assistant="Claude")` ✓
- `learn.sync_facts(..., assistant="Claude")` ✓
- `state.MemoryStateManager(session_id=...)` ✗ (breaks multi-agent pattern)

## Environment

- Python: 3.9.25
- ai-memory-system: v1.3.2 (commit c9f9545)
- neo4j: 5.28.4
- Tested: 2026-06-04

## Test Case

```python
import inspect
from ai_memory import state

sig = inspect.signature(state.MemoryStateManager)
print(f"Signature: {sig}")
# Output: (workspace=None, session_id: Optional[str] = None)

# Fails with agent_id=
try:
    manager = state.MemoryStateManager(agent_id="test")
except TypeError as e:
    print(f"Error: {e}")
    # TypeError: __init__() got an unexpected keyword argument 'agent_id'
```

## Impact

Breaks code expecting `agent_id=` parameter in multi-agent contexts. Creates API inconsistency across library modules.

## Proposed Solutions

1. **Add `agent_id=` parameter** (backwards compatible) - RECOMMENDED
   - Accept both `agent_id=` and `session_id=`
   - Prefer `agent_id=` if provided
   - Clear intent for multi-agent usage

2. **Rename to `agent_id=`** (breaking change)
   - Clearer naming convention
   - Requires version bump and migration guide
   - Deprecation period recommended

3. **Standardize on `assistant=`** (design alignment)
   - Align with other multi-agent APIs
   - Breaking change across all modules
   - Largest migration effort

## Additional Context

See detailed analysis: https://github.com/lee-a-veal/ai-memory-system/blob/bug/api-mismatch-memorystatemanager/docs/BUG_REPORT_v1_3_2_API_ISSUES.md

This appears to be a Phase 2/Phase 3 refactoring artifact where multi-agent support was added with `assistant=` parameter but `MemoryStateManager` naming wasn't standardized.
```

---

## Issue 2: API Naming Consistency

**Title:**
```
API: Multi-agent parameter naming inconsistency across modules
```

**Labels:** `design`, `api`, `enhancement`

**Body:**
```markdown
## Issue

The library uses inconsistent parameter names for agent/multi-agent scoping across different modules.

## Current State

Different modules use different parameter names for the same concept:

```python
# These use assistant= for agent scoping
from ai_memory.graph import traverse
from ai_memory.search import search_files
from ai_memory.learn import sync_facts

traverse(..., assistant="Claude")
search_files(..., assistant="Claude")
sync_facts(..., assistant="Claude")

# But MemoryStateManager uses session_id=
from ai_memory.state import MemoryStateManager
manager = MemoryStateManager(session_id="test")
```

## Impact

- **API Confusion:** Developers don't know which parameter to use
- **Documentation Burden:** Each module needs separate documentation
- **Type Safety:** IDE autocomplete can't predict parameter names
- **Consistency:** Multi-agent feature split across inconsistent APIs

## Options

### Option 1: Standardize on `agent_id=` (Clear Intent)
```python
traverse(..., agent_id="Claude")
search_files(..., agent_id="Claude")
sync_facts(..., agent_id="Claude")
MemoryStateManager(agent_id="Claude")
```

### Option 2: Standardize on `assistant=` (Already Partial Usage)
```python
traverse(..., assistant="Claude")  # Already uses this
search_files(..., assistant="Claude")  # Already uses this
sync_facts(..., assistant="Claude")  # Already uses this
MemoryStateManager(assistant="Claude")  # Update needed
```

### Option 3: Document and Accept Both
Accept both names with preference, clear documentation on which is primary.

## Recommendation

Standardize on single parameter name before v2.0.0 release to avoid long-term fragmentation.

## Environment

- Tested: ai-memory-system v1.3.2 (commit c9f9545)
- Python: 3.9.25
```

---

## How to Submit

### Method 1: Copy-Paste into GitHub Web UI (Easiest)

1. Go to: https://github.com/losts1/ai-memory-system/issues/new
2. Click "New issue"
3. Copy-paste the **Title** from above
4. Copy-paste the **Body** from above
5. Add labels from **Labels** field
6. Click "Submit new issue"

### Method 2: Use GitHub CLI (Command Line)

```bash
# Requires gh command and authentication
gh issue create --repo losts1/ai-memory-system \
  --title "BUG: MemoryStateManager constructor parameter mismatch (v1.3.2)" \
  --body "$(cat path/to/body.txt)" \
  --label bug,api
```

### Method 3: Open PR with Bug Report

Alternatively, create a pull request with your fork:
```
https://github.com/losts1/ai-memory-system/pull/new/lee-a-veal:bug/api-mismatch-memorystatemanager
```

---

## Supporting Documentation

Your fork contains detailed analysis:
- **Branch:** bug/api-mismatch-memorystatemanager
- **File:** docs/BUG_REPORT_v1_3_2_API_ISSUES.md (179 lines)
- **Content:**
  - Root cause analysis
  - Three proposed solutions with trade-offs
  - Migration path recommendations
  - Timeline and affected files

Reference this in issues if needed.

---
