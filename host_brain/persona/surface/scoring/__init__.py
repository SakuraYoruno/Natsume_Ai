"""Scoring facilators for isolated surface candidate generation.

Re-export the individual scoring components so that downstream imports
(including candidate_scoring.py) can continue to reference a single
stable path.
"""
from host_brain.persona.surface.scoring import (
    parse_utils as parse,
    stage_positions,
    control_affect,
    texture,
    constants as stage_constants,
)

__all__ = [
    "parse",
    "stage_positions",
    "control_affect",
    "texture",
    "stage_constants",
]
