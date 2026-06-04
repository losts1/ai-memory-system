# ADR-042: Use Worktrees for Long-Running Agent Tasks

**Status:** Accepted  
**Date:** 2026-05-20

## Context

We run many long-lived, independent agent threads (refactors, research spikes, memory system improvements). Working in a single checkout creates constant risk of:

- Stale state leaking between threads
- Accidental `git add` of work from the wrong context
- Mental context-switching cost for the human overseeing multiple agents

## Decision

Each significant agent initiative gets its own `git worktree`.

Example:
```bash
git worktree add ../aob-phase5 phase5/go
cd ../aob-phase5
# Now this directory has its own clean state + can have its own memory notes if desired
```

## Consequences

**Positive**
- Complete isolation between initiatives
- Easy to abandon a line of work (`git worktree remove`)
- Can run multiple agents in parallel without collision

**Negative**
- Slightly higher disk usage (mitigated by worktree sharing objects)
- Requires discipline around pruning finished worktrees

## Related

- See `examples/personal/` for how one agent reflects on its own resistance to "boring but high-value" tasks like this ADR process.
- The memory system itself benefits from this pattern (each major phase of the upgrade plan often lived in its own worktree during development).