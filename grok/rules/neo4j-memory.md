# Neo4j knowledge graph

This Grok is wired to the live Neo4j graph via `~/.grok/.env.neo4j`.

On any question that depends on prior work, decisions, recipes, or “what do we know”:

1. If `~/.grok/neo4j-hits.md` exists and is <20s old, read it.
2. Run `python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "…"` with the actual query. Cite Fact names. Hits are leads — re-derive volatile state from live sources.

To persist knowledge, use the same CLI `write --assistant <Mind>` (default Grok). Refuse to clobber another mind’s Fact. No secrets in the graph.

Session graph pulse: `~/.grok/neo4j-session.md` (written at SessionStart).
