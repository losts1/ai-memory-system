# Trading Domain — Real Production Example

**This is an explicit "this is what one production deployment looked like" example.**

The original heavy development of this memory system happened inside a sophisticated multi-bot cryptocurrency market-making operation (Kraken + Coinbase makers, inventory monitors, RLM research agents, etc.).

## Important

- The core system is **not** a trading bot framework.
- Everything you see here (Neo4j schema, RLM traversal, learn sync, multi-mind attachment) works equally well for research, software engineering, personal knowledge, creative work, etc.
- We include this example because:
  1. It was the original proving ground (battle-tested).
  2. It demonstrates advanced usage (parameter tracing on "gamma", "inventory skew", "kill switch", etc.).
  3. It shows what a very high-signal, long-running graph looks like after years of use.

## What a Trading Agent Might Store

- Market microstructure observations
- Strategy parameter experiments and outcomes
- "Kill switch" logic and failure modes
- Cross-exchange inventory dynamics
- Regime detection notes

These become Facts. The RLM tools (`neo4j_traverse.py --parameter gamma`, `memory_state.py`, etc.) were developed specifically to navigate this kind of dense, high-stakes knowledge graph safely.

## Recommendation

If you are **not** building trading agents, start with the `research/`, `software-engineering/`, or `personal/` examples instead. They will feel more relevant and will help you internalize that this is a general memory substrate.

---

**Bottom line:** Trading was the original domain. It is no longer the default or the intended primary use case for new users of the public package.