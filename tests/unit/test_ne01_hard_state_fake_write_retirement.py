from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_tribe_agents_do_not_treat_host_hard_state_as_mutable_sink() -> None:
    for relative in (
        "host_brain/tribe/limbic_agent.py",
        "host_brain/tribe/prefrontal_agent.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert 'require("hard_state")' not in source
        assert "hard_state.set(" not in source
        assert "hard_state.emotion_tag =" not in source
        assert "hard_state.emotion_intensity =" not in source
        assert "hard_state.mood_state =" not in source
