"""Life-memory value contracts and constants.

Runtime services live in their precise modules: ``store``, ``consolidator``
and ``sleep``.  The package root deliberately does not act as a service facade.
"""

from __future__ import annotations

# Re-export everything from sub-modules
from .constants import *  # noqa: F401,F403
from .models import *  # noqa: F401,F403
from .utils import *  # noqa: F401,F403
from .contracts import (
    ChatMemoryCandidate,
    MemoryAdmissionDecision,
    MemoryAdmissionKind,
    MemoryWriteReceipt,
)
from .semantic import *  # noqa: F401,F403
__all__ = [
    "ChatMemoryCandidate",
    "MemoryAdmissionDecision",
    "MemoryAdmissionKind",
    "MemoryWriteReceipt",
    "EpisodeRecallPacket",
    "derive_episode_id",
    "derive_evidence_id",
    "strong_evidence_refs",
    "TRUTH_CANDIDATE",
    "TRUTH_VERIFIED",
    "TRUTH_CORRECTED",
    "TRUTH_SUPPRESSED",
    "TRUTH_NARRATIVE_ONLY",
    "EPISODE_CHAT",
    "EPISODE_PERCEPTION",
    "EPISODE_ACTION",
    "EPISODE_BACKGROUND",
    "EPISODE_INNER",
    "RETENTION_HOT",
    "RETENTION_WARM",
    "RETENTION_COLD",
    "RETENTION_GONE",
    "COLLECTION_RECOMMENDATION",
    "COLLECTION_SCOPE_DAILY",
    "COLLECTION_SCOPE_RECENT",
    "COLLECTION_SCOPE_DELIVERY",
]
