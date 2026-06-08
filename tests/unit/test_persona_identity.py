from __future__ import annotations


def test_reserved_persona_self_env_values_resolve_to_natsume_ai(monkeypatch) -> None:
    from host_brain.persona.identity import get_canonical_persona_self_id, resolve_persona_self_id

    monkeypatch.setenv("NATSUME_PERSONA_SELF_ID", "user_1")

    assert get_canonical_persona_self_id() == "natsume_ai"
    assert resolve_persona_self_id("default") == "natsume_ai"
    assert resolve_persona_self_id("system") == "natsume_ai"
    assert resolve_persona_self_id("user_1") == "natsume_ai"


def test_custom_persona_self_env_value_is_kept(monkeypatch) -> None:
    from host_brain.persona.identity import get_canonical_persona_self_id, resolve_persona_self_id

    monkeypatch.setenv("NATSUME_PERSONA_SELF_ID", "natsume_private")

    assert get_canonical_persona_self_id() == "natsume_private"
    assert resolve_persona_self_id("") == "natsume_private"
