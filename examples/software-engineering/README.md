# Software Engineering Domain Example

Useful for agents that work on codebases, make architecture decisions, track technical debt, and maintain long-term project memory.

## Typical Artifacts

- Architecture Decision Records (ADRs) stored as Facts.
- "Gotcha" patterns and anti-patterns extracted into the Word index.
- Dependency graphs or module relationships modeled via `RELATED_TO`.

## Starter: Architecture Decision Note

```markdown
# ADR-042: Use Worktrees for Long-Running Agent Tasks

## Context
We frequently have multiple independent refactoring or research threads. Running them in the same working directory causes constant context switching and risk of cross-contamination.

## Decision
We will use `git worktree` for each major agent initiative. Each worktree gets its own checkout + its own `memory/` daily notes if needed.

## Consequences
- Positive: Clean separation, easy to abandon or merge later.
- Negative: More disk usage; must remember to prune worktrees.
- We added a `worktree/` helper script to manage them.
```

## Recommended RLM Usage

- `neo4j_traverse.py --start "ADR-042" --parameter worktree`
- Use `memory_state.py` to keep only the relevant ADRs loaded in a given coding session.