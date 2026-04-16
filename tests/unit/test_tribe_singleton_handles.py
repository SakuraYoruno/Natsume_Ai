from __future__ import annotations

import host_brain.tribe.glymphatic_agent as glymphatic_mod
import host_brain.tribe.interoception_agent as interoception_mod
import host_brain.tribe.limbic_agent as limbic_mod


def test_limbic_singleton_handle_is_initialized(monkeypatch) -> None:
    class _FakeLimbic:
        pass

    monkeypatch.setattr(limbic_mod, "_LIMBIC_AGENT", None)
    monkeypatch.setattr(limbic_mod, "LimbicAgent", _FakeLimbic)

    agent = limbic_mod.get_limbic_agent()

    assert isinstance(agent, _FakeLimbic)
    assert limbic_mod._LIMBIC_AGENT is agent


def test_interoception_singleton_handle_is_initialized(monkeypatch) -> None:
    class _FakeInteroception:
        pass

    monkeypatch.setattr(interoception_mod, "_INTEROCEPTION_AGENT", None)
    monkeypatch.setattr(interoception_mod, "InteroceptionAgent", _FakeInteroception)

    agent = interoception_mod.get_interoception_agent()

    assert isinstance(agent, _FakeInteroception)
    assert interoception_mod._INTEROCEPTION_AGENT is agent


def test_glymphatic_singleton_handle_is_initialized(monkeypatch) -> None:
    class _FakeGlymphatic:
        pass

    monkeypatch.setattr(glymphatic_mod, "_GLYMPHATIC_AGENT", None)
    monkeypatch.setattr(glymphatic_mod, "GlymphaticAgent", _FakeGlymphatic)

    agent = glymphatic_mod.get_glymphatic_agent()

    assert isinstance(agent, _FakeGlymphatic)
    assert glymphatic_mod._GLYMPHATIC_AGENT is agent
