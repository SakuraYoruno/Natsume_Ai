from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

from host_brain.persona.surface_main_chain import (
    compile_surface_main_chain_input,
)
from host_brain.persona.surface_runtime import build_direct_surface_messages
from host_brain.app.surface_main_chain_support import (
    _surface_expression_prompt_evidence,
)
from host_brain.surface_runtime.input_compiler import (
    _apply_active_expression_preferences,
)
from host_brain.tribe.prefrontal_runtime_policy import (
    expression_preference_directive,
)
from scripts.runtime.run_ne06_expression_live_acceptance import (
    _surface_style_anchors,
    _turn_expression_directive,
)


def test_active_expression_preferences_reach_first_surface_prompt_anchor(
    monkeypatch,
    surface_runtime_ports_factory,
) -> None:
    ports = surface_runtime_ports_factory(
        resolve_expression_preferences=lambda **_kwargs: {
            "schema": "natsume.expression-preference-policy.v1",
            "tag_scores": {
                "ai_tone": -1,
                "template_like": -1,
            },
        }
    )

    control = _apply_active_expression_preferences(
        {
            "task_mode": "daily_chat",
            "style_anchor": ["原有风格锚"],
        },
        agent_input=SimpleNamespace(person_id="default"),
        ports=ports,
    )
    compiled = compile_surface_main_chain_input(
        {
            "soul_md": "七海的人格短锚",
            "user_message": "今天有点累。",
            "task_control": control,
            "style_anchor": ["普通聊天锚"],
        }
    )

    assert compiled.request.control.style_anchor[0] == (
        "已激活的长期表达偏好（本轮执行）：避免机械 AI 腔；避免模板化分段"
    )
    assert "原有风格锚" in compiled.request.control.style_anchor
    assert expression_preference_directive(
        {"tag_scores": {"ai_tone": -1, "template_like": -1}}
    ) == compiled.request.control.style_anchor[0]
    for language in ("zh", "ja"):
        monkeypatch.setenv("HOST_COMPANION_TTS_LANGUAGE", language)
        rendered = "\n".join(
            str(message["content"])
            for message in build_direct_surface_messages(compiled.request)
        )
        assert compiled.request.control.style_anchor[0] in rendered


def test_expression_prompt_evidence_requires_the_rendered_directive() -> None:
    directive = (
        "已激活的长期表达偏好（本轮执行）：避免机械 AI 腔；避免模板化分段"
    )
    evidence = _surface_expression_prompt_evidence(
        messages=[{"role": "user", "content": f"本轮长期表达偏好：{directive}"}],
        style_anchors=[directive],
    )

    assert evidence == {
        "surface_expression_directive_digest": hashlib.sha256(
            directive.encode("utf-8")
        ).hexdigest()
    }
    assert _surface_expression_prompt_evidence(
        messages=[{"role": "user", "content": "没有偏好指令"}],
        style_anchors=[directive],
    ) == {}


def test_expression_preference_projection_failure_does_not_block_chat(
    surface_runtime_ports_factory,
) -> None:
    def unavailable(**_kwargs):
        raise RuntimeError("projection unavailable")

    control = {"task_mode": "daily_chat", "style_anchor": ["原有风格锚"]}
    result = _apply_active_expression_preferences(
        control,
        agent_input=SimpleNamespace(person_id="default"),
        ports=surface_runtime_ports_factory(
            resolve_expression_preferences=unavailable
        ),
    )

    assert result == control


def test_live_runner_reopens_existing_foreground_projection(tmp_path) -> None:
    db_path = tmp_path / "acceptance.sqlite3"
    directive = "已激活的长期表达偏好（本轮执行）：避免机械 AI 腔"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE life_command (
                command_id TEXT PRIMARY KEY,
                command_type TEXT NOT NULL,
                correlation_id TEXT,
                payload_json TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO life_command(
                command_id, command_type, correlation_id,
                payload_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "life::foreground_completed::req-1",
                "foreground_completed",
                "req-1",
                json.dumps(
                    {"reply_plan": {"style_anchor": [directive, "原有锚"]}},
                    ensure_ascii=False,
                ),
                1,
            ),
        )

    anchors = _surface_style_anchors(
        db_path,
        "req-1",
        timeout_s=0.0,
    )

    assert anchors == [directive, "原有锚"]
    assert _turn_expression_directive(
        {"surface_style_anchors": anchors}
    ) == directive
