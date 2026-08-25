"""Life memory constants — truth states, episode types, evidence types, etc."""

from __future__ import annotations

from datetime import timedelta, timezone
import re

TRUTH_CANDIDATE = "CANDIDATE"
TRUTH_VERIFIED = "VERIFIED"
TRUTH_CORRECTED = "CORRECTED"
TRUTH_SUPPRESSED = "SUPPRESSED"
TRUTH_NARRATIVE_ONLY = "NARRATIVE_ONLY"

STATUS_ACTIVE = "ACTIVE"
STATUS_SUPPRESSED = "SUPPRESSED"

OPEN_LOOP_OPEN = "OPEN"
OPEN_LOOP_CLOSED = "CLOSED"
OPEN_LOOP_CANCELLED = "CANCELLED"
OPEN_LOOP_SUPERSEDED = "SUPERSEDED"
OPEN_LOOP_TERMINAL_STATES: frozenset[str] = frozenset({OPEN_LOOP_CLOSED, OPEN_LOOP_CANCELLED, OPEN_LOOP_SUPERSEDED})

EPISODE_CHAT = "chat_episode"
EPISODE_PERCEPTION = "perception_episode"
EPISODE_ACTION = "action_episode"
EPISODE_BACKGROUND = "background_episode"
EPISODE_INNER = "inner_episode"
EPISODE_REFLECTION = "reflection_episode"
EPISODE_SEMANTIC = "semantic_episode"

EVIDENCE_USER_TEXT = "user_text"
EVIDENCE_ASSISTANT_REPLY = "assistant_reply"
EVIDENCE_GATEWAY_MESSAGE = "gateway_message"
EVIDENCE_VISION_OBSERVATION = "vision_observation"
EVIDENCE_AUDIO_ASR = "audio_asr"
EVIDENCE_SCHEDULER_EVENT = "scheduler_event"
EVIDENCE_INNER_THOUGHT = "inner_thought"
EVIDENCE_TOOL_RESULT = "tool_result"
EVIDENCE_REFLECTION = "reflection"
EVIDENCE_REINFORCEMENT = "reinforcement"
EVIDENCE_ATTACHMENT = "attachment"

RETENTION_HOT = "HOT"
RETENTION_WARM = "WARM"
RETENTION_COLD = "COLD"
RETENTION_GONE = "GONE"

MEMORY_CLASS_AUDIT_ANCHOR = "audit_anchor"
MEMORY_CLASS_CHAT_EXPERIENCE = "chat_experience"
MEMORY_CLASS_COLLECTION_EVENT = "collection_event"
MEMORY_CLASS_COMMITMENT = "commitment"
MEMORY_CLASS_CORRECTION = "correction"
MEMORY_CLASS_FAILURE_LESSON = "failure_lesson"
MEMORY_CLASS_INNER_LIFE = "inner_life"
MEMORY_CLASS_PERCEPTION_EVIDENCE = "perception_evidence"
MEMORY_CLASS_PREFERENCE = "preference"
MEMORY_CLASS_RELATIONSHIP_FEEDBACK = "relationship_feedback"
MEMORY_CLASS_REFLECTION = "reflection"
MEMORY_CLASS_SELF_CHANGE = "self_change"
MEMORY_CLASS_STABLE_FACT = "stable_fact"
MEMORY_CLASS_TOOL_ACTION = "tool_action"
MEMORY_CLASS_TRANSIENT_NOISE = "transient_noise"

RECALL_GRAIN_AUDIT = "audit_only"
RECALL_GRAIN_EPISODE = "episode"
RECALL_GRAIN_COLLECTION = "collection"
RECALL_GRAIN_SEMANTIC = "semantic"

DECAY_STABLE = "STABLE"
DECAY_SLOW = "SLOW"
DECAY_NORMAL = "NORMAL"
DECAY_FAST = "FAST"
DECAY_EPHEMERAL = "EPHEMERAL"

COLLECTION_RECOMMENDATION = "recommendation_collection"
COLLECTION_SCOPE_DAILY = "daily"
COLLECTION_SCOPE_RECENT = "recent"
COLLECTION_SCOPE_DELIVERY = "delivery"
COLLECTION_SCOPE_ROLLING_PREFIX = "rolling"
COLLECTION_SCOPE_ROLLING_3D = "rolling:3d"
COLLECTION_SCOPE_ROLLING_7D = "rolling:7d"
COLLECTION_SCOPE_ROLLING_30D = "rolling:30d"
COLLECTION_ROLLING_WINDOWS: tuple[tuple[str, int, str], ...] = (
    (COLLECTION_SCOPE_ROLLING_3D, 3, "近 3 天"),
    (COLLECTION_SCOPE_ROLLING_7D, 7, "近 7 天"),
    (COLLECTION_SCOPE_ROLLING_30D, 30, "近 30 天"),
)
COLLECTION_SCOPE_RECENT_WINDOW_DAYS = 7
MS_DAY = 24 * 60 * 60 * 1000

_SYNTHETIC_METADATA_FLAGS: tuple[str, ...] = ("e2e_probe", "synthetic_probe", "test_probe")
_SYNTHETIC_METADATA_MARKERS: tuple[str, ...] = ("real_scene_batch", "scenario_batch", "entrypoint_probe")
_SYNTHETIC_ID_MARKERS: tuple[str, ...] = (
    "surface-memory-recall-e2e",
    "life-memory-real-scene-e2e",
    "life-memory-real-scene-",
    "unit-tool-matrix",
    "pytest",
    "person-test",
    "session-test",
    "-test",
    "unit-",
    "test-",
    "test_",
    "synthetic",
)
_SYNTHETIC_SOURCE_MARKERS: tuple[str, ...] = (
    "real-scene-",
    "surface-memory-recall-e2e",
    "life-memory-real-scene",
    "unit-tool-matrix",
    "pytest",
    "synthetic",
)

_SEMANTIC_PRIORITY_MEMORY_CLASSES: tuple[str, ...] = (
    MEMORY_CLASS_PREFERENCE,
    MEMORY_CLASS_COMMITMENT,
    MEMORY_CLASS_CORRECTION,
    MEMORY_CLASS_RELATIONSHIP_FEEDBACK,
    MEMORY_CLASS_SELF_CHANGE,
    MEMORY_CLASS_STABLE_FACT,
    MEMORY_CLASS_FAILURE_LESSON,
)

ENTITY_KIND_BOOK = "book"
ENTITY_KIND_ITEM = "item"

_LOCAL_MEMORY_TZ = timezone(timedelta(hours=8))

STRONG_EVIDENCE_PREFIXES: tuple[str, ...] = (
    "gateway://message/",
    "qq://message/",
    "wechat://message/",
    "chat://request/",
    "chat://turn/",
    "surface://reply/",
    "skill://exec/",
    "mcp://exec/",
    "action://event/",
    "channel://message/",
    "channel://event/",
    "vision://observation/",
    "perception://event/",
    "scheduler://event/",
    "inner_world://event/",
    "inner_activity://event/",
    "voice://event/",
    "sandbox://exec/",
    "avatar://action/",
    "clawd://action/",
)

_RECALL_FETCH_LIMIT_FACTOR = 8
_RECALL_KEYWORD_LIMIT = 18
_ANAPHORIC_SELECTION_RE = re.compile(
    r"(?:哪(?:本|部|个|一(?:本|部|个))|哪个|哪部|哪本|"
    r"(?:我问你|刚问你|问你).{0,12}哪|"
    r"(?:喜欢|想看|先看|推荐|选|挑).{0,8}哪)",
)
_ANAPHORIC_EVENT_KINDS = frozenset({EPISODE_ACTION, EPISODE_BACKGROUND, EPISODE_PERCEPTION})
_ANAPHORIC_LOW_VALUE_ACTIONS = frozenset(
    {
        "qq_reply_sent",
        "wechat_reply_sent",
        "gateway_reply_sent",
        "channel_message_sent",
        "surface_reply",
        "avatar_action",
        "clawd_action",
    }
)
