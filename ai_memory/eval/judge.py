"""LLM relevance judge for retrieval evaluation.

Contract (see docs/superpowers/specs, "judge definition"):

* Input per call: one query and up to ten candidate Facts. Each candidate is
  shown as name, summary, at most three key points, assistant tag and status.
  Scores, fusion tags and embeddings are never shown, and candidate order is
  shuffled deterministically so position cannot leak the source ranker.
* Output: a JSON array only, one object per candidate:
  ``{"name": str, "grade": 0|1|2, "why": str}``. ``why`` is capped at 120 chars.
* A malformed reply is retried once, then the query is reported unjudged
  (``None``) rather than guessed.
* Judgments are cached by hash of (model, query, fact name, fact text).
* Before judge scores count, ``calibration_agreement`` against a hand-labelled
  golden set must reach ``CALIBRATION_THRESHOLD`` on grade-2 versus not-2.

The HTTP call is injected (``call(messages) -> str``) so the module has no
network dependency and is testable offline.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

CALIBRATION_THRESHOLD = 0.8
MAX_KEY_POINTS = 3
MAX_WHY = 120
JUDGE_BATCH = 10

JUDGE_SYSTEM_PROMPT = """\
You are a relevance judge for a personal knowledge graph used by AI assistants.
You receive one QUERY and a numbered list of candidate FACTS. Grade how well
each fact answers the query for the person who asked it.

Grades:
- grade 2: the fact directly answers the query, or is the one a person would want to read first.
- grade 1: related and useful as context, but not the answer.
- grade 0: unrelated; or any fact whose status is superseded or removed, regardless of what else is in the list (the retriever is expected to surface the active successor).

Rules:
- Judge each fact on its own text. Do not reward length, recency wording, or the assistant tag.
- Grade 2 requires the fact to answer the specific thing asked: the named mechanism, setting, decision or value. Same subject area with a different mechanism is grade 1 at most.
- A fact that only shares keywords with the query but answers a different question is grade 0 or 1, not 2.
- If two facts say the same thing, grade both the same.
- Output ONLY a JSON array, no prose, no markdown fences. One object per candidate, every candidate included:
  [{"name": "<exact candidate name>", "grade": 0|1|2, "why": "<under 120 chars>"}]

Example. QUERY: "how do we stop the maker bot from buying below the reserve balance"
[{"name": "reserve_balance_usd guard", "grade": 2, "why": "names the config key and the check"},
 {"name": "Kraken fee tiers", "grade": 0, "why": "fees, not reserve logic"},
 {"name": "USDManager order sizing", "grade": 1, "why": "adjacent sizing logic, not the reserve guard"}]
"""


# ── text shown to the judge ──────────────────────────────────────────────────

def judge_fact_text(fact: dict) -> str:
    """The exact text the judge sees for one fact. Stable across rankers:
    only name, summary, first MAX_KEY_POINTS key points, assistant and status."""
    kps = [str(p) for p in (fact.get("key_points") or []) if p][:MAX_KEY_POINTS]
    lines = [f"name: {fact.get('name', '')}"]
    summary = (fact.get("summary") or "").strip()
    if summary:
        lines.append(f"summary: {summary}")
    for kp in kps:
        lines.append(f"- {kp}")
    tag = fact.get("assistant") or "untagged"
    status = fact.get("status") or "active"
    lines.append(f"assistant: {tag}  status: {status}")
    return "\n".join(lines)


def build_judge_messages(query: str, candidates: List[dict], seed: int = 0) -> List[dict]:
    """System + user messages. Candidate order is shuffled by ``seed``."""
    order = list(candidates)
    random.Random(seed).shuffle(order)
    blocks = [f"[{i}]\n{judge_fact_text(c)}" for i, c in enumerate(order, 1)]
    user = f"QUERY: {query.strip()}\n\nCANDIDATES:\n\n" + "\n\n".join(blocks) + "\n\nJSON array:"
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ── parsing ──────────────────────────────────────────────────────────────────

_ARRAY = re.compile(r"\[.*\]", re.S)


def parse_judgments(text: str, expected_names: Iterable[str]) -> Optional[Dict[str, dict]]:
    """Parse the judge reply. Returns {name: {"grade", "why"}} for known names,
    or None if the reply is not a well-formed grade array or omits any expected
    candidate (an omission must never silently become grade 0)."""
    expected = set(expected_names)
    m = _ARRAY.search(text or "")
    if not m:
        return None
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list):
        return None
    out: Dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            return None
        grade = it.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (0, 1, 2):
            return None
        name = it.get("name")
        if name not in expected:
            continue
        out[name] = {"grade": grade, "why": str(it.get("why", ""))[:MAX_WHY]}
    if set(out) != expected:
        return None
    return out


# ── calibration ──────────────────────────────────────────────────────────────

def calibration_agreement(judge_grades: Dict[str, int], human_grades: Dict[str, int]) -> float:
    """Fraction of shared keys where judge and human agree on grade-2 vs not-2."""
    shared = set(judge_grades) & set(human_grades)
    if not shared:
        return 0.0
    hits = sum(1 for k in shared if (judge_grades[k] == 2) == (human_grades[k] == 2))
    return hits / len(shared)


# ── metrics ──────────────────────────────────────────────────────────────────

def _gain(g: int) -> float:
    return float(2 ** g - 1)


def ndcg_at_k(ranked: List[str], grades: Dict[str, int], k: int) -> float:
    """``grades`` is every judged grade for the query (the whole pool), so the
    ideal ordering includes relevant facts this ranker failed to retrieve."""
    top = ranked[:k]
    dcg = sum(_gain(grades.get(n, 0)) / math.log2(i + 2) for i, n in enumerate(top))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum(_gain(g) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(ranked: List[str], grades: Dict[str, int], k: int) -> float:
    relevant = {n for n, g in grades.items() if g == 2}
    if not relevant:
        return 0.0
    return len(relevant & set(ranked[:k])) / len(relevant)


def mrr(ranked: List[str], grades: Dict[str, int]) -> float:
    for i, n in enumerate(ranked, 1):
        if grades.get(n, 0) == 2:
            return 1.0 / i
    return 0.0


def passes_ship_gate(before: Dict[str, Dict[str, float]], after: Dict[str, Dict[str, float]]) -> bool:
    """Recall-first ship rule. ``before``/``after`` are
    {"golden": {"ndcg5", "recall5"}, "judged": {"ndcg5", "recall5"}}.
    Golden (human truth) may not drop on either metric; judged must hold or rise."""
    for split in ("golden", "judged"):
        for metric in ("ndcg5", "recall5"):
            if after[split][metric] < before[split][metric]:
                return False
    return True


# ── cache ────────────────────────────────────────────────────────────────────

class JudgeCache:
    """JSON-file cache keyed by sha256(model | query | fact name | fact text)."""

    def __init__(self, path):
        self.path = Path(path)
        self._d: Dict[str, dict] = {}
        if self.path.exists():
            try:
                self._d = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._d = {}

    @staticmethod
    def key(model: str, query: str, name: str, text: str) -> str:
        # The system prompt is part of the key: a re-calibrated prompt must
        # never be served grades produced by the old one.
        prompt_id = hashlib.sha256(JUDGE_SYSTEM_PROMPT.encode()).hexdigest()[:16]
        return hashlib.sha256("\x1f".join((prompt_id, model, query, name, text)).encode()).hexdigest()

    def get(self, model, query, name, text) -> Optional[dict]:
        return self._d.get(self.key(model, query, name, text))

    def put(self, model, query, name, text, judgment: dict) -> None:
        self._d[self.key(model, query, name, text)] = judgment

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._d, indent=0, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


# ── driver ───────────────────────────────────────────────────────────────────

def judge_query(
    query: str,
    candidates: List[dict],
    call: Callable[[List[dict]], str],
    *,
    model: str,
    cache: Optional[JudgeCache] = None,
    seed: int = 0,
) -> Optional[Dict[str, dict]]:
    """Grade ``candidates`` for ``query``. Cached candidates are not re-sent.
    Returns {name: {"grade", "why"}} or None if the model failed twice."""
    out: Dict[str, dict] = {}
    todo: List[dict] = []
    for c in candidates:
        text = judge_fact_text(c)
        hit = cache.get(model, query, c["name"], text) if cache else None
        if hit is not None:
            out[c["name"]] = hit
        else:
            todo.append(c)
    if not todo:
        return out

    for start in range(0, len(todo), JUDGE_BATCH):
        batch = todo[start:start + JUDGE_BATCH]
        names = {c["name"] for c in batch}
        parsed = None
        for attempt in range(2):
            reply = call(build_judge_messages(query, batch, seed=seed + attempt))
            parsed = parse_judgments(reply, names)
            if parsed is not None:
                break
        if parsed is None:
            return None
        for c in batch:
            j = parsed.get(c["name"])
            if j is None:
                continue
            out[c["name"]] = j
            if cache:
                cache.put(model, query, c["name"], judge_fact_text(c), j)
    if cache:
        cache.save()
    return out
