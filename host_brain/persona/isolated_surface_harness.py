"""Isolated surface generation — backward-compatible re-export hub.

This file was the original 5230-line god-module.  The code has been
decomposed into ``host_brain.persona.surface/`` sub-modules.  All
public symbols are re-exported here so existing importers continue
to work unchanged.
"""
from __future__ import annotations

from host_brain.persona.surface.candidate_scoring import *  # noqa: F401, F403
from host_brain.persona.surface.continuity_detectors import *  # noqa: F401, F403
from host_brain.persona.surface.data_models import *  # noqa: F401, F403
from host_brain.persona.surface.fact_handling import *  # noqa: F401, F403
from host_brain.persona.surface.input_validation import *  # noqa: F401, F403
from host_brain.persona.surface.prompt_construction import *  # noqa: F401, F403
from host_brain.persona.surface.relationship_affect import *  # noqa: F401, F403
from host_brain.persona.surface.scene_constants import *  # noqa: F401, F403
from host_brain.persona.surface.util import *  # noqa: F401, F403

__all__ = [
    "IsolatedCandidateScore",
    "IsolatedSurfaceInput",
    "IsolatedSurfaceRunResult",
    "ReplyControlPacket",
    "RelationshipControlAxes",
    "RelationshipStyleProfile",
    "build_isolated_surface_input",
    "build_isolated_surface_messages",
    "build_native_stream_surface_messages",
    "build_minimal_repair_messages",
    "build_surface_original_quote_query",
    "find_forbidden_input_paths",
    "memory_alignment_facts",
    "memory_alignment_terms",
    "memory_candidate_guidance",
    "memory_carryover_facts",
    "merged_facts",
    "merged_must_not_do",
    "normalize_surface_chat_punctuation",
    "parse_surface_candidates",
    "relationship_control_axes_for_distance",
    "run_isolated_surface_generation",
    "score_isolated_surface_candidates",
    "select_best_isolated_candidate",
    "surface_prompt_facts",
]
