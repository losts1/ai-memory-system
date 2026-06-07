"""Provenance metadata for AI Memory System Fact nodes.

Tracks origin (source type), trust level, and optional prompt-guard scan
results for every memory entry. Attaches to Fact nodes as provenance_* Neo4j
properties and to memory file frontmatter as a nested provenance: block.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

TrustLevel = Literal["trusted", "untrusted", "suspicious", "high_risk", "unknown"]
SourceType = Literal[
    "user", "web_fetch", "bash", "read", "api", "memory",
    "system", "learner", "legacy", "manual"
]


@dataclass
class Provenance:
    """Origin and trust metadata for a memory Fact."""

    source: SourceType
    trust: TrustLevel = "unknown"
    risk_score: Optional[int] = None
    risk_band: Optional[Literal["none", "low", "medium", "high"]] = None
    signals: List[str] = field(default_factory=list)
    original_source: Optional[str] = None
    written_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    session_id: Optional[str] = None
    assistant: Optional[str] = None
    scan_version: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict, omitting None values and empty lists."""
        return {
            k: v for k, v in asdict(self).items()
            if v is not None and v != []
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        """Deserialize from dict, ignoring unknown keys for forward compatibility."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
