"""Facade module for isolated surface scoring.

All scoring logic has been moved into the scoring/ subpackage.
This module re-exports everything to maintain backward compatibility.
"""
from __future__ import annotations

# Re-export all public APIs from the scoring subpackage
from host_brain.persona.surface.scoring import (
    parse_surface_candidates,
    score_isolated_surface_candidates,
    # Stage scoring
    _apply_back_stage_scoring,
    _apply_morning_stage_scoring,
    _apply_low_energy_stage_scoring,
    _apply_self_doubt_scene_scoring,
    _apply_positive_stage_scene_scoring,
    # Texture scoring
    _apply_signature_texture_scoring,
    _apply_surface_scene_quality_sample_scoring,
    # Control-affect scoring
    _apply_control_affect_scoring,
    # Parsing / violations
    _control_violations,
    _question_budget_count,
    merged_must_not_do,
    # Constants re-exported for backwards compat
    _JEALOUSY_LOW_PRESSURE_MARKERS,
    _RELATION_AXIS_PHYSICAL_MARKERS,
    _RELATION_AXIS_ROMANCE_MARKERS,
    # Positive-stage helpers
    _positive_stage_scene_hits,
    _morning_stage_texture_hits,
    _low_energy_stage_hits,
    _back_landing_hits,
    _back_stage_texture_hits,
    _thin_morning_greeting,
)

__all__ = [
    "parse_surface_candidates",
    "score_isolated_surface_candidates",
    "_apply_back_stage_scoring",
    "_apply_morning_stage_scoring",
    "_apply_low_energy_stage_scoring",
    "_apply_self_doubt_scene_scoring",
    "_apply_positive_stage_scene_scoring",
    "_apply_signature_texture_scoring",
    "_apply_surface_scene_quality_sample_scoring",
    "_apply_control_affect_scoring",
    "_control_violations",
    "_question_budget_count",
    "merged_must_not_do",
    "_JEALOUSY_LOW_PRESSURE_MARKERS",
    "_RELATION_AXIS_PHYSICAL_MARKERS",
    "_RELATION_AXIS_ROMANCE_MARKERS",
    "_positive_stage_scene_hits",
    "_morning_stage_texture_hits",
    "_low_energy_stage_hits",
    "_back_landing_hits",
    "_back_stage_texture_hits",
    "_thin_morning_greeting",
]
