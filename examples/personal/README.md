# Personal Knowledge Example

The simplest and most universal use case: an agent helping a human (or itself) track life goals, relationships, health, reading, and reflections.

## Common Patterns

- People and relationships live in `core/people.qmd` (private).
- Daily reflections and "what I learned about myself" go into regular daily logs.
- Long-term goals become Facts with `priority: high`.

## Starter: Personal Reflection Format

```markdown
# Reflection — 2026-05-27

## What felt alive today
- Deep work on memory system architecture felt like real progress.
- Noticed I was avoiding the "Phase 5" task because it felt less exciting than RLM tooling.

## What I want to remember
- Boredom or resistance is often a signal that the task has high long-term value.
- When I feel "I'd rather do the fun technical thing," that's usually the exact task I should prioritize.

## Open Questions
- How do I design a heartbeat that gently surfaces the boring-but-important items?
```

This kind of high-signal personal note becomes extremely valuable over months and years when the agent can traverse "what I have historically avoided and why".