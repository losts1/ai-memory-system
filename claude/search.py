#!/usr/bin/env python3
"""
Semantic + keyword search over Claude Code memory files.

Uses nomic-embed-text (via local Ollama) for semantic embeddings, with a
content-hash disk cache so unchanged files are never re-embedded. Falls back
to keyword-only scoring if Ollama is unavailable.

Usage:
    python3 search.py "websocket reconnect"
    python3 search.py "websocket reconnect" --top 3
    python3 search.py --reindex          # wipe embedding cache (force re-embed)
    python3 search.py --list             # list all memory files

Intended to be called from inside a Claude Code session via the Bash tool:
    python3 ~/.claude/projects/<project-slug>/memory/search.py "query"
"""

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

MEMORY_DIR = Path(__file__).parent
CACHE_DIR  = MEMORY_DIR / ".embed_cache"

OLLAMA_URL   = "http://localhost:11434/api/embed"
EMBED_MODEL  = "nomic-embed-text"

# Files that live in the memory dir but are not memories.
NON_MEMORY = {
    "MEMORY.md", "README.md", "IMPLEMENT-MEMORY-SYSTEM.md",
    "search.py", "distill.py", "queue_session.sh",
}

# Score weights when both signals are available.
SEMANTIC_WEIGHT = 0.70
KEYWORD_WEIGHT  = 0.30

# Score weights when Ollama is down (keyword-only mode).
KEYWORD_ONLY_WEIGHT = 1.00

# Minimum combined score to include a result.
MIN_SCORE = 0.05


# ── File enumeration ──────────────────────────────────────────────────────────

def get_memory_files() -> list[Path]:
    """Return all .md files in the memory dir that are actual memories."""
    return sorted(
        f for f in MEMORY_DIR.glob("*.md")
        if f.name not in NON_MEMORY
    )


# ── Embedding cache ───────────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    """16-char SHA-256 prefix — unique enough for a small file collection."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _cache_path(content_hash: str) -> Path:
    return CACHE_DIR / f"{content_hash}.json"


def _load_cached(content_hash: str) -> list[float] | None:
    p = _cache_path(content_hash)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cached(content_hash: str, embedding: list[float]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(content_hash).write_text(json.dumps(embedding))


# ── Ollama embedding ──────────────────────────────────────────────────────────

def _embed_via_ollama(text: str) -> list[float] | None:
    """
    Call Ollama /api/embed. Returns 768-dim float list, or None on failure.
    Uses /api/embed (newer endpoint) where response["embeddings"][0] is the vector.
    """
    try:
        import requests
        r = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL, "input": text},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]
    except Exception:
        return None


def get_embedding(content: str) -> list[float] | None:
    """Return embedding for content, using cache when available."""
    h = _content_hash(content)
    cached = _load_cached(h)
    if cached is not None:
        return cached
    emb = _embed_via_ollama(content)
    if emb is not None:
        _save_cached(h, emb)
    return emb


# ── Scoring ───────────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (pure Python, no numpy required)."""
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def keyword_score(query: str, content: str) -> float:
    """
    Fraction of query tokens (len > 2) that appear in content.
    Returns 0.0 if no meaningful tokens.
    """
    tokens = [t.lower() for t in re.split(r"\W+", query) if len(t) > 2]
    if not tokens:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for t in tokens if t in content_lower)
    return hits / len(tokens)


# ── Excerpt extraction ────────────────────────────────────────────────────────

def extract_excerpt(content: str, max_chars: int = 220) -> str:
    """
    Strip YAML frontmatter and return the first meaningful body text,
    truncated to max_chars.
    """
    lines = content.strip().splitlines()

    # Strip frontmatter block (--- ... ---)
    if lines and lines[0].strip() == "---":
        try:
            end = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
            lines = lines[end + 1:]
        except StopIteration:
            pass

    body = "\n".join(l for l in lines if l.strip()).strip()
    if len(body) > max_chars:
        return body[:max_chars].rstrip() + "…"
    return body


# ── Main search ───────────────────────────────────────────────────────────────

def search(query: str, top_n: int = 5) -> list[tuple[float, Path, str]]:
    """
    Return up to top_n (score, path, content) tuples, sorted descending by score.

    Strategy:
      1. Embed query with nomic-embed-text.
      2. For each memory file: cosine(query, doc) × 0.70 + keyword(query, doc) × 0.30
      3. If Ollama is unavailable, fall back to keyword-only scoring.
      4. Filter results below MIN_SCORE.
    """
    files = get_memory_files()
    if not files:
        return []

    query_emb = get_embedding(query)   # None if Ollama down
    ollama_ok = query_emb is not None

    results = []
    for path in files:
        content = path.read_text(encoding="utf-8")

        kw = keyword_score(query, content)

        if ollama_ok:
            doc_emb = get_embedding(content)
            sem = cosine_similarity(query_emb, doc_emb) if doc_emb else 0.0
            score = SEMANTIC_WEIGHT * sem + KEYWORD_WEIGHT * kw
        else:
            score = KEYWORD_ONLY_WEIGHT * kw

        if score >= MIN_SCORE:
            results.append((score, path, content))

    results.sort(key=lambda x: -x[0])
    return results[:top_n]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _reindex() -> None:
    """Wipe the embedding cache, forcing re-embedding on next search."""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        print(f"Embedding cache cleared: {CACHE_DIR}")
    else:
        print("No cache to clear.")


def _clean_cache() -> None:
    """
    Remove orphan embedding files whose content hash no longer matches any
    current memory file. These accumulate when memory files are updated
    (the old hash's cache file is never removed).
    """
    if not CACHE_DIR.exists():
        print("No cache directory.")
        return

    live_hashes = {
        _content_hash(f.read_text(encoding="utf-8"))
        for f in get_memory_files()
    }

    removed = 0
    for cache_file in CACHE_DIR.glob("*.json"):
        if cache_file.stem not in live_hashes:
            cache_file.unlink()
            removed += 1

    total = sum(1 for _ in CACHE_DIR.glob("*.json"))
    print(f"Cache cleaned: removed {removed} orphan file(s), {total} remain.")


def _list_memories() -> None:
    files = get_memory_files()
    if not files:
        print("No memory files found.")
        return
    print(f"{len(files)} memory file(s):")
    for f in files:
        print(f"  {f.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Claude Code memory files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", nargs="?", help="Search query string")
    parser.add_argument("--top",     type=int, default=5, help="Max results (default: 5)")
    parser.add_argument("--reindex",     action="store_true", help="Clear embedding cache (force re-embed all)")
    parser.add_argument("--clean-cache", action="store_true", help="Remove orphan embedding files for updated memories")
    parser.add_argument("--list",        action="store_true", help="List all memory files")
    args = parser.parse_args()

    if args.reindex:
        _reindex()
        return

    if args.clean_cache:
        _clean_cache()
        return

    if args.list:
        _list_memories()
        return

    if not args.query:
        parser.print_help()
        sys.exit(1)

    # Probe Ollama once — the result is cached so the search() call reuses it.
    ollama_ok = get_embedding(args.query) is not None
    mode = "semantic+keyword" if ollama_ok else "keyword-only"

    results = search(args.query, args.top)

    if not results:
        print(f"No results for: {args.query!r}  [{mode} (Ollama {'up' if ollama_ok else 'unavailable'})]")
        return

    import datetime
    print(f"Results for: {args.query!r}  [{mode}]\n")
    for score, path, content in results:
        mdate = datetime.date.fromtimestamp(path.stat().st_mtime)
        print(f"  [{score:.2f}] {path.name}  ({mdate})")
        ex = extract_excerpt(content)
        for line in ex.splitlines()[:3]:
            print(f"    {line}")
        print()


if __name__ == "__main__":
    main()
