from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.chat_retrieval_helpers import (
    _apply_active_knowledge_to_surface_packet,
)
from host_brain.persona.surface_main_chain import (
    compile_surface_main_chain_input,
)
from host_brain.persona.surface_memory_recall import (
    SurfaceMemoryFactRow,
    SurfaceMemoryRecallPacket,
)
from host_brain.persona.surface_runtime import build_direct_surface_messages
from host_brain.surface_runtime.input_compiler import (
    _active_identity_release_directive,
    _apply_active_identity_release,
)


def _identity_projection(*, warmth: float = 0.82) -> dict:
    return {
        "snapshot": {
            "snapshot_ref": "identity-snapshot://active",
            "style_fingerprint": {
                "dimensions": [
                    ["warmth", warmth],
                    ["curiosity", 0.8],
                    ["humor", 0.76],
                    ["sensitivity", 0.8],
                    ["assertiveness", 0.55],
                    ["playfulness", 0.45],
                    ["resilience", 0.78],
                ]
            },
        }
    }


def test_active_knowledge_enters_only_an_admitted_recall_packet() -> None:
    calls: list[dict] = []

    def search(**kwargs):
        calls.append(dict(kwargs))
        return [
            {
                "doc_id": "knowledge://runtime/alpha",
                "content": "项目代号是 PROJECT_NE06_ALPHA。",
                "score": 0.9,
                "metadata": {
                    "entry_digest": "a" * 64,
                    "provenance_ref": "project-source://alpha",
                },
            }
        ]

    packet = SurfaceMemoryRecallPacket(
        facts=("原有记忆",),
        route_ids=("surface_semantic",),
        trace={"route_count": 1},
    )
    skipped = _apply_active_knowledge_to_surface_packet(
        packet=packet,
        plan=SimpleNamespace(should_recall=False),
        search=search,
        person_id="default",
        fact_row_type=SurfaceMemoryFactRow,
    )
    assert skipped is packet
    assert calls == []

    selected = _apply_active_knowledge_to_surface_packet(
        packet=packet,
        plan=SimpleNamespace(
            should_recall=True,
            query="PROJECT_NE06_ALPHA",
            max_facts=4,
        ),
        search=search,
        person_id="default",
        fact_row_type=SurfaceMemoryFactRow,
    )

    assert calls == [
        {
            "query": "PROJECT_NE06_ALPHA",
            "person_id": "default",
            "top_k": 4,
            "threshold": 0.05,
        }
    ]
    assert selected.facts[0] == "已激活知识：项目代号是 PROJECT_NE06_ALPHA。"
    assert selected.rows[0].store_id == "semantic_memory"
    assert selected.route_ids[0] == "knowledge_os_v2"
    assert selected.evidence_refs[0] == (
        "evolution-knowledge-entry://sha256/" + "a" * 64
    )
    assert selected.trace["knowledge_os_active"] == {
        "status": "SELECTED",
        "selected_count": 1,
        "read_only": True,
    }


def test_active_identity_release_reaches_compact_surface_prompt(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    active = _identity_projection()
    ports = surface_runtime_ports_factory(
        resolve_active_identity=lambda: active,
    )
    control = _apply_active_identity_release(
        {"task_mode": "daily_chat", "style_anchor": ["原有风格锚"]},
        ports=ports,
    )
    directive = _active_identity_release_directive(active)
    assert directive.startswith("已激活身份版本的表达底色（不覆盖 Soul）：")
    assert control["style_anchor"][0] == directive

    compiled = compile_surface_main_chain_input(
        {
            "soul_md": "七海的人格短锚",
            "user_message": "今天想轻松聊聊。",
            "task_control": control,
        }
    )
    for language in ("zh", "ja"):
        monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", language)
        rendered = "\n".join(
            str(message["content"])
            for message in build_direct_surface_messages(compiled.request)
        )
        assert directive in rendered


def test_identity_projection_failure_keeps_chat_available(
    surface_runtime_ports_factory,
) -> None:
    def unavailable():
        raise RuntimeError("projection unavailable")

    control = {"task_mode": "daily_chat", "style_anchor": ["原有风格锚"]}
    assert _apply_active_identity_release(
        control,
        ports=surface_runtime_ports_factory(
            resolve_active_identity=unavailable,
        ),
    ) == control
