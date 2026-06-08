# How to Submit Bug Reports to Upstream

## Permission Issue

The GitHub token doesn't have write access to `losts1/ai-memory-system` (different repository owner). Issues must be submitted through the GitHub web interface.

## Quick Links

### Create Issues Directly

**Issue 1 - API Parameter Mismatch:**
https://github.com/losts1/ai-memory-system/issues/new?title=BUG%3A%20MemoryStateManager%20constructor%20parameter%20mismatch%20(v1.3.2)&labels=bug,api

**Issue 2 - API Naming Consistency:**
https://github.com/losts1/ai-memory-system/issues/new?title=API%3A%20Multi-agent%20parameter%20naming%20inconsistency%20across%20modules&labels=design,api,enhancement

### Or Create PR with Analysis

https://github.com/losts1/ai-memory-system/pull/new/lee-a-veal:bug/api-mismatch-memorystatemanager

---

## Step-by-Step Instructions

### Option A: Submit via GitHub Web UI (Easiest)

1. **For Issue 1 (Parameter Mismatch):**
   - Click: https://github.com/losts1/ai-memory-system/issues/new
   - Title: `BUG: MemoryStateManager constructor parameter mismatch (v1.3.2)`
   - Labels: `bug`, `api`
   - Body: Copy from `GITHUB_ISSUE_TEMPLATES.md` → Issue 1

2. **For Issue 2 (API Consistency):**
   - Click: https://github.com/losts1/ai-memory-system/issues/new
   - Title: `API: Multi-agent parameter naming inconsistency across modules`
   - Labels: `design`, `api`, `enhancement`
   - Body: Copy from `GITHUB_ISSUE_TEMPLATES.md` → Issue 2

3. Click "Submit new issue"

### Option B: Submit as Pull Request

1. Go to:
   https://github.com/losts1/ai-memory-system/pull/new/lee-a-veal:bug/api-mismatch-memorystatemanager

2. GitHub auto-fills:
   - Base: `losts1/ai-memory-system` (master)
   - Compare: `lee-a-veal/ai-memory-system` (bug/api-mismatch-memorystatemanager)

3. Use PR template:
   ```
   Title: docs: Report MemoryStateManager API inconsistency (v1.3.2)
   
   Body:
   ## Summary
   
   Documented API issue affecting multi-agent implementations.
   
   ## Issue
   
   The MemoryStateManager class uses session_id= parameter instead of agent_id=,
   creating naming inconsistency with other multi-agent APIs that use assistant=.
   
   ## What's Included
   
   - Comprehensive bug analysis (docs/BUG_REPORT_v1_3_2_API_ISSUES.md)
   - Test cases and root cause analysis
   - Three proposed solutions with trade-offs
   - Migration path recommendations
   
   ## Files Changed
   
   - docs/BUG_REPORT_v1_3_2_API_ISSUES.md
   
   See the attached documentation for complete details.
   ```

### Option C: Contact Maintainer Directly

Message `losts1` on GitHub with:
- Reference to your fork's `bug/api-mismatch-memorystatemanager` branch
- Link to `docs/BUG_REPORT_v1_3_2_API_ISSUES.md`

---

## What's Ready for Submission

✅ **Issue Templates** (`GITHUB_ISSUE_TEMPLATES.md`)
- Copy-paste ready for both issues
- Includes all test cases and evidence
- Properly formatted markdown

✅ **Detailed Documentation** (`docs/BUG_REPORT_v1_3_2_API_ISSUES.md`)
- 179 lines of comprehensive analysis
- Root cause identification
- Three proposed solutions
- Timeline and recommendations

✅ **Formatted Text Report** (`UPSTREAM_BUG_REPORT.txt`)
- Alternative format for sharing
- Can be used in discussions or emails

✅ **Branch with Changes** (`bug/api-mismatch-memorystatemanager`)
- Pushed to `lee-a-veal/ai-memory-system`
- Ready for PR submission

---

## Summary of Issues

| Issue | Severity | Type | Status |
|-------|----------|------|--------|
| MemoryStateManager parameter mismatch | HIGH | Bug | 📋 Ready to submit |
| API naming inconsistency | MEDIUM | Design | 📋 Ready to submit |

Both issues documented with test cases, root cause analysis, and proposed solutions.

---

## Next Steps

1. **Quick:** Click the issue links above and fill in the body text
2. **Or:** Copy templates from `GITHUB_ISSUE_TEMPLATES.md`
3. **Or:** Create PR using the provided branch link

The maintainer will review and decide on the preferred solution.

---

## Files in Your Fork

```
lee-a-veal/ai-memory-system/
├── docs/
│   └── BUG_REPORT_v1_3_2_API_ISSUES.md    (179 lines, detailed)
├── GITHUB_ISSUE_TEMPLATES.md              (Ready-to-submit templates)
├── UPSTREAM_BUG_REPORT.txt                (Formatted text report)
├── SUBMIT_ISSUES.md                       (This file)
└── (branch: bug/api-mismatch-memorystatemanager)
```

---
