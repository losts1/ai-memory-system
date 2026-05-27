# Research Domain Example

This shows how the memory system can support literature review, paper tracking, and concept synthesis for a research-oriented agent.

## Typical Structure

- Papers are turned into Facts (title + key claims + citations).
- Concepts get their own Word nodes via `HAS_WORD`.
- Related papers are linked via `RELATED_TO` (populated by `neo4j_learn_sync.py` or manual `link_related_facts`).

## Starter File: Sample Paper Note

```markdown
# Paper: Attention Is All You Need (2017)

## Key Claims
- Self-attention is sufficient to replace recurrence and convolution.
- Multi-head attention allows the model to jointly attend to information from different representation subspaces.

## Practical Takeaways for Agents
- Attention patterns can be inspected for interpretability.
- The "Transformer" architecture is now the default backbone for most modern LLMs.

## Citations
- Vaswani et al. (2017). Attention Is All You Need. NeurIPS.
```

## Recommended Tools for This Domain

- `hybrid_memory_search.py "transformer attention limitations" --graph`
- `neo4j_traverse.py --start "Attention Is All You Need" --depth 2 --fields name,summary`

See the main templates for how to structure deeper QMD summaries.