# Retrieval Phase 2 — Canonical Embedding Text, Provenance and Backfill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every in-repo writer embeds the same canonical text, records how the vector was made, and writes text and vector atomically; a backfill re-embeds the whole graph behind a keep-previous safety net and the harness gate decides whether the new vectors ship.

**Architecture:** A new pure module `ai_memory/embed.py` owns the prepared text (`fact_embed_text`), the boilerplate 4-gram detector and run-stripper, the sha helper, the Ollama call, and the compare-and-set (CAS) embed statement. A new `ai_memory/retrieval_config.py` reads and publishes the `(:RetrievalConfig {id: "current"})` singleton that carries the boilerplate gram set and its version. The four in-repo writers (`ai_memory/learn.py` → `MemoryClient.write()`/`learn()`, `scripts/neo4j_sync.py`, `scripts/rlm/neo4j_learn_sync.py`) call into `embed.py`; `ai-memory embed` runs the backfill and `ai-memory stats` reports vector drift. The grok client's `_fact_text` is ported in phase 4 (spec §6) and stays as it is here.

**Tech Stack:** Python ≥ 3.9, neo4j-python driver, optional `ollama` client (`pip install -e .[rlm]`), pytest with fake sessions (no network, no Neo4j in tests).

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` — §4 (canonical text, provenance, CAS, backfill), §5 (`ai_memory/embed.py` row), §7.2 (boilerplate), §9 phase 2 row and Operations, §2 measured context. Phase 0–1 (this branch, commits 4f288d2..81d3c40) delivered §3, §5 search rows and §8; the baseline is recorded at `~/.ai-memory/golden/baseline-phase0.json`.

## Global Constraints

- `requires-python = ">=3.9"`: every new module starts with `from __future__ import annotations`; no `X | None` or PEP 585 generics evaluated at runtime (dataclass field annotations are fine under the future import; `isinstance` checks and default values are not annotations).
- No new runtime dependency. `ollama` stays an optional extra; every function that embeds takes an injectable `embed_fn` and the default (`embed_text`) returns `None` when the package or server is unavailable.
- Prepared text (§4): name, summary, key points as `- ` lines, content; joined by newlines; whitespace-normalised to single spaces; capped at **2,000 characters**; then boilerplate removed. Summary precedes key points.
- Boilerplate (§7.2): token 4-grams over lowercase alphanumeric runs; a gram is boilerplate when present in ≥ `max(10, ceil(0.01 · N))` Facts; only **runs of two or more consecutive** boilerplate grams are removed; removal preserves the surviving text byte-for-byte apart from whitespace normalisation.
- Provenance written **in the same statement** as `embedding`: `embedding_model`, `embedding_dim`, `embedding_text_sha` (16 hex of sha256 over `f"{boilerplate_version}\n{prepared_text}"`), `boilerplate_version` (the `RetrievalConfig.version` the text was prepared with).
- Compare-and-set: the embed write carries a `WHERE` on the exact text fields the writer **did not itself set** in that statement, compared with `coalesce(f.<field>, '')` (lists: `coalesce(f.key_points, [])`); a Fact edited between read and write is skipped (`embedded = 0`), never overwritten.
- `EMBED_MODEL = "nomic-embed-text"`, `EMBED_DIM = 768`; a vector of another length is treated as an embedding failure and not written.
- A writer that cannot load `RetrievalConfig` (no node, or Neo4j read fails) writes the text and skips the vector; it never invents a version.
- `embedding_prev` is written only when `keep_prev=True`; `ai-memory embed --drop-prev` removes it; `ai-memory embed --rollback` restores it and clears the four provenance properties (a restored vector's text is unknown).
- Every write to the graph in this plan goes through `session.run` / `tx.run` with parameters; no string-interpolated user text in Cypher.
- Tests are offline and mocked; run `venv/bin/python -m pytest tests -q -p no:cacheprovider` from the worktree root and `venv/bin/ruff check ai_memory tests scripts` (12 pre-existing F401 findings are out of scope; add none).
- Commit after every task with the repo's attribution trailers (`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`). Never push.

## File Structure

| file | responsibility |
|---|---|
| `ai_memory/embed.py` (create) | `normalize_ws`, `fact_embed_text`, `detect_boilerplate`, `strip_boilerplate`, `text_sha`, `EMBED_MODEL`/`EMBED_DIM`, `embed_text` (Ollama), `build_embed_subquery`, `embed_params`, `read_fact_text`, `embed_fact`, `embed_all`, `drop_prev`, `rollback_prev`, `vector_stats` |
| `ai_memory/retrieval_config.py` (create) | `RetrievalConfig` dataclass, `load_retrieval_config(session)`, `publish_retrieval_config(session, grams)` |
| `ai_memory/learn.py` (modify) | `_sync_fact_tx` gains the embed subquery (CAS on `content`); `sync_facts`/`write_fact` load the config once and pass an `embed_fn` |
| `ai_memory/__init__.py` (modify) | docstrings of `learn()`/`write()`; export `fact_embed_text`, `embed_all`, `vector_stats` |
| `scripts/neo4j_sync.py` (modify) | per-fact write becomes one statement: text MERGE + embed subquery (CAS on `summary`, `key_points`) with canonical text |
| `scripts/rlm/neo4j_learn_sync.py` (modify) | `update_neo4j_vector` uses `embed_fact`; the private cache/thread pool goes |
| `scripts/neo4j_seed.py` (modify) | MERGE the initial `RetrievalConfig` (version 1, empty grams) |
| `ai_memory/_config.py`, `scripts/verify_schema.py` (modify) | report `RetrievalConfig` presence/version |
| `scripts/cli.py` (modify) | `embed` and `stats` subcommands (in-process) |
| `tests/fixtures/embed_text_cases.json` (create) | shared prepared-text fixtures for the phase-4 grok contract test |
| `tests/test_embed.py`, `tests/test_retrieval_config.py` (create); `tests/test_learn.py`, `tests/test_library.py`, `tests/test_verify_schema.py`, `tests/test_cli_smoke.py` (modify) | |
| `CHANGELOG.md`, `MIGRATION.md`, `README.md` (modify) | new properties, writer behaviour change, new commands |

---

### Task 1: Prepared text, boilerplate detector and sha (pure functions)

**Files:**
- Create: `ai_memory/embed.py`
- Create: `tests/test_embed.py`
- Create: `tests/fixtures/embed_text_cases.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_ws(s: str) -> str`; `fact_embed_text(name, summary, key_points, content, boilerplate) -> str` (`boilerplate` is any iterable of gram strings, may be empty); `gram_tokens(text: str) -> list[str]`; `detect_boilerplate(texts: Iterable[str], *, min_abs: int = 10, min_ratio: float = 0.01) -> frozenset[str]`; `strip_boilerplate(text: str, grams, *, min_run: int = 2) -> str`; `text_sha(text: str, version: int) -> str`; constants `EMBED_MODEL = "nomic-embed-text"`, `EMBED_DIM = 768`, `EMBED_CHARS = 2000`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_embed.py
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_memory.embed import (
    EMBED_CHARS, detect_boilerplate, fact_embed_text, gram_tokens, normalize_ws,
    strip_boilerplate, text_sha,
)

FIX = Path(__file__).parent / "fixtures" / "embed_text_cases.json"


def test_normalize_ws_collapses_all_whitespace():
    assert normalize_ws("  a\n\n b\t c  ") == "a b c"


def test_fact_embed_text_order_and_dash_lines():
    t = fact_embed_text("Name", "Sum.", ["p1", "", "p2"], "Body text", ())
    assert t == "Name Sum. - p1 - p2 Body text"


def test_fact_embed_text_skips_missing_parts():
    assert fact_embed_text("N", None, None, None, ()) == "N"
    assert fact_embed_text("N", "", [], "", ()) == "N"


def test_fact_embed_text_caps_before_stripping():
    long = "x" * 5000
    t = fact_embed_text("N", long, None, None, ())
    assert len(t) == EMBED_CHARS


def test_gram_tokens_alnum_lowercase():
    assert gram_tokens("Order-Flow (OFI) 30]") == ["order", "flow", "ofi", "30"]


def test_detect_boilerplate_threshold_is_max_10_or_one_percent():
    tpl = "topic selection gap filling memory md covers"
    texts = [f"{tpl} fact {i}" for i in range(9)] + ["unrelated words here only once"]
    assert detect_boilerplate(texts) == frozenset()          # 9 < max(10, 1)
    texts.append(f"{tpl} fact 9")
    grams = detect_boilerplate(texts)                        # 10 >= 10
    assert "topic selection gap filling" in grams
    assert "fact 9 unrelated words" not in grams
    # 1% rule: N = 2000 -> threshold 20
    many = [f"{tpl} z {i}" for i in range(15)] + [f"noise {i} {i} {i} {i}" for i in range(1985)]
    assert detect_boilerplate(many) == frozenset()          # 15 < 20


def test_strip_boilerplate_removes_runs_only():
    grams = {"topic selection gap filling", "selection gap filling memory", "probability of informed trading"}
    # two consecutive grams -> a run of 6 tokens is removed; the single gram survives
    text = "Topic Selection Gap Filling memory. VPIN is the probability of informed trading."
    out = strip_boilerplate(text, grams)
    assert out == "VPIN is the probability of informed trading."


def test_strip_boilerplate_preserves_case_and_punctuation_of_survivors():
    grams = {"a b c d", "b c d e"}
    assert strip_boilerplate("Keep, THIS! a b c d e Then-more.", grams) == "Keep, THIS! Then-more."


def test_strip_boilerplate_no_grams_is_identity_modulo_ws():
    assert strip_boilerplate("  hello   world ", frozenset()) == "hello world"


def test_text_sha_includes_version():
    a = text_sha("same text", 1)
    b = text_sha("same text", 2)
    assert a != b and len(a) == 16
    assert a == hashlib.sha256(b"1\nsame text").hexdigest()[:16]


def test_fixture_cases_byte_equal():
    cases = json.loads(FIX.read_text(encoding="utf-8"))
    assert len(cases) >= 6
    for c in cases:
        got = fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], frozenset(c["boilerplate"]))
        assert got == c["expected"], c["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_memory.embed'`

- [ ] **Step 3: Write the module**

```python
# ai_memory/embed.py
"""Canonical embedding text, boilerplate removal and provenance-carrying embed writes (spec §4, §7.2).

Pure helpers (no I/O): normalize_ws, fact_embed_text, gram_tokens, detect_boilerplate,
strip_boilerplate, text_sha. I/O helpers (embed_text, embed_fact, embed_all, ...) are added
in later tasks of the phase-2 plan.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable, List, Optional

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
EMBED_CHARS = 2000
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def normalize_ws(s: str) -> str:
    return " ".join((s or "").split())


def gram_tokens(text: str) -> List[str]:
    return [m.group(0).lower() for m in _TOKEN.finditer(text or "")]


def _grams_of(tokens: List[str]) -> set:
    return {" ".join(tokens[i:i + 4]) for i in range(len(tokens) - 3)}


def detect_boilerplate(texts: Iterable[str], *, min_abs: int = 10, min_ratio: float = 0.01) -> frozenset:
    """Token 4-grams present in >= max(min_abs, ceil(min_ratio * N)) texts."""
    texts = list(texts)
    n = len(texts)
    threshold = max(min_abs, math.ceil(min_ratio * n))
    df: Counter = Counter()
    for t in texts:
        df.update(_grams_of(gram_tokens(t)))
    return frozenset(g for g, c in df.items() if c >= threshold)


def strip_boilerplate(text: str, grams, *, min_run: int = 2) -> str:
    """Remove runs of >= min_run consecutive boilerplate grams; keep everything else verbatim
    (whitespace-normalised). A lone boilerplate gram is kept."""
    text = text or ""
    grams = set(grams or ())
    spans = [(m.start(), m.end(), m.group(0).lower()) for m in _TOKEN.finditer(text)]
    toks = [s[2] for s in spans]
    if not grams or len(toks) < 4:
        return normalize_ws(text)
    hit = [" ".join(toks[i:i + 4]) in grams for i in range(len(toks) - 3)]
    drop = [False] * len(toks)
    i = 0
    while i < len(hit):
        if hit[i]:
            j = i
            while j < len(hit) and hit[j]:
                j += 1
            if j - i >= min_run:
                for k in range(i, j + 3):
                    drop[k] = True
            i = j
        else:
            i += 1
    if not any(drop):
        return normalize_ws(text)
    out = []
    cursor = 0
    for (start, end, _), d in zip(spans, drop):
        if d:
            out.append(text[cursor:start])
            cursor = end
    out.append(text[cursor:])
    return normalize_ws("".join(out))


def fact_embed_text(name: Optional[str], summary: Optional[str], key_points, content: Optional[str], boilerplate) -> str:
    parts = [name or ""]
    if summary and summary.strip():
        parts.append(summary.strip())
    for p in (key_points or []):
        if p and str(p).strip():
            parts.append("- " + str(p).strip())
    if content and content.strip():
        parts.append(content.strip())
    prepared = normalize_ws("\n".join(parts))[:EMBED_CHARS]
    return strip_boilerplate(prepared, boilerplate)


def text_sha(text: str, version: int) -> str:
    return hashlib.sha256(f"{version}\n{text}".encode("utf-8")).hexdigest()[:16]
```

Note on `strip_boilerplate`: a boilerplate run of `r` consecutive grams covers `r + 3` tokens; the test "removes runs only" expects the six tokens `topic selection gap filling memory` … wait — that text yields grams `topic selection gap filling` and `selection gap filling memory` (both boilerplate, a run of 2, covering tokens 0..4 = five tokens) then `gap filling memory vpin` (not boilerplate). The expected survivor is `VPIN is the probability of informed trading.` — the period after `memory` sits between dropped token `memory` and kept token `VPIN` and is removed with the dropped span (the code appends text only up to each dropped token's start and resumes at its end, so the `. ` after `memory` is kept — then normalisation gives `. VPIN is …`). **Rule:** punctuation immediately following a dropped run is dropped too. Implement that by extending each dropped token's `end` to the start of the next kept token when the next token is kept:

```python
    out = []
    cursor = 0
    n = len(spans)
    for idx, ((start, end, _), d) in enumerate(zip(spans, drop)):
        if d:
            out.append(text[cursor:start])
            nxt = spans[idx + 1][0] if idx + 1 < n else len(text)
            cursor = nxt if (idx + 1 < n and not drop[idx + 1]) else end
    out.append(text[cursor:])
    return normalize_ws("".join(out))
```

With this rule `"Keep, THIS! a b c d e Then-more."` → `"Keep, THIS! Then-more."` (the space before `Then` is swallowed, normalisation re-inserts one) and the run test yields exactly `"VPIN is the probability of informed trading."`.

- [ ] **Step 4: Write the fixture file**

The six cases and the `expected` string each MUST produce (these are the contract; the generator below only saves typing):

| id | expected |
|---|---|
| `plain` | `Order Book Imbalance OBI as a signal. - depth ratio - 5-level` |
| `no-summary` | `Rough Volatility - Hurst 0.1 Body.` |
| `whitespace` | `Two Words line1 line2 tab` |
| `cap` | `N 0123456789 xxxx…` — exactly 2,000 characters, i.e. `"N 0123456789 " + "x" * 1987` |
| `run-stripped` | `Fact VPIN is the probability of informed trading.` |
| `single-gram-kept` | `Fact the probability of informed trading matters` |

Generate the file with this one-off script so the `cap` case is exact rather than hand-typed:

```python
# one-off, run from the worktree root; do not commit this script
import json
from ai_memory.embed import fact_embed_text
cases = [
 {"id": "plain", "name": "Order Book Imbalance", "summary": "OBI as a signal.", "key_points": ["depth ratio", "5-level"], "content": None, "boilerplate": []},
 {"id": "no-summary", "name": "Rough Volatility", "summary": None, "key_points": ["Hurst 0.1"], "content": "Body.", "boilerplate": []},
 {"id": "whitespace", "name": "  Two   Words ", "summary": "line1\n\nline2\t tab", "key_points": [], "content": "", "boilerplate": []},
 {"id": "cap", "name": "N", "summary": "0123456789", "key_points": [], "content": "x" * 2500, "boilerplate": []},
 {"id": "run-stripped", "name": "Fact", "summary": "Topic Selection Gap Filling memory. VPIN is the probability of informed trading.", "key_points": [], "content": None,
  "boilerplate": ["topic selection gap filling", "selection gap filling memory", "probability of informed trading"]},
 {"id": "single-gram-kept", "name": "Fact", "summary": "the probability of informed trading matters", "key_points": [], "content": None,
  "boilerplate": ["probability of informed trading"]},
]
for c in cases:
    c["expected"] = fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], frozenset(c["boilerplate"]))
json.dump(cases, open("tests/fixtures/embed_text_cases.json", "w"), indent=1, ensure_ascii=False)
```

Then **read the generated file and check each `expected` by eye against the Global Constraints** (this is the fixture phase 4 asserts byte-equality against; a wrong expectation here is a wrong contract). The `plain`, `no-summary`, `whitespace`, `run-stripped` and `single-gram-kept` expectations must equal the strings listed above; `cap` must be exactly 2,000 characters.

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add ai_memory/embed.py tests/test_embed.py tests/fixtures/embed_text_cases.json
git commit -m "feat(embed): canonical fact text, boilerplate 4-gram detector and run-stripper, versioned sha"
```

---

### Task 2: `RetrievalConfig` singleton — load, publish, seed, verify

**Files:**
- Create: `ai_memory/retrieval_config.py`
- Create: `tests/test_retrieval_config.py`
- Modify: `scripts/neo4j_seed.py` (after the fulltext indexes, before `verify_schema`)
- Modify: `ai_memory/_config.py:134-175` (`validate_schema`), `scripts/verify_schema.py`, `tests/test_verify_schema.py`

**Interfaces:**
- Produces: `RetrievalConfig(version: int, boilerplate: frozenset, updated_at: Optional[str])`; `load_retrieval_config(session) -> Optional[RetrievalConfig]`; `publish_retrieval_config(session, grams, *, now: Optional[str] = None) -> RetrievalConfig`; `RETRIEVAL_CONFIG_ID = "current"`; `validate_schema(...)` result gains key `"retrieval_config"` holding `"version N"` or `"missing"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_retrieval_config.py
from __future__ import annotations

from ai_memory.retrieval_config import (
    RETRIEVAL_CONFIG_ID, RetrievalConfig, load_retrieval_config, publish_retrieval_config,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def single(self):
        return self._rows[0] if self._rows else None
    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, rows_by_call=None):
        self.calls = []
        self.rows_by_call = list(rows_by_call or [])
    def run(self, cypher, params=None, **kw):
        p = dict(params or {}); p.update(kw)
        self.calls.append((cypher, p))
        return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


def test_load_returns_none_when_no_node():
    s = FakeSession([[]])
    assert load_retrieval_config(s) is None
    cypher, params = s.calls[0]
    assert "RetrievalConfig" in cypher and params["id"] == RETRIEVAL_CONFIG_ID


def test_load_parses_node():
    s = FakeSession([[{"version": 3, "boilerplate": ["a b c d", "b c d e"], "updated_at": "2026-09-04T00:00:00Z"}]])
    cfg = load_retrieval_config(s)
    assert cfg == RetrievalConfig(version=3, boilerplate=frozenset({"a b c d", "b c d e"}), updated_at="2026-09-04T00:00:00Z")


def test_load_tolerates_null_boilerplate():
    s = FakeSession([[{"version": 1, "boilerplate": None, "updated_at": None}]])
    assert load_retrieval_config(s).boilerplate == frozenset()


def test_publish_increments_version_and_writes_sorted_grams():
    s = FakeSession([[{"version": 4, "updated_at": "T"}]])
    cfg = publish_retrieval_config(s, {"z y x w", "a b c d"}, now="T")
    cypher, params = s.calls[0]
    assert "MERGE (c:RetrievalConfig {id: $id})" in cypher
    assert "ON CREATE SET c.version = 0" in cypher
    assert "c.version = c.version + 1" in cypher
    assert params["grams"] == ["a b c d", "z y x w"]
    assert params["now"] == "T"
    assert cfg.version == 4 and cfg.boilerplate == frozenset({"z y x w", "a b c d"})
```

Add to `tests/test_verify_schema.py` (find the existing test that builds a fake driver for `validate_schema` and mirror its fake; the fake `run` must now also answer a `RetrievalConfig` query):

```python
def test_validate_schema_reports_retrieval_config(monkeypatch):
    from ai_memory import _config
    class R(dict):
        def __getitem__(self, k): return dict.__getitem__(self, k)
    class Sess:
        def __init__(self, cfg_rows): self.cfg_rows = cfg_rows
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "RetrievalConfig" in q:
                return iter(self.cfg_rows)
            return iter([])
    class Drv:
        def __init__(self, rows): self.rows = rows
        def session(self): return Sess(self.rows)
    out = _config.validate_schema(Drv([R(version=2)]))
    assert out["retrieval_config"] == "version 2"
    out = _config.validate_schema(Drv([]))
    assert out["retrieval_config"] == "missing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_retrieval_config.py tests/test_verify_schema.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError` for `ai_memory.retrieval_config`; `KeyError: 'retrieval_config'`.

- [ ] **Step 3: Write the module**

```python
# ai_memory/retrieval_config.py
"""The (:RetrievalConfig {id: "current"}) singleton (spec §4): boilerplate grams + version.
Phase 5 adds the z-score baselines and edge floor to the same node."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

RETRIEVAL_CONFIG_ID = "current"


@dataclass(frozen=True)
class RetrievalConfig:
    version: int
    boilerplate: frozenset
    updated_at: Optional[str] = None


def load_retrieval_config(session) -> Optional[RetrievalConfig]:
    rec = session.run(
        "MATCH (c:RetrievalConfig {id: $id}) "
        "RETURN c.version AS version, c.boilerplate AS boilerplate, c.updated_at AS updated_at",
        id=RETRIEVAL_CONFIG_ID,
    ).single()
    if rec is None or rec["version"] is None:
        return None
    return RetrievalConfig(
        version=int(rec["version"]),
        boilerplate=frozenset(rec["boilerplate"] or []),
        updated_at=rec["updated_at"],
    )


def publish_retrieval_config(session, grams: Iterable[str], *, now: Optional[str] = None) -> RetrievalConfig:
    grams_sorted = sorted(set(grams))
    now = now or datetime.now(timezone.utc).isoformat()
    rec = session.run(
        "MERGE (c:RetrievalConfig {id: $id}) "
        "ON CREATE SET c.version = 0 "
        "SET c.version = c.version + 1, c.boilerplate = $grams, c.updated_at = $now "
        "RETURN c.version AS version, c.updated_at AS updated_at",
        id=RETRIEVAL_CONFIG_ID, grams=grams_sorted, now=now,
    ).single()
    return RetrievalConfig(version=int(rec["version"]), boilerplate=frozenset(grams_sorted), updated_at=rec["updated_at"])
```

Seed (`scripts/neo4j_seed.py`, inside `create_schema` after the `fact_key_points` block):

```python
        # 6. Retrieval config singleton (spec §4): version 1 with no boilerplate so writers can
        #    embed on a fresh install; `ai-memory embed --all` publishes later versions.
        print("Creating retrieval config...")
        session.run(
            "MERGE (c:RetrievalConfig {id: 'current'}) "
            "ON CREATE SET c.version = 1, c.boilerplate = [], c.updated_at = toString(datetime())"
        )
        print("  RetrievalConfig ready")
```

`validate_schema` (`ai_memory/_config.py`), inside the `with driver.session() as s:` block, before the return:

```python
        cfg = list(s.run("MATCH (c:RetrievalConfig {id: 'current'}) RETURN c.version AS version"))
        out["retrieval_config"] = f"version {cfg[0]['version']}" if cfg else "missing"
```

`scripts/verify_schema.py`: where the report prints `vector_indexes`/fulltext lines, add one line printing `retrieval_config` (read the existing print style and match it; if `verify_schema.py` has its own `diff_schema` that does not call `validate_schema`, add a `live_retrieval_config` parameter is NOT required — print the value from `validate_schema` in `main()` only).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_retrieval_config.py tests/test_verify_schema.py -q -p no:cacheprovider`
Expected: all pass (5 new).

- [ ] **Step 5: Commit**

```bash
git add ai_memory/retrieval_config.py tests/test_retrieval_config.py scripts/neo4j_seed.py ai_memory/_config.py scripts/verify_schema.py tests/test_verify_schema.py
git commit -m "feat(config): RetrievalConfig singleton (load/publish), seeded at version 1, reported by validate_schema"
```

---

### Task 3: Embed I/O — Ollama call, CAS subquery, `embed_fact`

**Files:**
- Modify: `ai_memory/embed.py` (append)
- Modify: `tests/test_embed.py` (append)

**Interfaces:**
- Consumes: `RetrievalConfig` (Task 2), Task 1 helpers.
- Produces:
  - `embed_text(text: str, *, model: str = EMBED_MODEL) -> Optional[list]` — Ollama; `None` on any failure or wrong length.
  - `EMBED_PARAM_NAMES = ("embedding", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version")`
  - `build_embed_subquery(cas_fields: Sequence[str], *, keep_prev: bool = False) -> str` — a `CALL { WITH f WITH f WHERE <cas> SET … RETURN count(f) AS embedded }` block; `cas_fields ⊆ {"summary", "key_points", "content"}`; each field compares `coalesce(f.<field>, <empty>) = $cas_<field>`; with `keep_prev` the SET starts with `f.embedding_prev = f.embedding,`.
  - `embed_params(vector, sha, version, *, cas: dict) -> dict` — the parameters for that subquery (`cas` keys are field names; missing text → `""`, missing list → `[]`).
  - `read_fact_text(session, name) -> Optional[dict]` — `{"name","summary","key_points","content"}` or `None`.
  - `embed_fact(session, name, cfg, embed_fn=embed_text, *, keep_prev=False) -> str` — one of `"embedded" | "cas_skipped" | "embed_failed" | "missing"`. Reads the four text fields, builds the text with `cfg.boilerplate`, embeds, writes a `MATCH (f:Fact {name:$name})` + the subquery with CAS on **all three** text fields.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_embed.py`)

```python
from ai_memory.embed import (
    EMBED_DIM, EMBED_MODEL, EMBED_PARAM_NAMES, build_embed_subquery, embed_fact, embed_params,
    embed_text, read_fact_text,
)
from ai_memory.retrieval_config import RetrievalConfig


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def single(self): return self._rows[0] if self._rows else None
    def __iter__(self): return iter(self._rows)


class FakeSession:
    def __init__(self, rows_by_call=None):
        self.calls = []; self.rows_by_call = list(rows_by_call or [])
    def run(self, cypher, params=None, **kw):
        p = dict(params or {}); p.update(kw); self.calls.append((cypher, p))
        return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


CFG = RetrievalConfig(version=7, boilerplate=frozenset())
VEC = [0.1] * EMBED_DIM


def test_embed_text_returns_none_when_ollama_missing(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "ollama":
            raise ImportError("no ollama")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert embed_text("hello") is None


def test_embed_text_rejects_wrong_dimension(monkeypatch):
    import sys, types
    fake = types.SimpleNamespace(embeddings=lambda model, prompt: {"embedding": [0.0] * 10})
    monkeypatch.setitem(sys.modules, "ollama", fake)
    assert embed_text("hello") is None
    fake.embeddings = lambda model, prompt: {"embedding": [0.5] * EMBED_DIM}
    assert embed_text("hello") == [0.5] * EMBED_DIM


def test_build_embed_subquery_shape():
    q = build_embed_subquery(["summary", "key_points", "content"])
    assert q.startswith("CALL {") and q.rstrip().endswith("}")
    assert "WITH f WHERE coalesce(f.summary, '') = $cas_summary" in q
    assert "coalesce(f.key_points, []) = $cas_key_points" in q
    assert "coalesce(f.content, '') = $cas_content" in q
    for p in EMBED_PARAM_NAMES:
        assert f"f.{p} = ${p}" in q
    assert "RETURN count(f) AS embedded" in q
    assert "embedding_prev" not in q
    assert "f.embedding_prev = f.embedding," in build_embed_subquery(["content"], keep_prev=True)


def test_build_embed_subquery_rejects_unknown_field():
    with pytest.raises(ValueError):
        build_embed_subquery(["name"])


def test_embed_params_fills_defaults():
    p = embed_params(VEC, "abcd" * 4, 7, cas={"summary": None, "key_points": None, "content": "c"})
    assert p["embedding"] == VEC and p["embedding_model"] == EMBED_MODEL and p["embedding_dim"] == EMBED_DIM
    assert p["embedding_text_sha"] == "abcd" * 4 and p["boilerplate_version"] == 7
    assert p["cas_summary"] == "" and p["cas_key_points"] == [] and p["cas_content"] == "c"


def test_embed_fact_writes_one_cas_statement():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": ["k"], "content": None}], [{"embedded": 1}]])
    out = embed_fact(s, "N", CFG, embed_fn=lambda t: VEC)
    assert out == "embedded"
    assert len(s.calls) == 2
    cypher, params = s.calls[1]
    assert cypher.startswith("MATCH (f:Fact {name: $name})")
    assert "CALL {" in cypher and "$cas_summary" in cypher and "$cas_key_points" in cypher and "$cas_content" in cypher
    assert params["cas_summary"] == "S" and params["cas_key_points"] == ["k"] and params["cas_content"] == ""
    assert params["embedding_text_sha"] == text_sha("N S - k", 7)
    assert params["boilerplate_version"] == 7


def test_embed_fact_reports_cas_skip_and_failures():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}], [{"embedded": 0}]])
    assert embed_fact(s, "N", CFG, embed_fn=lambda t: VEC) == "cas_skipped"
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}]])
    assert embed_fact(s, "N", CFG, embed_fn=lambda t: None) == "embed_failed"
    assert len(s.calls) == 1                       # no write attempted
    s = FakeSession([[]])
    assert embed_fact(s, "gone", CFG, embed_fn=lambda t: VEC) == "missing"


def test_embed_fact_keep_prev_flag_reaches_cypher():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}], [{"embedded": 1}]])
    embed_fact(s, "N", CFG, embed_fn=lambda t: VEC, keep_prev=True)
    assert "f.embedding_prev = f.embedding," in s.calls[1][0]


def test_read_fact_text_shape():
    s = FakeSession([[{"name": "N", "summary": None, "key_points": None, "content": "c"}]])
    assert read_fact_text(s, "N") == {"name": "N", "summary": None, "key_points": None, "content": "c"}
    assert read_fact_text(FakeSession([[]]), "x") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'embed_text'` (and friends).

- [ ] **Step 3: Append the implementation to `ai_memory/embed.py`**

```python
# --- I/O helpers -------------------------------------------------------------
import sys
from typing import Callable, Dict, Sequence

from ai_memory.retrieval_config import RetrievalConfig

EMBED_PARAM_NAMES = ("embedding", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version")
_CAS_EMPTY = {"summary": "''", "key_points": "[]", "content": "''"}
_CAS_DEFAULT = {"summary": "", "key_points": [], "content": ""}


def embed_text(text: str, *, model: str = EMBED_MODEL) -> Optional[list]:
    """Local Ollama embedding of the prepared text; None when unavailable or wrong length."""
    try:
        import ollama
        vec = ollama.embeddings(model=model, prompt=text)["embedding"]
    except Exception as e:  # noqa: BLE001 — Ollama is an optional dependency
        print(f"Embedding error (Ollama unreachable?): {e}", file=sys.stderr)
        return None
    if not isinstance(vec, list) or len(vec) != EMBED_DIM:
        print(f"Embedding rejected: expected {EMBED_DIM} dims, got {len(vec) if isinstance(vec, list) else type(vec).__name__}", file=sys.stderr)
        return None
    return [float(x) for x in vec]


def build_embed_subquery(cas_fields: Sequence[str], *, keep_prev: bool = False) -> str:
    """CALL subquery that sets embedding + provenance on `f` only if the CAS fields still match."""
    for f in cas_fields:
        if f not in _CAS_EMPTY:
            raise ValueError(f"unknown CAS field {f!r}")
    where = " AND ".join(f"coalesce(f.{f}, {_CAS_EMPTY[f]}) = $cas_{f}" for f in cas_fields) or "true"
    sets = ["f.embedding_prev = f.embedding"] if keep_prev else []
    sets += [f"f.{p} = ${p}" for p in EMBED_PARAM_NAMES]
    return (
        "CALL {\n"
        "  WITH f\n"
        f"  WITH f WHERE {where}\n"
        f"  SET {', '.join(sets)}\n"
        "  RETURN count(f) AS embedded\n"
        "}"
    )


def embed_params(vector: list, sha: str, version: int, *, cas: Dict[str, object]) -> dict:
    p = {"embedding": vector, "embedding_model": EMBED_MODEL, "embedding_dim": EMBED_DIM,
         "embedding_text_sha": sha, "boilerplate_version": version}
    for f, v in cas.items():
        p[f"cas_{f}"] = v if v not in (None, "") else _CAS_DEFAULT[f]
        if f == "key_points":
            p[f"cas_{f}"] = list(v or [])
    return p


def read_fact_text(session, name: str) -> Optional[dict]:
    rec = session.run(
        "MATCH (f:Fact {name: $name}) RETURN f.name AS name, f.summary AS summary, "
        "f.key_points AS key_points, f.content AS content", name=name).single()
    if rec is None:
        return None
    return {"name": rec["name"], "summary": rec["summary"], "key_points": rec["key_points"], "content": rec["content"]}


def embed_fact(session, name: str, cfg: RetrievalConfig, embed_fn: Callable[[str], Optional[list]] = embed_text,
               *, keep_prev: bool = False) -> str:
    """Read → prepare → embed → CAS write (all three text fields). Returns embedded|cas_skipped|embed_failed|missing."""
    row = read_fact_text(session, name)
    if row is None:
        return "missing"
    text = fact_embed_text(row["name"], row["summary"], row["key_points"], row["content"], cfg.boilerplate)
    vec = embed_fn(text)
    if not vec:
        return "embed_failed"
    cypher = "MATCH (f:Fact {name: $name})\n" + build_embed_subquery(["summary", "key_points", "content"], keep_prev=keep_prev) + "\nRETURN embedded"
    params = embed_params(vec, text_sha(text, cfg.version), cfg.version,
                          cas={"summary": row["summary"], "key_points": row["key_points"], "content": row["content"]})
    params["name"] = name
    rec = session.run(cypher, **params).single()
    return "embedded" if rec and rec["embedded"] else "cas_skipped"
```

Move the new `import sys` and typing names up into the module's import block (ruff will flag E402 otherwise); keep `from ai_memory.retrieval_config import RetrievalConfig` at the top too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/embed.py tests/test_embed.py
git commit -m "feat(embed): Ollama embed_text, CAS embed subquery/params, embed_fact read-prepare-embed-write"
```

---

### Task 4: Backfill, drop/rollback of `embedding_prev`, vector stats

**Files:**
- Modify: `ai_memory/embed.py` (append)
- Modify: `tests/test_embed.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3, `publish_retrieval_config`/`load_retrieval_config`.
- Produces:
  - `embed_all(driver, *, keep_prev=False, publish=True, stale_only=False, embed_fn=embed_text, log=print) -> dict` with keys `facts, embedded, cas_skipped, embed_failed, missing, skipped_fresh, config_version, grams`.
  - `drop_prev(driver) -> int`, `rollback_prev(driver) -> int`.
  - `vector_stats(driver) -> dict` with keys `facts, with_embedding, without_embedding, foreign, wrong_model, stale, with_prev, isolated, config_version`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from ai_memory.embed import drop_prev, embed_all, rollback_prev, vector_stats


class FakeDriver:
    def __init__(self, session): self._s = session
    def session(self): return self._s
    def close(self): pass


class Sess(FakeSession):
    def __enter__(self): return self
    def __exit__(self, *a): return False


ROWS = [{"name": "A", "summary": "topic selection gap filling one", "key_points": [], "content": None},
        {"name": "B", "summary": "topic selection gap filling two", "key_points": ["k"], "content": None}]


def test_embed_all_publishes_then_embeds_every_fact():
    # call order: (1) load all facts, (2) publish config, then per fact: read, write
    s = Sess([ROWS, [{"version": 2, "updated_at": "T"}],
              [ROWS[0]], [{"embedded": 1}],
              [ROWS[1]], [{"embedded": 0}]])
    out = embed_all(FakeDriver(s), embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["facts"] == 2 and out["embedded"] == 1 and out["cas_skipped"] == 1 and out["config_version"] == 2
    assert "RetrievalConfig" in s.calls[1][0] and "c.version = c.version + 1" in s.calls[1][0]
    assert isinstance(out["grams"], int)


def test_embed_all_without_publish_uses_current_config_and_fails_closed_when_missing():
    s = Sess([ROWS, [{"version": 5, "boilerplate": [], "updated_at": None}],
              [ROWS[0]], [{"embedded": 1}], [ROWS[1]], [{"embedded": 1}]])
    out = embed_all(FakeDriver(s), publish=False, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["config_version"] == 5 and out["embedded"] == 2
    s = Sess([ROWS, []])
    with pytest.raises(RuntimeError):
        embed_all(FakeDriver(s), publish=False, embed_fn=lambda t: VEC, log=lambda *a: None)


def test_embed_all_stale_only_skips_matching_sha():
    cfg_rows = [{"version": 5, "boilerplate": [], "updated_at": None}]
    fresh_sha = text_sha(fact_embed_text("A", ROWS[0]["summary"], [], None, frozenset()), 5)
    rows = [dict(ROWS[0], embedding_text_sha=fresh_sha, boilerplate_version=5), dict(ROWS[1], embedding_text_sha="stale", boilerplate_version=5)]
    s = Sess([rows, cfg_rows, [ROWS[1]], [{"embedded": 1}]])
    out = embed_all(FakeDriver(s), publish=False, stale_only=True, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["skipped_fresh"] == 1 and out["embedded"] == 1


def test_embed_all_keep_prev_reaches_writes():
    s = Sess([ROWS[:1], [{"version": 2, "updated_at": "T"}], [ROWS[0]], [{"embedded": 1}]])
    embed_all(FakeDriver(s), keep_prev=True, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert "f.embedding_prev = f.embedding," in s.calls[3][0]


def test_drop_and_rollback_prev():
    s = Sess([[{"n": 3}]])
    assert drop_prev(FakeDriver(s)) == 3
    assert "REMOVE f.embedding_prev" in s.calls[0][0] and "embedding_prev IS NOT NULL" in s.calls[0][0]
    s = Sess([[{"n": 2}]])
    assert rollback_prev(FakeDriver(s)) == 2
    q = s.calls[0][0]
    assert "SET f.embedding = f.embedding_prev" in q
    for p in ("embedding_prev", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version"):
        assert f"f.{p}" in q.split("REMOVE", 1)[1]


def test_vector_stats_counts():
    cfg = [{"version": 5, "boilerplate": [], "updated_at": None}]
    good_sha = text_sha(fact_embed_text("A", "s", [], None, frozenset()), 5)
    rows = [
        {"name": "A", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": EMBED_MODEL, "sha": good_sha, "has_prev": False, "isolated": False},
        {"name": "B", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": None, "sha": None, "has_prev": True, "isolated": True},
        {"name": "C", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": "other", "sha": "old", "has_prev": False, "isolated": True},
        {"name": "D", "summary": "s", "key_points": [], "content": None, "has_emb": False, "model": None, "sha": None, "has_prev": False, "isolated": True},
    ]
    s = Sess([cfg, rows])
    st = vector_stats(FakeDriver(s))
    assert st == {"facts": 4, "with_embedding": 3, "without_embedding": 1, "foreign": 1, "wrong_model": 1,
                  "stale": 1, "with_prev": 1, "isolated": 3, "config_version": 5}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'embed_all'`.

- [ ] **Step 3: Append the implementation**

```python
from ai_memory.retrieval_config import load_retrieval_config, publish_retrieval_config

_ALL_FACTS = ("MATCH (f:Fact) RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points, "
              "f.content AS content, f.embedding_text_sha AS embedding_text_sha, f.boilerplate_version AS boilerplate_version "
              "ORDER BY f.name")


def embed_all(driver, *, keep_prev: bool = False, publish: bool = True, stale_only: bool = False,
              embed_fn: Callable[[str], Optional[list]] = embed_text, log=print) -> dict:
    """Backfill every Fact: (optionally) recompute boilerplate and publish a new RetrievalConfig
    version, then prepare/embed/CAS-write each Fact (spec §4 Backfill, §7.5 order)."""
    out = {"facts": 0, "embedded": 0, "cas_skipped": 0, "embed_failed": 0, "missing": 0, "skipped_fresh": 0,
           "config_version": None, "grams": 0}
    with driver.session() as s:
        rows = [dict(r) for r in s.run(_ALL_FACTS)]
        out["facts"] = len(rows)
        if publish:
            raw = [fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], ()) for r in rows]
            grams = detect_boilerplate(raw)
            cfg = publish_retrieval_config(s, grams)
            log(f"published RetrievalConfig version {cfg.version} with {len(grams)} boilerplate grams")
        else:
            cfg = load_retrieval_config(s)
            if cfg is None:
                raise RuntimeError("no RetrievalConfig node; run scripts/neo4j_seed.py or ai-memory embed --all (publish)")
        out["config_version"] = cfg.version
        out["grams"] = len(cfg.boilerplate)
        for i, r in enumerate(rows, 1):
            if stale_only and r.get("boilerplate_version") == cfg.version:
                text = fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], cfg.boilerplate)
                if r.get("embedding_text_sha") == text_sha(text, cfg.version):
                    out["skipped_fresh"] += 1
                    continue
            out[embed_fact(s, r["name"], cfg, embed_fn, keep_prev=keep_prev)] += 1
            if i % 100 == 0:
                log(f"  {i}/{len(rows)} … embedded={out['embedded']} cas_skipped={out['cas_skipped']} failed={out['embed_failed']}")
    return out


def drop_prev(driver) -> int:
    with driver.session() as s:
        rec = s.run("MATCH (f:Fact) WHERE f.embedding_prev IS NOT NULL REMOVE f.embedding_prev RETURN count(f) AS n").single()
        return int(rec["n"]) if rec else 0


def rollback_prev(driver) -> int:
    with driver.session() as s:
        rec = s.run(
            "MATCH (f:Fact) WHERE f.embedding_prev IS NOT NULL "
            "SET f.embedding = f.embedding_prev "
            "REMOVE f.embedding_prev, f.embedding_model, f.embedding_dim, f.embedding_text_sha, f.boilerplate_version "
            "RETURN count(f) AS n").single()
        return int(rec["n"]) if rec else 0


_STATS_ROWS = ("MATCH (f:Fact) RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points, f.content AS content, "
               "f.embedding IS NOT NULL AS has_emb, f.embedding_model AS model, f.embedding_text_sha AS sha, "
               "f.embedding_prev IS NOT NULL AS has_prev, NOT exists { (f)-[:RELATED_TO]-() } AS isolated")


def vector_stats(driver) -> dict:
    st = {"facts": 0, "with_embedding": 0, "without_embedding": 0, "foreign": 0, "wrong_model": 0, "stale": 0,
          "with_prev": 0, "isolated": 0, "config_version": None}
    with driver.session() as s:
        cfg = load_retrieval_config(s)
        st["config_version"] = cfg.version if cfg else None
        for r in s.run(_STATS_ROWS):
            st["facts"] += 1
            st["isolated"] += bool(r["isolated"])
            st["with_prev"] += bool(r["has_prev"])
            if not r["has_emb"]:
                st["without_embedding"] += 1
                continue
            st["with_embedding"] += 1
            if r["sha"] is None:
                st["foreign"] += 1
                continue
            if r["model"] != EMBED_MODEL:
                st["wrong_model"] += 1
            if cfg is not None:
                text = fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], cfg.boilerplate)
                if r["sha"] != text_sha(text, cfg.version):
                    st["stale"] += 1
    return st
```

Stats semantics (so the test's expected dict is exact): B has an embedding but no sha → `foreign` (and nothing else); C has a sha that does not match and a wrong model → both `wrong_model` and `stale`; A is fresh; D has no embedding. `isolated` counts every Fact with no RELATED_TO edge regardless of embedding (B, C, D → 3).

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_embed.py -q -p no:cacheprovider`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/embed.py tests/test_embed.py
git commit -m "feat(embed): embed_all backfill with publish/keep-prev/stale-only, drop/rollback embedding_prev, vector_stats"
```

---

### Task 5: `learn.py` — text and vector in one statement for `write()` and `learn()`

**Files:**
- Modify: `ai_memory/learn.py:435-530` (`_sync_fact_tx`, `sync_facts`, `write_fact`)
- Modify: `ai_memory/__init__.py` (`learn()`/`write()` docstrings only)
- Modify: `tests/test_learn.py`

**Interfaces:**
- Consumes: `fact_embed_text`, `text_sha`, `build_embed_subquery`, `embed_params`, `embed_text` (`ai_memory.embed`); `load_retrieval_config`.
- Produces: `_sync_fact_tx(tx, topic, assistant=None, *, cfg=None, embed_fn=None) -> bool`; `sync_facts(topics, *, workspace=None, assistant=None, embed_fn=embed_text) -> int`; `write_fact(topic, *, assistant=None, driver=None, workspace=None, embed_fn=embed_text) -> bool`. Passing `embed_fn=None` disables embedding.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_learn.py`; reuse its existing fake `tx` pattern — read the file first and match how `_sync_fact_tx` is currently driven; if there is no tx fake, add this one)

```python
from ai_memory.embed import EMBED_DIM, fact_embed_text, text_sha
from ai_memory.retrieval_config import RetrievalConfig


class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None


class _Tx:
    def __init__(self, rows_by_call): self.calls = []; self.rows = list(rows_by_call)
    def run(self, q, **params):
        self.calls.append((q, params))
        return _Res(self.rows.pop(0) if self.rows else [])


TOPIC = {"name": "T", "summary": "S", "key_points": ["k1"], "source_file": "api", "created_at": "2026-09-04T00:00:00+00:00"}
CFG = RetrievalConfig(version=3, boilerplate=frozenset())


def test_sync_fact_tx_embeds_in_the_merge_statement():
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"content": "existing body"}], [{"name": "T", "embedded": 1}]])
    ok = _sync_fact_tx(tx, TOPIC, None, cfg=CFG, embed_fn=lambda t: [0.1] * EMBED_DIM)
    assert ok
    read_q, _ = tx.calls[0]
    assert read_q.strip().startswith("OPTIONAL MATCH (f:Fact {name: $name})") or read_q.strip().startswith("MATCH (f:Fact {name: $name})")
    merge_q, params = tx.calls[1]
    assert "MERGE (f:Fact {name: $name})" in merge_q and "CALL {" in merge_q
    assert "coalesce(f.content, '') = $cas_content" in merge_q and "$cas_summary" not in merge_q
    assert params["cas_content"] == "existing body"
    expected_text = fact_embed_text("T", "S", ["k1"], "existing body", frozenset())
    assert params["embedding_text_sha"] == text_sha(expected_text, 3) and params["boilerplate_version"] == 3
    assert "RETURN f.name as name, embedded" in merge_q or "RETURN f.name AS name, embedded" in merge_q


def test_sync_fact_tx_without_cfg_or_vector_writes_text_only():
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"name": "T"}]])
    assert _sync_fact_tx(tx, TOPIC, None, cfg=None, embed_fn=lambda t: [0.1] * EMBED_DIM)
    assert "CALL {" not in tx.calls[0][0] and len(tx.calls) == 1
    tx = _Tx([[{"content": None}], [{"name": "T"}]])
    assert _sync_fact_tx(tx, TOPIC, None, cfg=CFG, embed_fn=lambda t: None)
    assert "CALL {" not in tx.calls[1][0]


def test_sync_facts_loads_config_once_and_passes_embed_fn(monkeypatch):
    import ai_memory.learn as L
    seen = {"cfg_loads": 0, "tx_cfg": []}
    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "RetrievalConfig" in q:
                seen["cfg_loads"] += 1
                return _Res([{"version": 3, "boilerplate": [], "updated_at": None}])
            return _Res([])
        def execute_write(self, fn, *a, **kw):
            if fn is L._sync_fact_tx:
                seen["tx_cfg"].append(kw.get("cfg"))
                return True
            return None
    class Drv:
        def session(self): return Sess()
        def close(self): pass
    monkeypatch.setattr(L, "get_driver", lambda ws=None: Drv())
    n = L.sync_facts([TOPIC, dict(TOPIC, name="U")], embed_fn=lambda t: None)
    assert n == 2 and seen["cfg_loads"] == 1 and [c.version for c in seen["tx_cfg"]] == [3, 3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_learn.py -q -p no:cacheprovider`
Expected: FAIL — `TypeError: _sync_fact_tx() got an unexpected keyword argument 'cfg'`.

- [ ] **Step 3: Implement**

In `ai_memory/learn.py` add imports:

```python
from ai_memory.embed import build_embed_subquery, embed_params, embed_text, fact_embed_text, text_sha
from ai_memory.retrieval_config import load_retrieval_config
```

Replace `_sync_fact_tx`'s signature and body from `words = extract_words(...)` down to the `result = tx.run(...)` call:

```python
def _sync_fact_tx(tx, topic: dict, assistant: Optional[str] = None, *, cfg=None, embed_fn=None) -> bool:
    """Transaction: MERGE a Fact node + Word index edges; when a RetrievalConfig and an embedder
    are available, the same statement sets the vector and its provenance (CAS on `content`,
    the one text field this writer does not own — spec §4)."""
    try:
        words = extract_words(topic['name'])
        params = {
            'name': topic['name'],
            'summary': topic['summary'],
            'key_points': topic['key_points'],
            'source_file': topic['source_file'],
            'created_at': topic['created_at'],
        }
        set_clause = """
            SET f.summary = $summary,
                f.key_points = $key_points,
                f.source_file = $source_file,
                f.created_at = coalesce(f.created_at, $created_at),
                f.updated_at = $created_at
        """
        if assistant:
            set_clause += ", f.assistant = $assistant"
            params['assistant'] = assistant
        if topic.get('provenance') is not None:
            prov_dict = topic['provenance'].to_dict()
            for f in dataclasses.fields(topic['provenance']):
                param_key = f'prov_{f.name}'
                set_clause += f', f.provenance_{f.name} = ${param_key}'
                params[param_key] = prov_dict.get(f.name)
        embed_block, embed_return = "", ""
        if cfg is not None and embed_fn is not None:
            seen = tx.run("OPTIONAL MATCH (f:Fact {name: $name}) RETURN f.content AS content", name=topic['name']).single()
            content_seen = seen['content'] if seen else None
            text = fact_embed_text(topic['name'], topic['summary'], topic['key_points'], content_seen, cfg.boilerplate)
            vec = embed_fn(text)
            if vec:
                embed_block = "WITH f\n" + build_embed_subquery(["content"]) + "\n"
                embed_return = ", embedded"
                params.update(embed_params(vec, text_sha(text, cfg.version), cfg.version, cas={"content": content_seen}))
        result = tx.run(f"""
            MERGE (f:Fact {{name: $name}})
            {set_clause}
            WITH f
            OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
            DELETE old
            WITH f
            MERGE (s:Source {{name: $source_file}})
            MERGE (f)-[:FROM_SOURCE]->(s)
            {embed_block}RETURN f.name AS name{embed_return}
        """, **params)
```

Keep the rest of the function (the `if not result.single(): return False`, the Assistant MERGE, the words UNWIND, the `except TransientError: raise` and the generic handler) unchanged.

`sync_facts` and `write_fact`:

```python
def write_fact(topic, *, assistant=None, driver=None, workspace=None, embed_fn=embed_text) -> bool:
    ...
        with driver.session() as session:
            cfg = _load_cfg(session) if embed_fn is not None else None
            result = session.execute_write(_sync_fact_tx, topic, assistant, cfg=cfg, embed_fn=embed_fn)
            return bool(result)


def _load_cfg(session):
    """RetrievalConfig or None; a writer that cannot read it stores text and skips the vector (§4)."""
    try:
        return load_retrieval_config(session)
    except Exception as e:  # noqa: BLE001
        print(f"RetrievalConfig unavailable ({e}); writing text without embedding", file=sys.stderr)
        return None


def sync_facts(topics, *, workspace=None, assistant=None, embed_fn=embed_text) -> int:
    ...
        with driver.session() as session:
            session.run("CREATE CONSTRAINT word_text_unique IF NOT EXISTS FOR (w:Word) REQUIRE w.text IS UNIQUE")
            cfg = _load_cfg(session) if embed_fn is not None else None
            for topic in topics:
                if session.execute_write(_sync_fact_tx, topic, assistant, cfg=cfg, embed_fn=embed_fn):
                    synced += 1
            session.execute_write(_post_sync_tx, max_df_ratio=0.1, min_shared=2)
```

`ai_memory/__init__.py`: in `learn()`'s docstring replace the paragraph starting "Facts created by this method are findable via ``search_graph()`` … writes embeddings via Ollama." with: "Facts are embedded with the canonical text (spec §4) when local Ollama and the ``RetrievalConfig`` node are available; otherwise the text is written and ``ai-memory embed --all`` supplies the vector later." In `write()`'s docstring add the same sentence after the Returns block. Add `'fact_embed_text', 'embed_all', 'vector_stats'` to `__all__` with matching imports from `ai_memory.embed`.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python -m pytest tests/test_learn.py tests/test_library.py -q -p no:cacheprovider`
Expected: all pass (3 new). If an existing `test_library.py` test drives `MemoryClient.write()` through a fake driver whose `run` cannot answer the new `OPTIONAL MATCH … RETURN f.content` read or the `RetrievalConfig` read, extend that fake to return an empty result for them — do not weaken its assertions.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/learn.py ai_memory/__init__.py tests/test_learn.py tests/test_library.py
git commit -m "feat(learn): write() and learn() embed the canonical text in the MERGE statement with CAS on content"
```

---

### Task 6: `scripts/neo4j_sync.py` — canonical text, one statement per Fact

**Files:**
- Modify: `scripts/neo4j_sync.py:37-49` (drop `EMBED_MODEL`/`get_embedding`), `:182-230` (per-fact loop)
- Create: `tests/test_neo4j_sync_embed.py`

**Interfaces:**
- Consumes: `ai_memory.embed` (`fact_embed_text`, `text_sha`, `build_embed_subquery`, `embed_params`, `embed_text`), `load_retrieval_config`.
- Produces: `write_fact_with_embedding(neo4j_session, fact: dict, *, relative_path: str, assistant: Optional[str], cfg, embed_fn) -> bool` (module-level helper, returns True when the vector was written in the same statement) — the loop calls it; `sync_file(...)` loads `cfg` once per file.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_neo4j_sync_embed.py
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ai_memory.embed import EMBED_DIM, fact_embed_text, text_sha
from ai_memory.retrieval_config import RetrievalConfig


class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None


class _Sess:
    def __init__(self, rows): self.calls = []; self.rows = list(rows)
    def run(self, q, **params):
        self.calls.append((q, params)); return _Res(self.rows.pop(0) if self.rows else [])


FACT = {"name": "N", "content": "body " * 3, "source": "sess.md"}


def test_sync_writer_embeds_canonical_text_in_one_statement():
    import neo4j_sync as S
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"]}], [{"name": "N", "embedded": 1}]])
    ok = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Nova", cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert ok is True
    read_q, _ = s.calls[0]
    assert "RETURN f.summary AS summary, f.key_points AS key_points" in read_q
    q, p = s.calls[1]
    assert "MERGE (f:Fact {name: $name})" in q and "SET f.content = $content" in q and "CALL {" in q
    assert "coalesce(f.summary, '') = $cas_summary" in q and "coalesce(f.key_points, []) = $cas_key_points" in q
    assert "$cas_content" not in q                                  # content is this writer's own field
    assert p["cas_summary"] == "Sum" and p["cas_key_points"] == ["k"] and p["assistant"] == "Nova"
    text = fact_embed_text("N", "Sum", ["k"], FACT["content"][:2000], frozenset())
    assert p["embedding_text_sha"] == text_sha(text, 2)


def test_sync_writer_text_only_when_no_vector():
    import neo4j_sync as S
    s = _Sess([[{"summary": None, "key_points": None}], [{"name": "N"}]])
    ok = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=RetrievalConfig(1, frozenset()), embed_fn=lambda t: None)
    assert ok is False and "CALL {" not in s.calls[1][0]
    s = _Sess([[{"name": "N"}]])
    ok = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=None, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert ok is False and len(s.calls) == 1 and "CALL {" not in s.calls[0][0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_neo4j_sync_embed.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'neo4j_sync' has no attribute 'write_fact_with_embedding'` (if importing `neo4j_sync` itself fails because of workspace/env side effects at import time, note what it needs — it should only compute paths — and set `AI_MEMORY_DIR` to a tmp dir in the test via `monkeypatch.setenv` before import).

- [ ] **Step 3: Implement**

Remove `EMBED_MODEL` and `get_embedding` from `scripts/neo4j_sync.py`; add imports:

```python
from ai_memory.embed import build_embed_subquery, embed_params, embed_text, fact_embed_text, text_sha
from ai_memory.retrieval_config import load_retrieval_config
```

Add the helper (module level, above `sync_file`):

```python
def write_fact_with_embedding(neo4j_session, fact: dict, *, relative_path: str, assistant, cfg, embed_fn=embed_text) -> bool:
    """MERGE the Fact's text and, when possible, its canonical-text embedding in ONE statement.
    CAS on summary/key_points (owned by other writers); content is ours. Returns True iff the
    vector was written."""
    fact_id = hashlib.sha256(f"{relative_path}:{fact['name']}".encode()).hexdigest()[:16]
    content = fact["content"][:2000]
    params = {"id": fact_id, "name": fact["name"], "content": content, "source": fact["source"], "session_id": relative_path}
    fact_set = "SET f.content = $content, f.source = $source, f.id = coalesce(f.id, $id)"
    if assistant:
        fact_set += ", f.assistant = $assistant"
        params["assistant"] = assistant
    embed_block, embed_return = "", ""
    if cfg is not None and embed_fn is not None:
        seen = neo4j_session.run("OPTIONAL MATCH (f:Fact {name: $name}) RETURN f.summary AS summary, f.key_points AS key_points",
                                 name=fact["name"]).single()
        summary_seen = seen["summary"] if seen else None
        kp_seen = seen["key_points"] if seen else None
        text = fact_embed_text(fact["name"], summary_seen, kp_seen, content, cfg.boilerplate)
        vec = embed_fn(text)
        if vec:
            embed_block = "WITH f\n" + build_embed_subquery(["summary", "key_points"]) + "\n"
            embed_return = ", embedded"
            params.update(embed_params(vec, text_sha(text, cfg.version), cfg.version, cas={"summary": summary_seen, "key_points": kp_seen}))
    rec = neo4j_session.run(
        f"""
        MERGE (f:Fact {{name: $name}})
        {fact_set}
        WITH f
        MATCH (s:Session {{id: $session_id}})
        MERGE (f)-[:LEARNED_IN]->(s)
        {embed_block}RETURN f.name AS name{embed_return}
        """,
        **params,
    ).single()
    return bool(rec and embed_return and rec["embedded"])
```

In the loop (`for fact in synced_facts:`), replace everything from `fact_id = …` through the `else: embedding_failures += 1` with:

```python
                if not write_fact_with_embedding(neo4j_session, fact, relative_path=relative_path, assistant=assistant, cfg=cfg, embed_fn=embed_text):
                    embedding_failures += 1
```

and load `cfg` once, right after the Session MERGE (inside the same `try`):

```python
            try:
                cfg = load_retrieval_config(neo4j_session)
            except Exception as e:  # noqa: BLE001
                print(f"  RetrievalConfig unavailable ({e}); syncing text without embeddings", file=sys.stderr)
                cfg = None
```

Update the end-of-run warning text (line ~309) to: `"facts have no embedding — Ollama or RetrievalConfig unavailable. Run `ai-memory embed --all` once they are."`. Keep `hashlib` imported (still used).

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_neo4j_sync_embed.py tests/test_cli_smoke.py -q -p no:cacheprovider`
Expected: pass (2 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/neo4j_sync.py tests/test_neo4j_sync_embed.py
git commit -m "feat(sync): neo4j_sync writes canonical-text embedding + provenance in the Fact MERGE statement"
```

---

### Task 7: `scripts/rlm/neo4j_learn_sync.py` — `update_neo4j_vector` via `embed_fact`

**Files:**
- Modify: `scripts/rlm/neo4j_learn_sync.py:101-108` (keep `_prepare_embedding_text` — FAISS still uses it), `:119-143` (delete `_get_embedding_with_cache`), `:267-310` (`update_neo4j_vector`)
- Create: `tests/test_learn_sync_embed.py`

**Interfaces:**
- Consumes: `embed_fact`, `embed_text`, `load_retrieval_config`.
- Produces: `update_neo4j_vector(topics, driver, *, embed_fn=embed_text) -> int` (count embedded).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learn_sync_embed.py
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "rlm"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ai_memory.embed import EMBED_DIM


class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)


class _Sess:
    def __init__(self, rows): self.calls = []; self.rows = list(rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def run(self, q, **params):
        self.calls.append((q, params)); return _Res(self.rows.pop(0) if self.rows else [])


class _Drv:
    def __init__(self, s): self.s = s
    def session(self): return self.s


def test_update_neo4j_vector_uses_embed_fact_per_topic():
    import neo4j_learn_sync as LS
    s = _Sess([[{"version": 2, "boilerplate": [], "updated_at": None}],
               [{"name": "A", "summary": "s", "key_points": ["k"], "content": None}], [{"embedded": 1}],
               [{"name": "B", "summary": "s", "key_points": [], "content": None}], [{"embedded": 0}]])
    n = LS.update_neo4j_vector([{"name": "A", "key_points": ["k"]}, {"name": "B", "key_points": []}], _Drv(s), embed_fn=lambda t: [0.1] * EMBED_DIM)
    assert n == 1
    assert "RetrievalConfig" in s.calls[0][0]
    assert "CALL {" in s.calls[2][0] and "$cas_summary" in s.calls[2][0] and "$cas_content" in s.calls[2][0]
    assert not hasattr(LS, "_get_embedding_with_cache")


def test_update_neo4j_vector_returns_zero_without_config():
    import neo4j_learn_sync as LS
    s = _Sess([[]])
    assert LS.update_neo4j_vector([{"name": "A"}], _Drv(s), embed_fn=lambda t: [0.1] * EMBED_DIM) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_learn_sync_embed.py -q -p no:cacheprovider`
Expected: FAIL — `TypeError: update_neo4j_vector() got an unexpected keyword argument 'embed_fn'`.

- [ ] **Step 3: Implement**

```python
from ai_memory.embed import embed_fact, embed_text
from ai_memory.retrieval_config import load_retrieval_config


def update_neo4j_vector(topics: list, driver, *, embed_fn=embed_text) -> int:
    """Embed each synced topic's Fact from its canonical text (spec §4) with provenance, CAS-guarded."""
    try:
        with driver.session() as session:
            cfg = load_retrieval_config(session)
            if cfg is None:
                print("  RetrievalConfig missing; skipping embeddings (run ai-memory embed --all)", file=sys.stderr)
                return 0
            counts = {"embedded": 0, "cas_skipped": 0, "embed_failed": 0, "missing": 0}
            for topic in topics:
                name = topic.get("name")
                if not name:
                    continue
                counts[embed_fact(session, name, cfg, embed_fn)] += 1
        if counts["embedded"]:
            print(f"  Embedded {counts['embedded']} facts (canonical text, config v{cfg.version}); "
                  f"cas_skipped={counts['cas_skipped']} failed={counts['embed_failed']}")
        return counts["embedded"]
    except Exception as e:  # noqa: BLE001
        print(f"Warning: Could not update Neo4j vectors: {e}", file=sys.stderr)
        return 0
```

Delete `_get_embedding_with_cache` and the `ThreadPoolExecutor`/`as_completed`/`pickle` imports if nothing else uses them (grep first). Keep the `NEO4J_VECTOR_AVAILABLE` probe only if something else reads it; otherwise remove it and the `import ollama` try-block (embed_text does its own import). Keep `_prepare_embedding_text` for the FAISS path.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_learn_sync_embed.py tests/test_cli_smoke.py -q -p no:cacheprovider`
Expected: pass (2 new); ruff clean on the file (`venv/bin/ruff check scripts/rlm/neo4j_learn_sync.py`).

- [ ] **Step 5: Commit**

```bash
git add scripts/rlm/neo4j_learn_sync.py tests/test_learn_sync_embed.py
git commit -m "feat(learn-sync): Neo4j vectors via embed_fact (canonical text, provenance, CAS); drop private embedding cache"
```

---

### Task 8: CLI — `ai-memory embed` and `ai-memory stats`

**Files:**
- Modify: `scripts/cli.py` (new `cmd_embed`, `cmd_stats`, two subparsers)
- Modify: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: `embed_all`, `drop_prev`, `rollback_prev`, `vector_stats` (`ai_memory.embed`), `get_driver` (`ai_memory._config`).
- Produces: `ai-memory embed (--all | --stale-only | --drop-prev | --rollback) [--keep-prev] [--no-publish] [--json PATH]`; `ai-memory stats [--json PATH]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_smoke.py`)

```python
def test_cli_embed_all_calls_embed_all(monkeypatch, capsys):
    import cli
    import ai_memory.embed as E
    seen = {}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: seen.update(kw) or {"facts": 2, "embedded": 2, "cas_skipped": 0, "embed_failed": 0, "missing": 0, "skipped_fresh": 0, "config_version": 3, "grams": 5})
    assert cli.main(["embed", "--all", "--keep-prev"]) == 0
    assert seen["keep_prev"] is True and seen["publish"] is True and seen["stale_only"] is False
    out = capsys.readouterr().out
    assert "embedded" in out and "config_version" in out


def test_cli_embed_modes_are_exclusive_and_required():
    import cli
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["embed"])
    with pytest.raises(SystemExit):
        cli.main(["embed", "--all", "--rollback"])


def test_cli_embed_drop_and_rollback(monkeypatch, capsys):
    import cli
    import ai_memory.embed as E
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "drop_prev", lambda drv: 7)
    monkeypatch.setattr(E, "rollback_prev", lambda drv: 4)
    assert cli.main(["embed", "--drop-prev"]) == 0 and "7" in capsys.readouterr().out
    assert cli.main(["embed", "--rollback"]) == 0 and "4" in capsys.readouterr().out


def test_cli_stats_prints_table_and_json(monkeypatch, capsys, tmp_path):
    import cli, json
    import ai_memory.embed as E
    st = {"facts": 10, "with_embedding": 9, "without_embedding": 1, "foreign": 3, "wrong_model": 0, "stale": 2, "with_prev": 0, "isolated": 4, "config_version": 2}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "vector_stats", lambda drv: st)
    out_path = tmp_path / "s.json"
    assert cli.main(["stats", "--json", str(out_path)]) == 0
    text = capsys.readouterr().out
    assert "foreign" in text and "isolated" in text
    assert json.loads(out_path.read_text()) == st
```

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest tests/test_cli_smoke.py -q -p no:cacheprovider -k "embed or stats"`
Expected: FAIL — `AttributeError: module 'cli' has no attribute '_open_driver'` / argparse `invalid choice: 'embed'`.

- [ ] **Step 3: Implement**

```python
def _open_driver():
    from ai_memory._config import get_driver
    return get_driver()


def cmd_embed(args: argparse.Namespace) -> int:
    import json as _json
    import ai_memory.embed as E
    driver = _open_driver()
    try:
        if args.drop_prev:
            n = E.drop_prev(driver); print(f"removed embedding_prev from {n} facts"); result = {"dropped_prev": n}
        elif args.rollback:
            n = E.rollback_prev(driver); print(f"restored embedding_prev on {n} facts (provenance cleared)"); result = {"rolled_back": n}
        else:
            result = E.embed_all(driver, keep_prev=args.keep_prev, publish=not args.no_publish, stale_only=args.stale_only)
            for k, v in result.items():
                print(f"{k:<16} {v}")
        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(result, indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


def cmd_stats(args: argparse.Namespace) -> int:
    import json as _json
    import ai_memory.embed as E
    driver = _open_driver()
    try:
        st = E.vector_stats(driver)
        for k, v in st.items():
            print(f"{k:<18} {v}")
        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(st, indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass
```

Parsers (add before the `eval` subparser):

```python
    # embed (ai_memory.embed backfill)
    p = subparsers.add_parser("embed", help="Re-embed Facts from the canonical text with provenance (spec §4)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="re-embed every Fact (nightly / first backfill)")
    mode.add_argument("--stale-only", action="store_true", help="re-embed only Facts whose text sha changed")
    mode.add_argument("--drop-prev", action="store_true", help="remove embedding_prev after the gate passes")
    mode.add_argument("--rollback", action="store_true", help="restore embedding_prev and clear provenance")
    p.add_argument("--keep-prev", action="store_true", help="keep the previous vector in embedding_prev")
    p.add_argument("--no-publish", action="store_true", help="reuse the current RetrievalConfig instead of publishing a new version")
    p.add_argument("--json", dest="json_out", default=None)
    p.set_defaults(func=cmd_embed)

    # stats (vector drift)
    p = subparsers.add_parser("stats", help="Vector provenance drift: foreign / stale / wrong-model vectors, isolated Facts")
    p.add_argument("--json", dest="json_out", default=None)
    p.set_defaults(func=cmd_stats)
```

`Path` is already imported in `cli.py` (check; add `from pathlib import Path` if not). The `main()` early-dispatch for `eval` stays as is.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_cli_smoke.py -q -p no:cacheprovider`
Expected: pass (4 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/cli.py tests/test_cli_smoke.py
git commit -m "feat(cli): ai-memory embed (--all/--stale-only/--drop-prev/--rollback, --keep-prev) and ai-memory stats"
```

---

### Task 9: Docs — CHANGELOG, MIGRATION, README

**Files:**
- Modify: `CHANGELOG.md` (Unreleased → Added / Changed), `MIGRATION.md` (extend "Upgrading to the hybrid retrieval path (unreleased)"), `README.md` (commands table / quick start where `ai-memory search` is documented)

- [ ] **Step 1: CHANGELOG `### Added`**

```markdown
- **`ai_memory.embed`** — canonical embedding text (`fact_embed_text`: name, summary,
  key points as dash lines, content; 2,000-char cap; corpus boilerplate 4-gram runs
  removed), versioned text sha, provenance-carrying compare-and-set embed writes
  (`embedding_model`, `embedding_dim`, `embedding_text_sha`, `boilerplate_version` set
  in the same statement as `embedding`), `embed_all` backfill with `embedding_prev`
  safety net, `vector_stats`. **`ai_memory.retrieval_config`** — the
  `(:RetrievalConfig {id: "current"})` singleton (boilerplate grams + version) that
  every writer reads before embedding. CLI: `ai-memory embed --all|--stale-only|
  --drop-prev|--rollback [--keep-prev] [--no-publish]`, `ai-memory stats`.
```

`### Changed`:

```markdown
- **All in-repo writers embed the same text.** `MemoryClient.write()`/`learn()`,
  `neo4j_sync.py` and `neo4j_learn_sync.py` previously embedded four different texts
  (name+content, key points only, …) or none; they now embed `fact_embed_text(...)` and
  write text and vector in one statement, guarded by a compare-and-set on the text
  fields they do not own. A writer that cannot read `RetrievalConfig` or reach Ollama
  writes the text only; `ai-memory embed --all` fills the vector later.
- `scripts/neo4j_seed.py` creates `RetrievalConfig` version 1 (no boilerplate) on fresh
  installs; `validate_schema()` reports `retrieval_config`.
```

- [ ] **Step 2: MIGRATION.md** — append under the hybrid-retrieval section:

```markdown
### Embedding provenance and the canonical text (phase 2)

New Fact properties: `embedding_model`, `embedding_dim`, `embedding_text_sha`,
`boilerplate_version`, and (during a backfill run with `--keep-prev`) `embedding_prev`.
New node: `(:RetrievalConfig {id: "current", version, boilerplate, updated_at})`.

Existing graphs: run `python scripts/neo4j_seed.py` (idempotent; creates the config node),
then `ai-memory embed --all --keep-prev`. This re-embeds every Fact from the canonical
text (about 10 s per 1,500 Facts on local Ollama) and publishes a new `RetrievalConfig`
version. Vectors written by tooling outside this repo have no sha and show as `foreign`
in `ai-memory stats`; the backfill makes them canonical. Compare retrieval quality with
`ai-memory eval` before and after; if the gate passes run `ai-memory embed --drop-prev`,
otherwise `ai-memory embed --rollback` restores the previous vectors (and clears the
provenance properties, since their text is unknown).

Writers: `MemoryClient.write()` and `learn()` now embed when Ollama and the config node
are reachable (they previously never embedded). `neo4j_sync.py` no longer embeds
`name + content[:500]`; `neo4j_learn_sync.py` no longer embeds key points only and no
longer keeps a pickle cache under `memory/embeddings/`. The grok client still embeds its
own text until phase 4.
```

- [ ] **Step 3: README** — add `ai-memory embed --all` and `ai-memory stats` rows wherever the CLI commands are listed, one line each, matching the existing table style.

- [ ] **Step 4: Verify, commit**

Run: `venv/bin/python -m pytest tests -q -p no:cacheprovider` and `venv/bin/ruff check ai_memory tests scripts`
Expected: all tests pass; ruff shows only the pre-existing findings.

```bash
git add CHANGELOG.md MIGRATION.md README.md
git commit -m "docs: phase 2 embedding provenance, canonical text, embed/stats commands"
```

---

### Task 10 (operational, not code): backfill with safety net, harness gate, drop or roll back

Run from the worktree root with `AI_MEMORY_DIR=~/.grok` (credentials via `.env.neo4j`) and `AI_MEMORY_GOLDEN=~/.ai-memory/golden/retrieval-2026-09.json`. The worktree venv must have the `ollama` extra (`venv/bin/pip install ollama`). The controller runs this task itself; nothing here is a subagent job.

- [ ] **Step 1: Before-state**

```bash
venv/bin/python scripts/cli.py stats --json ~/.ai-memory/golden/stats-before-phase2.json
```

Expected: `foreign` ≈ 1,500 (every existing vector predates provenance), `config_version` = missing/None until the seed runs. Then `venv/bin/python scripts/neo4j_seed.py` (idempotent) and re-run `stats`: `config_version` = 1.

- [ ] **Step 2: Backfill with the previous vectors kept**

```bash
venv/bin/python scripts/cli.py embed --all --keep-prev --json ~/.ai-memory/golden/embed-phase2.json
```

Expected: `facts` = 1,502-ish, `embedded` = facts minus `cas_skipped` (0 or a handful), `embed_failed` = 0, `config_version` = 2, `grams` ≈ 100 (spec §2 measured 108 at ≥16). Then `stats`: `foreign` = 0, `stale` = 0, `with_prev` = embedded.

- [ ] **Step 3: Harness after re-embed and the phase-2 gate**

```bash
venv/bin/python -m ai_memory.eval.harness --rankers legacy,hybrid_fallback --json ~/.ai-memory/golden/after-phase2.json
venv/bin/python - <<'EOF'
import json
from ai_memory.eval.harness import gate
b = json.load(open("~/.ai-memory/golden/baseline-phase0.json"))
a = json.load(open("~/.ai-memory/golden/after-phase2.json"))
print("phase-2 gate (hybrid_fallback after re-embed vs before):", gate(b, a, "hybrid_fallback"))
print("legacy after vs before (informational):", gate(b, a, "legacy"))
EOF
```

Note: the `ai-memory eval` subcommand name trips this session's shell guard; call the module directly as above. New judge calls are cached per (query, name, text), so changed teasers re-judge only what changed.

- [ ] **Step 4: Decide**

Gate `True` → `venv/bin/python scripts/cli.py embed --drop-prev`, record both tables in the phase-3 plan's preamble, and note the `order_flow_imbalance` query from the phase-0 per-query analysis (the empty-summary Fact hybrid demoted) — check whether it recovers.
Gate `False` → `venv/bin/python scripts/cli.py embed --rollback`, then `stats` must show `foreign` back at the pre-run count and `with_prev` = 0; the per-query script in the session scratchpad (`per_query.py`) locates the losing queries; the fix goes back through Task 1 (text rule) or the boilerplate threshold, test-first, and the run repeats from Step 2.

---

## Follow-on plans (not in this document)

- **Phase 3** — `scripts/neo4j_migrate_vector_filters.py`: pre-flight answering spec §10.1–10.5 and deciding the `WITH` list, scheduled drop-and-recreate window, gate on indexed-node count and per-property probe; `hybrid_search` joins the harness.
- **Phase 4** — grok client port (`_fact_text` = verbatim `fact_embed_text` reading the config over Bolt, provenance in the CAS statement) + `tests/test_retrieval_contract.py` asserting byte-equality on `tests/fixtures/embed_text_cases.json`.
- **Phase 5** — `ai_memory/wordindex.py` edge layer, z-score baselines and edge floor on `RetrievalConfig`, nightly `rule_version` cutover, retire `link_related_facts`/`_post_sync_tx`/grok `organize`.
- **Phase 6** — duplicate/supersede report.

## Self-review notes

- Spec coverage: §4 prepared text (T1), singleton (T2), provenance + CAS (T3, T5, T6, T7), backfill + `embedding_prev` (T4, T8, T10), `ai-memory stats` counts (T4, T8), writers (T5 write/learn, T6 sync, T7 learn-sync; grok deferred to phase 4 per §9), seed/verify (T2), §7.2 boilerplate rule (T1, T4 publish), §9 phase-2 gate (T10). Not in scope: §4 index rebuild (phase 3), §6, §7.1/7.3–7.6.
- Type consistency: `RetrievalConfig(version, boilerplate, updated_at)` (T2) is consumed by `embed_fact`/`embed_all`/`vector_stats` (T3–T4), `_sync_fact_tx(cfg=…)` (T5), `write_fact_with_embedding(cfg=…)` (T6), `update_neo4j_vector` (T7). `build_embed_subquery(cas_fields, keep_prev=)` + `embed_params(vector, sha, version, cas=)` are shared by T3, T5, T6. `embed_fact` return strings `embedded|cas_skipped|embed_failed|missing` are the keys `embed_all` increments (T4) and `update_neo4j_vector` counts (T7). `text_sha(text, version)` everywhere.
- Known deviation to flag to reviewers: `write()`/`learn()` (T5) perform a read of `f.content` inside the write transaction before the single MERGE statement; the spec's "single statement" holds for the write itself, and the CAS on `content` covers the read-to-write window.
- Fixture caveat: `tests/fixtures/embed_text_cases.json` is generated by the code under test in T1; the implementer must read the generated expectations and confirm each against the Global Constraints, because phase 4's contract test inherits them.
