from __future__ import annotations

from dataclasses import dataclass

import pytest

from host_brain.app.tts_route import (
    TTS_ROUTE_DEPENDENCY_UNAVAILABLE,
    resolve_tts_route,
)


@dataclass(frozen=True, slots=True)
class _Route:
    speed_scale: float
    pitch_shift: float
    emotion_tag: str
    style_note: str = ""
    lang_hint: str = "zh"

    def as_dict(self) -> dict[str, object]:
        return {
            "speed_scale": self.speed_scale,
            "pitch_shift": self.pitch_shift,
            "emotion_tag": self.emotion_tag,
            "style_note": self.style_note,
            "lang_hint": self.lang_hint,
        }


class _Router:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def route(self, **kwargs: object) -> _Route:
        self.calls.append(dict(kwargs))
        return _Route(
            speed_scale=1.1,
            pitch_shift=0.4,
            emotion_tag=str(kwargs["emotion_tag"]),
            style_note="steady",
        )


class _MetaLearning:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, dict(kwargs)))


def test_resolve_tts_route_uses_explicit_host_collaborators() -> None:
    router = _Router()
    meta = _MetaLearning()

    route = resolve_tts_route(
        text="你今天很难过啊。是发生什么事了吗？",
        emotion_context={
            "emotion_tag": "SAD",
            "vad_vector": {"valence": "-0.7", "arousal": "0.4"},
        },
        bond_summary={"stage": "FRIEND"},
        tts_context_router=router,
        meta_learning_loop=meta,
    )

    assert router.calls == [
        {
            "text": "你今天很难过啊。是发生什么事了吗？",
            "emotion_tag": "sad",
            "bond_stage": "FRIEND",
            "valence": -0.7,
            "arousal": 0.4,
        }
    ]
    assert route["emotion_tag"] == "sad"
    assert route["speed"] == 1.1
    assert route["pitch"] == 0.2
    assert meta.calls[0][0] == ("tts_context_router",)


def test_resolve_tts_route_rejects_missing_scope_dependency() -> None:
    with pytest.raises(RuntimeError, match=TTS_ROUTE_DEPENDENCY_UNAVAILABLE):
        resolve_tts_route(
            text="测试",
            emotion_context={"emotion_tag": "CALM"},
            bond_summary={"stage": "FRIEND"},
            tts_context_router=None,
            meta_learning_loop=_MetaLearning(),
        )


def test_resolve_tts_route_normalizes_calm_with_explicit_scope() -> None:
    route = resolve_tts_route(
        text="没关系的，我会陪着你。",
        emotion_context={"emotion_tag": "CALM"},
        bond_summary={"stage": "FRIEND"},
        tts_context_router=_Router(),
        meta_learning_loop=_MetaLearning(),
    )

    assert route["emotion_tag"] == "calm"
