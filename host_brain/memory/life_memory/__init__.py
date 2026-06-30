"""Life memory sub-package — backward-compatible re-export hub.

Decomposed from the original 8,299-line life_memory.py into focused modules.
All public symbols are re-exported here for backward compatibility.
"""

from __future__ import annotations

# Re-export everything from sub-modules
from .constants import *  # noqa: F401,F403
from .models import *  # noqa: F401,F403
from .utils import *  # noqa: F401,F403
from .semantic import *  # noqa: F401,F403
from .store import LifeMemoryStore
from .consolidator import LifeMemoryConsolidator
from .sleep import MemorySleepReplayTask

__all__ = [
    "LifeMemoryStore",
    "LifeMemoryConsolidator",
    "MemorySleepReplayTask",
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
