"""A1 收尾：涌现项目 next_step 随推进重生成（不再冻结在创建时）。"""

from __future__ import annotations

import host_brain.runtime.autonomous_self as aself

NOW = 1_800_000_000_000


def test_emergent_next_step_refreshes_with_progress() -> None:
    proj = {
        "project_id": "emergent_天文", "origin": "emergent", "status": "active",
        "theme": "天文", "progress": 0.3, "last_worked_at_ms": 0,
        "next_step": "回看最近关于「天文」的经历，找出能为阿晓做的一件具体小事。",
    }
    out = aself._update_projects([proj], attention="emergent_天文", signals=[], now_ms=NOW)
    assert "天文" in out[0]["next_step"]
    assert "念头" in out[0]["next_step"]  # 已重生成，反映攒了多少
    assert out[0]["last_worked_at_ms"] == NOW


def test_default_project_next_step_not_touched() -> None:
    proj = {"project_id": "improve_lightnovel_recommendation", "status": "active",
            "progress": 0.2, "next_step": "原始下一步"}
    out = aself._update_projects([proj], attention="improve_lightnovel_recommendation", signals=[], now_ms=NOW)
    # 默认项目（非 emergent）不重写 next_step
    assert out[0]["next_step"] == "原始下一步"
