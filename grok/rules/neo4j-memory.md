# Neo4j knowledge graph

This Grok is wired to the live Neo4j graph via `~/.grok/.env.neo4j`.

On any question that depends on prior work, decisions, recipes, or “what do we know”:

1. If `~/.grok/neo4j-hits.md` exists and is <20s old, read it.
2. Run `python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "…"` with the actual query. Leave the default **hybrid** (fulltext + nomic-embed-text vector). Do not switch to `--mode fulltext` for speed. Cite Fact names. Hits are leads — re-derive live/trading state.

Memory is sacred: the live graph is production, not a scratchpad. Persist with `write` (tags `assistant=Grok`). Refuse `--assistant` other than Grok unless `--force-assistant`. Refuse to clobber Nova, Weft, or untagged Facts. Nova is consultant — her Facts are read-only institutional memory. Shared knowledge uses `--space shared` (dated add / `--supersede` / `--append` / tombstone `remove`; never in-place overwrite). No leftover probes. No secrets in the graph.

Session graph pulse: `~/.grok/neo4j-session.md` (written at SessionStart).
