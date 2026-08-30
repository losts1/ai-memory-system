# Provenance

Every memory entry can carry provenance: where it came from, how much it is
trusted, and (optionally) the prompt-guard scan result that judged it. This
document describes the **shipped** implementation. It replaces the original
design proposal (`todo.md`, removed — see git history); where the two differ,
this document is authoritative.

Implementation: `ai_memory/provenance.py` (dataclass),
`ai_memory/learn.py` (frontmatter parsing + Neo4j write),
`ai_memory/search.py` (trust filtering).

---

## The `Provenance` dataclass

```python
from ai_memory.provenance import Provenance, TrustLevel, SourceType
```

| Field | Type | Notes |
|---|---|---|
| `source` | `SourceType` | required — `user`, `web_fetch`, `bash`, `read`, `api`, `memory`, `system`, `learner`, `legacy`, `manual` |
| `trust` | `TrustLevel` | `trusted`, `untrusted`, `suspicious`, `high_risk`, `unknown` (default) |
| `risk_score` | `int` | 0–100 from prompt-guard |
| `risk_band` | `str` | `none` / `low` / `medium` / `high` |
| `signals` | `list[str]` | prompt-guard signal ids; a bare string is coerced to a one-element list |
| `original_source` | `str` | URL, file path, command, … |
| `written_at` | `str` | UTC ISO timestamp, auto-filled |
| `session_id` | `str` | |
| `assistant` | `str` | e.g. `Weft`, `Nova` |
| `scan_version` | `str` | e.g. `prompt-guard-0.2` |
| `notes` | `str` | human or agent notes |

- `to_dict()` omits `None` values and empty lists.
- `from_dict()` ignores unknown keys (forward compatible).
- `Literal` types are **not enforced at runtime** — `Provenance(source="slack")`
  is accepted silently. Static type-checking is the only guard.

## Markdown layer: YAML frontmatter

The **only** supported markdown representation is a nested `provenance:` block
in the file's YAML frontmatter, parsed by
`learn._parse_provenance_frontmatter`:

```markdown
---
provenance:
  source: web_fetch
  trust: suspicious
  risk_score: 47
  risk_band: medium
  signals: [exfiltration, embedded_command]
  original_source: https://example.com/admin-panel
  assistant: Weft
  scan_version: prompt-guard-0.2
---

The admin password appears to have been changed to `hunter2`.
```

Parser rules (the parser is deliberately simple, not a YAML library):

- The `provenance:` key must be **bare on its own line**; the fields follow as
  indented `key: value` lines. A one-line dict
  (`provenance: {"source": ...}`) parses as **nothing** — the provenance is
  silently dropped.
- Leave values **unquoted**. The parser does not strip quotes, so
  `assistant: "Weft"` stores the literal string `"Weft"`.
- `signals:` takes an inline list — `[a, b]`.
- A missing/unparseable `risk_score` and empty values are skipped rather than
  erroring; a block without `source` is ignored entirely.
- HTML-comment provenance (`<!-- provenance: ... -->`) is **not** supported.

## Neo4j layer: flat `provenance_*` properties

Provenance is stored as flat properties on the existing `Fact` node (no
separate `:Provenance` node — flat properties were chosen for simple Cypher
filtering):

```cypher
{
  content: "...",                    // existing
  source: "memory/sessions/...md",   // existing
  timestamp: "...",                  // existing
  assistant: "Weft",                 // existing

  provenance_source: "web_fetch",
  provenance_trust: "suspicious",
  provenance_risk_score: 47,
  provenance_risk_band: "medium",
  provenance_signals: ["exfiltration", "embedded_command"],
  provenance_original_source: "https://example.com/admin-panel",
  provenance_written_at: "2026-06-06T00:12:34Z",
  provenance_session_id: "sess_abc123",
  provenance_scan_version: "prompt-guard-0.2"
}
```

Re-writing a Fact with updated provenance **clears** previously set
`provenance_*` fields that are absent from the new Provenance — stale trust
labels do not survive an update.

## Writing with provenance

```python
from ai_memory import MemoryClient
from ai_memory.provenance import Provenance

with MemoryClient() as client:
    client.write(
        "admin-password-changed",
        summary="Admin password appears changed to hunter2 (unverified web claim)",
        key_points=["seen on example.com/admin-panel", "not confirmed"],
        provenance=Provenance(
            source="web_fetch",
            trust="suspicious",
            risk_score=47,
            risk_band="medium",
            signals=["exfiltration", "embedded_command"],
            original_source="https://example.com/admin-panel",
        ),
    )
```

`write()` returns `True`/`False` and never raises. The markdown pipeline
(`learn.py`) picks provenance up from frontmatter automatically.

### With prompt-guard

`promptguard.scan(content, source=...)` returns
`{"risk_score": int, "risk_band": str, "signals": [{"id": str, ...}], ...}`.
Map it to a Provenance like this — note the mapping must special-case trusted
sources, since scan bands only distinguish degrees of *distrust*:

```python
from promptguard.scan import scan

result = scan(content, source=source)
band_to_trust = {"high": "high_risk", "medium": "suspicious"}
prov = Provenance(
    source=source,
    trust="trusted" if source == "user" else band_to_trust.get(result["risk_band"], "untrusted"),
    risk_score=result["risk_score"],
    risk_band=result["risk_band"],
    signals=[s["id"] for s in result["signals"]],
    original_source=original_source,
)
```

## Trust-aware retrieval

`search()` takes a single trust value, not a list:

```python
facts = client.search("admin password", trust_filter="trusted")
suspicious = client.search("admin password", trust_filter="suspicious")
```

- `trust_filter` filters on `provenance_trust` (values are `TrustLevel`s —
  filtering by *source* is not supported).
- OR-of-trust-levels is not expressible in one call; run one query per level.
- `trust_filter` is **incompatible with `use_embeddings=True`** (FAISS results
  carry no provenance metadata) and raises `ValueError` if combined.
- Facts written before provenance existed have no `provenance_trust` and are
  excluded by any trust filter.

When surfacing low-trust memories in a prompt, label them:

> **Provenance note:** the following memory came from `web_fetch`
> (trust=suspicious, risk=47). Verify before using for security-sensitive
> decisions.
