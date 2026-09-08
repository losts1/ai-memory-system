# Retrieval Phase 5 — Edge Layer (Word Index, Pair Score, On-Write, Nightly Cutover) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared-word cliques with the spec's edge rule — a z-scored blend of TF-IDF and embedding cosine, per-Fact top-5 above a per-corpus floor — maintained on write and rebuilt nightly under a `rule_version` cutover that retires the 5,102 legacy `RELATED_TO` edges, with the harness proving the judged edge quality did not drop.

**Architecture:** `ai_memory/wordindex.py` holds the pure rule (tokenizer, IDF, TF-IDF cosine, z-score blend, duplicate exclusion, picks, edge merge) plus two I/O layers: the numpy-backed nightly `rebuild_edges` (matrices over the whole corpus, baselines from 40,000 seeded random pairs, floor = blend p99, batched edge write, `rule_version` cutover) and the numpy-free `maintain_edges_for` used on every write (one server-side cosine row via `vector.similarity.cosine`, TF-IDF from the Word index, the written Fact's picks, and the "worst pick beaten" re-pick of neighbours). The Word index stays (`(:Word {text, idf})` + `HAS_WORD`), now written by the new tokenizer from the prepared text with `Fact.tfidf_norm` alongside. Baselines, floor and `rule_version` live on the `RetrievalConfig` singleton. `ai_memory/eval/edges.py` judges a seeded edge sample with the `edge` rubric for the gate. The legacy writers (`link_related_facts`, `_post_sync_tx`, grok `organize`'s 2-shared-words MERGE) are retired; grok `organize` re-runs the on-write rule for a mind's Facts using verbatim copies.

**Tech Stack:** Python ≥ 3.9 for the package (`numpy>=1.24` as a new optional extra `edges`, imported lazily only by `rebuild_edges`); Neo4j 2026.04 (`vector.similarity.cosine`, pattern comprehensions, `log()`); the local judge (`qwen3.8-27b-q6k`) for the edge sample.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` — §7.1–7.5 (tokenizer, boilerplate, pair score, on-write, nightly), §7.6 first sentence (duplicates excluded from a Fact's edge budget), §8 (per-rule edge sample of 30), §9 phase-5 row and Operations, §4 (`Word.idf`, config holds baselines and floor). Phases 0–4 landed on this branch (through 5c5595b).

## Measured (live, 2026-09-05)

- 1,502 Facts, all with embeddings (1 foreign); `Word` nodes 1,833; `HAS_WORD` 6,921; `RELATED_TO` 5,102, none versioned; legacy edge props `shared_keywords, shared_count` (grok adds `source`); isolated Facts 381 (25%); `traverse`/`trace_parameter` read only edge existence and `related_count`, never edge weights.
- `RETURN vector.similarity.cosine(a, b)` works; a per-Fact row `MATCH (f {name}), (g:Fact) WHERE g <> f AND g.embedding IS NOT NULL RETURN g.name, vector.similarity.cosine(f.embedding, g.embedding), [(g)-[:HAS_WORD]->(w) | w.text]` returns 1,501 rows in one round trip.
- Simulation (spec §7.7, `edge_sim2.py`): C = normalised-embedding cosine (0 where either side lacks a vector); T = cosine of L2-normalised binary TF-IDF rows (weight = ln(N/df)); baselines = mean/std/p50/p95/p99 of T, C and blend over 40,000 seeded random pairs with both sides embedded; blend = 0.5·zT + 0.5·zC; floor = blend p99; each Fact picks top 5 ≥ floor; result on this graph: 2% isolated, max degree 26 (baseline 25% / 93). Judged edge sample (edge rubric): current 94% related / 57% direct; z-blend p99 99% / 71%.
- numpy: absent from the worktree venv, present system-wide (2.2.6); the operational task installs it into the venv.

## Global Constraints

- `requires-python = ">=3.9"`; new modules start with `from __future__ import annotations`; `X | None` / `list[str]` only in annotations; never in runtime-evaluated positions.
- `numpy` is an optional extra `edges = ["numpy>=1.24"]` (add to `pyproject.toml`); only `rebuild_edges` (and its helpers) import it, lazily, raising `RuntimeError("numpy is required for the nightly edge rebuild: pip install 'ai-memory-system[edges]'")` when missing. The on-write path and the grok client never import numpy.
- Tokenizer (§7.1): lowercase alphanumeric runs (`[a-z0-9]+` over the lowercased text); drop stopwords (function words, timezones, months, mind names — the list in Task 1, verbatim); drop digit-only tokens; keep length ≥ 3 or in the short allow-list; name tokens (those of the Fact name surviving the filters) always kept and listed first; then the remaining tokens by descending frequency (ties by first occurrence); cap 24; source text = `fact_embed_text(...)` with the current `RetrievalConfig` boilerplate.
- Pair score (§7.3): `T` = Σ_{shared} idf² / (‖a‖·‖b‖) with binary TF-IDF rows; `C` = embedding cosine (0 when either lacks a vector); `zT = (T − t_mean)/t_std`, `zC = (C − c_mean)/c_std`; `blend = 0.5·zT + 0.5·zC`; floor = blend p99 over the baseline pairs; baselines from 40,000 random ordered pairs (i≠j, both embedded) drawn with `numpy.random.default_rng(seed)` exactly as the simulation; std uses population std + 1e-9.
- Picks: each Fact picks its top 5 by blend with blend ≥ floor, skipping duplicates — same name after `strip_time_suffix`, connected by SUPERSEDES, or `C ≥ 0.95` (§7.6) — and skipping itself. An edge exists while either endpoint picks it; stored once in canonical direction (`a.name < b.name`) with properties `weight` (blend), `tfidf`, `cos`, `shared_keywords` (≤ 10, sorted), `picked_by` (list of names), `via` (`"both"` or the single picker's name), `rule_version`.
- On-write (§7.4): recompute the written Fact's picks against all N; then for every Fact g with `blend(X,g) ≥ floor` whose current worst pick (5th, or −∞ with fewer than five) is beaten, add X to g's picks and drop g's weakest pick if it now has six; an edge nobody picks is deleted. Baselines and floor come from `RetrievalConfig`; a write with no `rule_version` published yet skips edge maintenance (the nightly supplies it). Unknown tokens use `idf = ln(N)` until the nightly recomputes.
- Nightly (§7.5) order in `ai-memory nightly`: `embed_all(publish=True)` (boilerplate + re-embed) → `rebuild_edges` (tokens + IDF + baselines + floor + picks → edges under `rule_version+1`) → one transaction deletes every `RELATED_TO` with `rule_version` null or ≠ current → orphaned `Word` cleanup. The first cutover retires the 5,102 legacy edges; the operational task exports them to a JSON file first.
- Phase-5 gate (§9): zero `RELATED_TO` without the current `rule_version`; isolated ≤ 3%; max degree ≤ 30; judged edge sample (edge rubric, seeded) not below the pre-cutover sample on `related` and `direct` shares.
- Tests offline (`tests/conftest.py` guard); `venv/bin/python -m pytest tests -q -p no:cacheprovider`; rebuild tests use `pytest.importorskip("numpy")`; grok suite `venv/bin/python -m pytest grok/skills/neo4j-memory/scripts/test_neo4j_memory.py -q -p no:cacheprovider`; ruff clean on new files, none new in touched files. Never `git stash`. Commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`. Never push.

## File Structure

| file | responsibility |
|---|---|
| `ai_memory/wordindex.py` (create) | pure rule: `STOP`, `SHORT`, `tokenize`, `idf_map`, `tfidf_norm`, `tfidf_cosine`, `shared_keywords`, `zscore`, `blend`, `is_duplicate`, `pick`, `canonical_pair`, `edges_from_picks`; I/O: `load_edge_config`, `publish_edge_config`, `write_fact_tokens`, `write_idf`, `write_edges`, `cutover`, `edge_stats`, `maintain_edges_for`, `rebuild_edges` (numpy) |
| `ai_memory/learn.py` (modify) | `_sync_fact_tx` writes tokens from the prepared text; `sync_facts`/`write_fact` call `maintain_edges_for`; `link_related_facts`, `_post_sync_tx`, `extract_words`' Word-index use retired; `rebuild_graph` → `rebuild_edges` |
| `ai_memory/retrieval_config.py` (modify) | `RetrievalConfig` gains optional edge fields |
| `ai_memory/embed.py` (modify) | `vector_stats` reports edge stats via `edge_stats` |
| `ai_memory/eval/edges.py` (create) | `sample_edges`, `judge_edges`, `edge_gate`, `main` |
| `scripts/cli.py` (modify) | `edges --rebuild|--dry-run`, `nightly`, `eval-edges` |
| `pyproject.toml` (modify) | `edges` extra |
| `grok/skills/neo4j-memory/scripts/neo4j_memory.py` (modify) | tokenizer copy in `_set_words`; `organize` → on-write rule |
| `tests/test_wordindex.py`, `tests/test_eval_edges.py` (create); `tests/test_learn.py`, `tests/test_embed.py`, `tests/test_cli_smoke.py`, `tests/test_retrieval_contract.py`, grok tests (modify) | |
| `CHANGELOG.md`, `MIGRATION.md`, `README.md`, `grok/README.md`, `grok/skills/neo4j-memory/SKILL.md` (modify) | |

---

### Task 1: `wordindex.py` — the pure rule

**Files:** Create `ai_memory/wordindex.py`, `tests/test_wordindex.py`.

**Interfaces (produces):**
```python
STOP: frozenset[str]   # function words + timezones + months + mind names (below)
SHORT: frozenset[str]  # {"ai","ml","sql","gpu","nlp","rl","api","cli","qa","etl"}
EDGE_K = 5; DUP_COS = 0.95; TOKEN_CAP = 24
def tokenize(text: str, name: str = "", cap: int = TOKEN_CAP) -> list[str]
def idf_map(df: Mapping[str, int], n: int) -> dict[str, float]            # ln(n/df)
def tfidf_norm(tokens: Iterable[str], idf: Mapping[str, float], default_idf: float) -> float
def tfidf_cosine(tokens_a, tokens_b, idf, norm_a: float, norm_b: float, default_idf: float) -> float
def shared_keywords(tokens_a, tokens_b, idf, cap: int = 10) -> list[str]   # shared, sorted by -idf then name
def zscore(v: float, mean: float, std: float) -> float
def blend(t: float, c: float, base: Mapping[str, float]) -> float           # 0.5*zT + 0.5*zC using base["t_mean"], ["t_std"], ["c_mean"], ["c_std"]
def is_duplicate(name_a: str, name_b: str, cos: float, supersedes: Mapping[str, str] | None = None) -> bool
def pick(cands: Iterable[tuple[str, float, float, float]], floor: float, k: int = EDGE_K) -> list[tuple[str, float, float, float]]
    # cands = (other_name, blend, tfidf, cos) already filtered of self and duplicates; top-k by blend desc, ties by name; only blend >= floor
def canonical_pair(a: str, b: str) -> tuple[str, str]
def edges_from_picks(picks: Mapping[str, list[tuple[str, float, float, float]]]) -> dict[tuple[str, str], dict]
    # {(a,b): {"weight","tfidf","cos","picked_by": sorted list, "via": "both" | picker}}
```

- [ ] **Step 1: Failing tests**

```python
# tests/test_wordindex.py
from __future__ import annotations

import math

import pytest

from ai_memory import wordindex as W


def test_tokenize_rules():
    text = "Order-Flow Imbalance (OFI) — 30] [learner 2026-08-30 the market making signal signal signal AI"
    toks = W.tokenize(text, name="Order Flow Imbalance")
    assert toks[:3] == ["order", "flow", "imbalance"]          # name tokens first
    assert "ofi" in toks and "signal" in toks and "ai" in toks   # short allow-list kept
    assert "30" not in toks and "2026" not in toks and "the" not in toks and "learner" in toks
    assert len(set(toks)) == len(toks)


def test_tokenize_cap_and_frequency_order():
    body = " ".join(f"w{i}" for i in range(40)) + " zzz zzz zzz"
    toks = W.tokenize(body, name="", cap=24)
    assert len(toks) == 24 and toks[0] == "zzz"                 # most frequent first among non-name tokens


def test_tokenize_stopwords_include_tz_months_minds():
    assert W.tokenize("EDT UTC nova weft grok jan feb Nothing", name="") == ["nothing"]


def test_idf_and_tfidf():
    idf = W.idf_map({"a": 1, "b": 2, "c": 4}, 4)
    assert idf["a"] == pytest.approx(math.log(4)) and idf["c"] == 0.0
    na = W.tfidf_norm(["a", "b"], idf, default_idf=math.log(4))
    nb = W.tfidf_norm(["b", "x"], idf, default_idf=math.log(4))      # x unknown -> default idf
    assert na == pytest.approx(math.sqrt(idf["a"] ** 2 + idf["b"] ** 2))
    cos = W.tfidf_cosine(["a", "b"], ["b", "x"], idf, na, nb, default_idf=math.log(4))
    assert cos == pytest.approx(idf["b"] ** 2 / (na * nb))
    assert W.tfidf_cosine(["a"], ["x"], idf, na, nb, default_idf=1.0) == 0.0
    assert W.tfidf_cosine([], [], idf, 0.0, 0.0, default_idf=1.0) == 0.0


def test_shared_keywords_sorted_by_idf():
    idf = {"rare": 3.0, "mid": 1.0, "common": 0.1}
    assert W.shared_keywords(["common", "rare", "mid", "x"], ["mid", "common", "rare"], idf) == ["rare", "mid", "common"]


def test_zscore_blend_and_duplicates():
    base = {"t_mean": 0.0, "t_std": 0.1, "c_mean": 0.6, "c_std": 0.1}
    assert W.blend(0.1, 0.7, base) == pytest.approx(1.0)
    assert W.is_duplicate("Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2", 0.5)
    assert W.is_duplicate("Old", "New", 0.5, {"New": "Old"})
    assert W.is_duplicate("A", "B", 0.96) and not W.is_duplicate("A", "B", 0.94)


def test_pick_top_k_above_floor_ties_by_name():
    cands = [("b", 2.0, 0.1, 0.9), ("a", 2.0, 0.1, 0.9), ("c", 1.0, 0.0, 0.5), ("d", 0.5, 0.0, 0.4)]
    assert [c[0] for c in W.pick(cands, floor=1.0, k=5)] == ["a", "b", "c"]
    assert [c[0] for c in W.pick(cands, floor=1.0, k=2)] == ["a", "b"]
    assert W.pick(cands, floor=3.0) == []


def test_edges_from_picks_merges_both_directions():
    picks = {"A": [("B", 2.0, 0.1, 0.9)], "B": [("A", 2.0, 0.1, 0.9), ("C", 1.5, 0.0, 0.8)], "C": []}
    edges = W.edges_from_picks(picks)
    assert set(edges) == {("A", "B"), ("B", "C")}
    assert edges[("A", "B")]["picked_by"] == ["A", "B"] and edges[("A", "B")]["via"] == "both"
    assert edges[("B", "C")]["picked_by"] == ["B"] and edges[("B", "C")]["via"] == "B"
    assert edges[("A", "B")]["weight"] == 2.0 and edges[("B", "C")]["cos"] == 0.8
```

- [ ] **Step 2: Run to fail** (`ModuleNotFoundError`). **Step 3: Implement**

```python
# ai_memory/wordindex.py  (pure part; I/O appended in Tasks 2–3)
"""Edge layer (spec §7): tokenizer, TF-IDF/embedding pair score, picks, on-write maintenance, nightly rebuild."""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping

from ai_memory.retrieval import strip_time_suffix, _chain

STOP = frozenset((
    "the and for with from via per how but key what when why this that are was were not you your has have "
    "utc edt est pst pdt cst cdt gmt jan feb mar apr may jun jul aug sep oct nov dec "
    "nova weft grok claude thread shared ntr").split())
SHORT = frozenset({"ai", "ml", "sql", "gpu", "nlp", "rl", "api", "cli", "qa", "etl"})
EDGE_K = 5
DUP_COS = 0.95
TOKEN_CAP = 24
_TOK = re.compile(r"[a-z0-9]+")


def _keep(w: str) -> bool:
    return w not in STOP and not w.isdigit() and (len(w) >= 3 or w in SHORT)


def tokenize(text: str, name: str = "", cap: int = TOKEN_CAP) -> list[str]:
    cnt = Counter(w for w in _TOK.findall((text or "").lower()) if _keep(w))
    name_toks = [w for w in dict.fromkeys(_TOK.findall((name or "").lower())) if w in cnt]
    rest = [w for w, _ in cnt.most_common() if w not in name_toks]
    return (name_toks + rest)[:cap]


def idf_map(df: Mapping[str, int], n: int) -> dict[str, float]:
    return {w: math.log(n / d) for w, d in df.items() if d > 0}


def _w(tok: str, idf: Mapping[str, float], default_idf: float) -> float:
    return idf.get(tok, default_idf)


def tfidf_norm(tokens: Iterable[str], idf: Mapping[str, float], default_idf: float) -> float:
    return math.sqrt(sum(_w(t, idf, default_idf) ** 2 for t in set(tokens)))


def tfidf_cosine(tokens_a, tokens_b, idf, norm_a: float, norm_b: float, default_idf: float) -> float:
    if not norm_a or not norm_b:
        return 0.0
    shared = set(tokens_a) & set(tokens_b)
    return sum(_w(t, idf, default_idf) ** 2 for t in shared) / (norm_a * norm_b)


def shared_keywords(tokens_a, tokens_b, idf, cap: int = 10) -> list[str]:
    shared = set(tokens_a) & set(tokens_b)
    return sorted(shared, key=lambda t: (-idf.get(t, 0.0), t))[:cap]


def zscore(v: float, mean: float, std: float) -> float:
    return (v - mean) / std if std else 0.0


def blend(t: float, c: float, base: Mapping[str, float]) -> float:
    return 0.5 * zscore(t, base["t_mean"], base["t_std"]) + 0.5 * zscore(c, base["c_mean"], base["c_std"])


def is_duplicate(name_a: str, name_b: str, cos: float, supersedes: Mapping[str, str] | None = None) -> bool:
    if strip_time_suffix(name_a) == strip_time_suffix(name_b):
        return True
    if cos >= DUP_COS:
        return True
    return bool(supersedes) and name_b in _chain(name_a, dict(supersedes))


def pick(cands: Iterable[tuple[str, float, float, float]], floor: float, k: int = EDGE_K) -> list[tuple[str, float, float, float]]:
    ok = [c for c in cands if c[1] >= floor]
    ok.sort(key=lambda c: (-c[1], c[0]))
    return ok[:k]


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def edges_from_picks(picks: Mapping[str, list[tuple[str, float, float, float]]]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for src, lst in picks.items():
        for other, b, t, c in lst:
            key = canonical_pair(src, other)
            e = out.setdefault(key, {"weight": b, "tfidf": t, "cos": c, "picked_by": []})
            if src not in e["picked_by"]:
                e["picked_by"].append(src)
    for e in out.values():
        e["picked_by"].sort()
        e["via"] = "both" if len(e["picked_by"]) == 2 else e["picked_by"][0]
    return out
```

(`_chain` is a private helper of `ai_memory/retrieval.py`; importing it inside the package is acceptable — note it in the module docstring. The phase-4 client copies `_chain` too, so Task 6's copy imports nothing.)

- [ ] **Step 4: Run** → 8 passed; ruff clean. **Step 5: Commit** `feat(wordindex): edge-rule pure functions — tokenizer, TF-IDF cosine, z-blend, duplicate exclusion, picks, edge merge`.

---

### Task 2: Nightly rebuild (numpy) + config/edge I/O + cutover

**Files:** Modify `ai_memory/wordindex.py` (append), `ai_memory/retrieval_config.py`, `pyproject.toml`; modify `tests/test_wordindex.py`, `tests/test_retrieval_config.py`.

**Interfaces (produces):**
```python
# retrieval_config.py
@dataclass(frozen=True) class RetrievalConfig: version, boilerplate, updated_at=None,
    rule_version: int | None = None, edge_floor: float | None = None,
    t_mean: float | None = None, t_std: float | None = None, c_mean: float | None = None, c_std: float | None = None,
    baseline_pairs: int | None = None, baseline_seed: int | None = None, n_facts: int | None = None
# load_retrieval_config reads the extra properties when present (None otherwise); publish_retrieval_config unchanged.
# wordindex.py
def load_edge_config(session) -> dict | None      # {"rule_version","edge_floor","t_mean","t_std","c_mean","c_std","n_facts"} or None when rule_version is null
def publish_edge_config(session, *, base: dict, edge_floor: float, n_facts: int, pairs: int, seed: int) -> int   # MERGE the singleton; rule_version = coalesce(c.rule_version,0)+1; returns new rule_version
def write_fact_tokens(session, name: str, tokens: list[str], norm: float) -> None   # DELETE old HAS_WORD, MERGE Word + HAS_WORD, SET f.tfidf_norm
def write_idf(session, n_facts: int) -> int      # MATCH (w:Word)<-[:HAS_WORD]-(f) WITH w, count(DISTINCT f) AS df SET w.df = df, w.idf = log(toFloat($n)/df); returns words updated
def write_edges(session, edges: Mapping[tuple[str,str], dict], rule_version: int, *, batch: int = 500) -> int
    # UNWIND $rows AS r MATCH (a:Fact {name:r.a}), (b:Fact {name:r.b}) MERGE (a)-[e:RELATED_TO]->(b) SET e.weight=r.weight, e.tfidf=r.tfidf, e.cos=r.cos, e.shared_keywords=r.shared, e.picked_by=r.picked_by, e.via=r.via, e.rule_version=$rv
    # also REMOVE e.shared_count, e.source (legacy props) on the merged edge
def cutover(session, rule_version: int) -> int   # MATCH ()-[e:RELATED_TO]->() WHERE e.rule_version IS NULL OR e.rule_version <> $rv DELETE e RETURN count
def cleanup_orphan_words(session) -> int
def edge_stats(session) -> dict   # {"edges","edges_current_rule","edges_stale_rule","rule_version","isolated","isolated_pct","max_degree","p95_degree","n_facts"}
def rebuild_edges(driver, *, seed: int = 0, pairs: int = 40000, k: int = EDGE_K, dry_run: bool = False, log=print) -> dict
```
`rebuild_edges` steps: (1) `cfg = load_retrieval_config` (boilerplate; `None` → `RuntimeError`); (2) load corpus rows `name, summary, key_points, content, embedding, status` and `SUPERSEDES` map; (3) texts = `fact_embed_text(...)` with `cfg.boilerplate`; tokens = `tokenize(text, name)`; df/idf/norms; (4) numpy: `E` normalised (rows lacking a vector zero), `C = E@E.T` with diagonal 0 and 0 where either side lacks a vector; `M[i, vocab[w]] = idf[w]`, rows normalised, `T = M@M.T` diag 0; (5) baselines over `pairs` random ordered pairs (`rng = np.random.default_rng(seed)`; `ii, jj = rng.integers(0, N, pairs)`; keep `ii != jj` and both embedded): `t_mean,t_std(+1e-9),c_mean,c_std(+1e-9)`, `B = 0.5*zT + 0.5*zC` (diag −99), `edge_floor = percentile(B[ii,jj], 99)`; (6) picks per Fact: candidates sorted by `B` desc, skipping self and `is_duplicate(name_i, name_j, C[i,j], supersedes)`, take those ≥ floor, top k; `edges = edges_from_picks(...)` with `shared_keywords` filled from tokens/idf; (7) report = `{"n_facts","vocab","rule_version_next","edge_floor","base","edges","isolated","isolated_pct","max_degree","p95_degree","dry_run"}`; if `dry_run` return here; (8) write: `write_fact_tokens` for all (batched via UNWIND: `UNWIND $rows AS r MATCH (f:Fact {name:r.name}) OPTIONAL MATCH (f)-[old:HAS_WORD]->() DELETE old WITH DISTINCT f, r SET f.tfidf_norm = r.norm WITH f, r UNWIND r.tokens AS t MERGE (w:Word {text:t}) MERGE (f)-[:HAS_WORD]->(w)`), `write_idf`, `rv = publish_edge_config(...)`, `write_edges(edges, rv)`, `deleted = cutover(rv)`, `cleanup_orphan_words`; report adds `rule_version`, `edges_written`, `edges_deleted`, `words_orphaned`.

- [ ] **Step 1: Failing tests** — (a) `RetrievalConfig` round-trips the new optional fields in `load_retrieval_config` (fake row with/without them); (b) `publish_edge_config` Cypher contains `coalesce(c.rule_version, 0) + 1` and sets `edge_floor`, `t_mean`, `t_std`, `c_mean`, `c_std`, `baseline_pairs`, `baseline_seed`, `n_facts`; returns the version from the fake; (c) `write_edges` batches rows and the statement contains `MERGE (a)-[e:RELATED_TO]->(b)`, every property name, `REMOVE e.shared_count, e.source` and `$rv`; (d) `cutover` statement `WHERE e.rule_version IS NULL OR e.rule_version <> $rv` and returns the fake count; (e) `write_idf` statement contains `log(toFloat($n) / df)`; (f) `rebuild_edges` with `pytest.importorskip("numpy")` on a 6-Fact synthetic corpus (fake session scripted by substring: corpus query → rows with 8-d unit embeddings crafted so two clusters exist; SUPERSEDES → []; config → boilerplate []) with `dry_run=True` returns a report whose `edges` connect within clusters and not across, `isolated == 0`, `rule_version_next` computed, and issues NO write statements; with `dry_run=False` the fake sees tokens → idf → publish → edges → cutover → orphan cleanup in that order; (g) `rebuild_edges` without numpy: monkeypatch `builtins.__import__` to raise for `numpy` → `RuntimeError` mentioning `ai-memory-system[edges]`; (h) `edge_stats` from scripted rows.

- [ ] **Step 2: fail; Step 3: implement (append to `wordindex.py`; `retrieval_config.py` fields; `pyproject.toml` extra); Step 4: run + ruff; Step 5: Commit** `feat(wordindex): nightly rebuild — matrices, baselines, floor, picks, batched edges, rule_version cutover; edge config on RetrievalConfig; numpy extra`.

---

### Task 3: On-write maintenance and retiring the legacy writers

**Files:** Modify `ai_memory/wordindex.py` (append `maintain_edges_for`), `ai_memory/learn.py`, `ai_memory/__init__.py`; modify `tests/test_wordindex.py`, `tests/test_learn.py`, `tests/test_library.py`.

**Interfaces (produces):**
```python
def maintain_edges_for(session, name: str, edge_cfg: dict, *, k: int = EDGE_K, log=None) -> dict
    # returns {"picked": n, "repicked": m, "deleted": d, "skipped": reason|None}
```
Algorithm: (1) read X: `MATCH (f:Fact {name:$n}) RETURN f.embedding IS NOT NULL AS has_emb, [(f)-[:HAS_WORD]->(w) | w.text] AS toks, f.tfidf_norm AS norm` — no embedding → `{"skipped": "no_embedding"}`; (2) row: `MATCH (f:Fact {name:$n}) MATCH (g:Fact) WHERE g <> f AND g.embedding IS NOT NULL RETURN g.name AS name, vector.similarity.cosine(f.embedding, g.embedding) AS cos, [(g)-[:HAS_WORD]->(w) | w.text] AS toks, g.tfidf_norm AS norm`; idf for the union of tokens: `MATCH (w:Word) WHERE w.text IN $toks RETURN w.text, w.idf` (missing → `ln(n_facts)`); supersedes map (`load_supersedes`-style query, best-effort); (3) for each g: `t = tfidf_cosine`, `b = blend(t, cos, edge_cfg)`; drop duplicates; (4) X's picks = `pick(...)`; (5) re-pick set = `{g : b ≥ floor}` minus duplicates; for those, fetch g's current picks: `MATCH (g:Fact {name:$g})-[e:RELATED_TO]-(o:Fact) WHERE $g IN e.picked_by RETURN o.name, e.weight ORDER BY e.weight ASC` — if fewer than k picks or `b > min weight` → g picks X; if that makes k+1 → the weakest is un-picked: remove g from that edge's `picked_by` (recompute `via`), delete the edge if `picked_by` is empty; (6) write X's edges and the re-pick edges via `write_edges`-shaped MERGE with `rule_version = edge_cfg["rule_version"]` and `picked_by` merged (`picked_by = apoc-free`: read existing `picked_by` first, union in Python, SET). Everything through parameters.

`learn.py`: `_prepare_embed` also returns `tokens = tokenize(text, topic["name"])` and `norm = tfidf_norm(tokens, idf_lookup, default)` — simpler: `_sync_fact_tx` writes tokens via the same UNWIND used today (replacing `extract_words(topic['name'])` with the tokens computed in `_prepare_embed` from the prepared text; when `cfg` is None, tokens from the raw text without boilerplate); `f.tfidf_norm` is set by `maintain_edges_for`'s caller: after `execute_write(_sync_fact_tx, ...)`, `sync_facts`/`write_fact` call `edge_cfg = load_edge_config(session)`; if `edge_cfg` → `maintain_edges_for(session, name, edge_cfg)` (which first sets `f.tfidf_norm` from the Word idf of X's tokens). Delete `link_related_facts`, `_post_sync_tx`; keep `cleanup_orphaned_words` only if still referenced (else delete); `rebuild_graph(*, workspace=None)` → calls `wordindex.rebuild_edges(driver)` and returns `report["edges_written"]`; `extract_words` stays only for `is_topic_saturated` (line 87) — leave it. `ai_memory/__init__.py`: export `maintain_edges_for`, `rebuild_edges`, `edge_stats`.

- [ ] **Step 1: Failing tests** — `maintain_edges_for` against a scripted fake session: (a) X with 3 neighbours above the floor → X picks all 3; neighbour n1 has 5 picks with worst 0.2 < b(X,n1)=1.4 → n1 re-picks X and its weakest edge (to `old`) is un-picked and deleted when nobody else picked it; neighbour n2 has 2 picks → re-picks X with no deletion; n3 below floor → no re-pick; assert the exact statements/params sequence (MERGE with `picked_by` union; `REMOVE`/`SET picked_by`/`DELETE` for the un-pick); (b) X without embedding → `{"skipped": "no_embedding"}` and no writes; (c) duplicates (`Shared — x — 2026-08-30` vs `#2`) excluded from X's picks; (d) `sync_facts` calls `maintain_edges_for` per topic when `load_edge_config` returns a config and skips it (text-only + tokens) when it returns `None`; `_post_sync_tx`/`link_related_facts` no longer exist (`not hasattr(learn, ...)`); (e) `_sync_fact_tx` writes the tokenizer's tokens (from the prepared text) instead of name words — the `UNWIND $words` statement receives e.g. `["order","flow","imbalance","signal",...]`.

- [ ] **Step 2: fail; Step 3: implement; Step 4: both suites + ruff (learn.py baseline count unchanged); Step 5: Commit** `feat(edges): on-write maintenance (picks + worst-pick re-pick) wired into write()/learn(); legacy shared-word edge writers retired`.

---

### Task 4: Stats + CLI (`edges`, `nightly`)

**Files:** Modify `ai_memory/embed.py` (`vector_stats` merges `edge_stats`), `scripts/cli.py`, `tests/test_embed.py`, `tests/test_cli_smoke.py`.

- `vector_stats(driver)` gains keys `edges, edges_current_rule, edges_stale_rule, rule_version, max_degree, p95_degree, isolated_pct` (via `wordindex.edge_stats(session)`; `isolated` already there).
- `ai-memory edges (--rebuild | --dry-run) [--seed N] [--pairs N] [--k N] [--json PATH]` → `rebuild_edges`; exit 0 on success, 1 on `RuntimeError` (numpy missing / no config) with the message printed, no traceback.
- `ai-memory nightly [--seed N] [--json PATH]` → `embed_all(driver, publish=True)` then `rebuild_edges(driver, seed=...)`; prints both reports; exit 1 if either fails; documents the §7.5 order in `--help`.
- Tests: monkeypatched `E.embed_all`/`W.rebuild_edges` (import at call time through the module so monkeypatching works); mutually exclusive modes; JSON output; `vector_stats` fake rows → exact dict.
- Commit `feat(cli): ai-memory edges --rebuild/--dry-run and ai-memory nightly; stats report edge health`.

---

### Task 5: Judged edge sample (`ai_memory/eval/edges.py`, `ai-memory eval-edges`)

**Interfaces (produces):**
```python
def sample_edges(session, n: int, seed: int, *, rule_version: int | None = None, legacy: bool = False) -> list[dict]
    # fetch all (a,b) pairs with the chosen filter (legacy → rule_version IS NULL; rule_version → = rv; neither → all), each with both Facts' name/summary/key_points/assistant/status; random.Random(seed).sample(pairs, min(n, len))
def judge_edges(sample: list[dict], call, *, model: str, cache: JudgeCache | None, seed: int = 0) -> dict
    # per edge: judge_query(query=judge_fact_text(a), candidates=[b_fact], call, model=model, cache=cache, seed=seed, rubric="edge") -> grade of b
    # returns {"n","judged","unjudged","related_share","direct_share","grades":[...]}; unjudged edges count as missing (not zero) and make the gate fail
def edge_gate(before: dict, after: dict) -> bool   # after judged all and related_share >= before and direct_share >= before
def main(argv) -> int   # --sample 30 --seed 7 [--legacy | --rule-version N] --judge-url --judge-model --cache --json
```
`scripts/cli.py`: `eval-edges` early-dispatch like `eval` (`argv[0] == "eval-edges"` → `edges.main(argv[1:])`). Tests with a fake judge `call` returning JSON grades; `edge_gate` truth table incl. unjudged → False. Commit `feat(eval): judged edge sample with the edge rubric and the phase-5 gate`.

---

### Task 6: grok client — tokenizer and `organize` on the new rule

**Files:** Modify `grok/skills/neo4j-memory/scripts/neo4j_memory.py`, its tests, `tests/test_retrieval_contract.py`.

- Copy verbatim from `ai_memory/wordindex.py`: `STOP`, `SHORT`, `EDGE_K`, `DUP_COS`, `TOKEN_CAP`, `_TOK`, `_keep`, `tokenize`, `_w`, `tfidf_norm`, `tfidf_cosine`, `shared_keywords`, `zscore`, `blend`, `is_duplicate` (uses the client's existing `strip_time_suffix`/`_chain` copies), `pick`, `canonical_pair`, `edges_from_picks`.
- `_set_words(session, name, text)` → `_write_tokens(session, name, prepared_text)` using `tokenize(prepared_text, name)`; the three write sites pass the canonical text (`fact_embed_text(...)` with the loaded config's boilerplate, or the raw text when no config). Old `_words`/`_WORD`/`_STOP` removed.
- `_load_edge_config(session) -> dict | None` (same properties as the library's `load_edge_config`); `_maintain_edges_for(session, name, edge_cfg)` — a verbatim port of the library's `maintain_edges_for` algorithm (same statements, same parameter names), used after each write (`cmd_write`, shared write) when the config has a `rule_version`, and by `cmd_organize`: iterate the mind's Facts (`MATCH (f:Fact {assistant:$a}) RETURN f.name`) calling it; print `maintained N facts: picked P, repicked R, deleted D`; the old 2-shared-words MERGE is removed.
- Contract test additions: `tokenize` parity on the fixture texts (`tests/fixtures/embed_text_cases.json` expected strings as inputs, with names) and on the adversarial strings; `tfidf_cosine`/`blend`/`pick`/`edges_from_picks` parity on a small shared table; `STOP`/`SHORT`/`EDGE_K`/`DUP_COS` equality.
- Grok tests: `_write_tokens` statement shape; `_maintain_edges_for` on a scripted fake (mirror of Task 3's case a); `organize` iterates and reports.
- Commit `feat(grok): tokenizer + on-write edge rule (verbatim); organize re-pointed; contract test covers the edge rule`.

---

### Task 7: Docs

CHANGELOG (Added: `ai_memory.wordindex`, `ai-memory edges`, `ai-memory nightly`, `ai-memory eval-edges`, `edges` extra; Changed: RELATED_TO semantics and properties, Word index from prepared text, legacy writers removed, grok `organize`; Fixed: none), MIGRATION (edge layer subsection: what changes for `traverse`/`related_count` users — edges now sparser and weighted; the nightly command and its order; the first cutover deletes legacy edges — export first; `rule_version`; numpy extra; grok redeploy), README (commands), grok README + SKILL.md (`organize` semantics, Word index). Commit `docs: phase 5 edge layer`.

---

### Task 8 (operational, controller-run): baseline, cutover, gate, deploy

From the worktree with `AI_MEMORY_DIR=~/.grok`.
- [ ] `venv/bin/pip install numpy` (worktree venv only). `ai-memory stats` before → record `edges 5102`, `edges_stale_rule 5102`, `isolated 381`, `max_degree`.
- [ ] Export legacy edges: `MATCH (a)-[r:RELATED_TO]->(b) RETURN a.name, b.name, properties(r)` → `~/.ai-memory/golden/legacy-related-to-2026-09-05.json` (restorable).
- [ ] Baseline judged sample: `venv/bin/python -m ai_memory.eval.edges --legacy --sample 60 --seed 7 --json ~/.ai-memory/golden/edges-before.json` (spec says 30; 60 halves the sampling noise, same seed on both sides).
- [ ] `venv/bin/python scripts/cli.py edges --dry-run --json ~/.ai-memory/golden/edges-dryrun.json` → expect isolated ≈ 2%, max degree ≈ 26, floor and baselines printed.
- [ ] `venv/bin/python scripts/cli.py nightly --json ~/.ai-memory/golden/nightly-phase5.json` (publishes RetrievalConfig v3, re-embeds, rebuilds edges under rule_version 1, deletes the legacy edges). Then `stats`: `edges_stale_rule 0`, `isolated_pct ≤ 0.03`, `max_degree ≤ 30`.
- [ ] After sample: `… eval.edges --rule-version 1 --sample 60 --seed 7 --json …/edges-after.json`; gate via `edge_gate(before, after)`.
- [ ] Deploy the grok client (diff live vs repo pre-phase-5 first), run `organize --assistant Grok` live and a scoped search smoke.
- [ ] Record everything in the ledger; update memory; push to `private`.

## Follow-on

- **Phase 6** — duplicate/supersede report (§7.6 report; owner review; no automatic merges).

## Self-review notes

- Spec coverage: §7.1 (T1 tokenizer), §7.2 (phase 2, reused via `RetrievalConfig.boilerplate`), §7.3 (T1 score + T2 baselines/floor/picks), §7.4 (T3), §7.5 (T2 cutover + T4 `nightly` order + T3 retirements + T6 grok `organize`), §7.6 first sentence (duplicate exclusion in picks), §8 edge sample (T5), §9 gate (T4 stats + T5 + T8), §4 `Word.idf` (T2 `write_idf`) and config fields (T2).
- Type consistency: `pick` candidates are `(name, blend, tfidf, cos)` everywhere (T1, T2, T3, T6); `edges_from_picks` output keys `weight,tfidf,cos,picked_by,via` are what `write_edges` reads and what `maintain_edges_for` writes; `load_edge_config` keys `rule_version, edge_floor, t_mean, t_std, c_mean, c_std, n_facts` match `blend(...)`'s `base` keys and `publish_edge_config`'s properties; `RetrievalConfig` optional fields mirror them.
- Known risk to flag: the on-write re-pick reads each candidate neighbour's current picks with one query per neighbour above the floor (typically < 10 per write); acceptable at this scale, noted for the final review.
- Known deviation: sample size 60 for the live gate (spec says 30) — same seed and rubric on both sides; the spec number is the minimum.
