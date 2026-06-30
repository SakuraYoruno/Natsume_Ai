"""A1 自主项目自生成与退休（emergent project genesis / retirement）单测。

验证：经历里反复出现且未被覆盖的主题被提案成涌现项目；上限/门槛/覆盖跳过生效；
完成或长期闲置的涌现项目自动退休；默认核心项目永不退休；退休很久的项目被剪枝。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import host_brain.runtime.autonomous_self as aself
from host_brain.runtime import life_continuity as lc

NOW = 1_800_000_000_000
DAY_MS = 24 * 3600 * 1000
# 用默认项目未覆盖的新主题（"天文"），避免与默认的「轻小说推荐」项目撞主题
THEME_LINES = ("阿晓说想学天文摄影", "陪阿晓查了天文台开放时间", "天文社团招新阿晓很心动")


def _ledger() -> SimpleNamespace:
    ledger = SimpleNamespace(_conn=sqlite3.connect(":memory:", check_same_thread=False))
    lc.ensure_life_schema(ledger)
    return ledger


def _seed(ledger, lines, *, source_kind="dialogue") -> None:
    for line in lines:
        lc.record_life_experience(
            ledger=ledger, person_id="default", source_kind=source_kind,
            event_kind="dialogue_turn", content_summary=line, salience=0.5,
        )


# ── 自生成 ──

def test_emergent_genesis_from_recurring_theme() -> None:
    ledger = _ledger()
    _seed(ledger, THEME_LINES)
    projects = aself._propose_emergent_projects([], ledger=ledger, person_id="default", now_ms=NOW)
    emergent = [p for p in projects if p.get("origin") == "emergent"]
    assert len(emergent) == 1
    proj = emergent[0]
    assert proj["status"] == "active"
    assert proj["theme"] == "天文"
    assert proj["theme"] in "".join(THEME_LINES)
    assert proj["confidence"] >= 0.3
    assert proj["project_id"].startswith("emergent_")


def test_emergent_needs_min_events() -> None:
    ledger = _ledger()
    _seed(ledger, ("阿晓聊起轻小说",))
    projects = aself._propose_emergent_projects([], ledger=ledger, person_id="default", now_ms=NOW)
    assert [p for p in projects if p.get("origin") == "emergent"] == []


def test_emergent_skips_self_logged_events() -> None:
    # autonomous_self / life_runtime 等自产事件不该成为主题来源
    ledger = _ledger()
    _seed(ledger, ("身体存在感 body presence tick", "身体存在感 body presence tick", "身体存在感 body presence tick"),
          source_kind="autonomous_self")
    projects = aself._propose_emergent_projects([], ledger=ledger, person_id="default", now_ms=NOW)
    assert [p for p in projects if p.get("origin") == "emergent"] == []


def test_emergent_respects_max_active_cap() -> None:
    ledger = _ledger()
    _seed(ledger, THEME_LINES)
    existing = [
        {"project_id": f"emergent_t{i}", "origin": "emergent", "status": "active", "theme": f"t{i}"}
        for i in range(aself._EMERGENT_MAX_ACTIVE)
    ]
    projects = aself._propose_emergent_projects(list(existing), ledger=ledger, person_id="default", now_ms=NOW)
    assert aself._emergent_active_count(projects) == aself._EMERGENT_MAX_ACTIVE


def test_emergent_skips_covered_theme() -> None:
    ledger = _ledger()
    _seed(ledger, THEME_LINES)
    existing = [{"project_id": "watch_astronomy", "title": "陪阿晓看天文", "motive": "", "next_step": "", "theme": ""}]
    projects = aself._propose_emergent_projects(list(existing), ledger=ledger, person_id="default", now_ms=NOW)
    assert [p for p in projects if p.get("origin") == "emergent"] == []


def test_emergent_genesis_one_per_cycle() -> None:
    ledger = _ledger()
    _seed(ledger, THEME_LINES)
    projects = aself._propose_emergent_projects([], ledger=ledger, person_id="default", now_ms=NOW)
    assert sum(1 for p in projects if p.get("origin") == "emergent") == 1


# ── 退休与剪枝 ──

def test_retire_completed_emergent() -> None:
    projects = [{"project_id": "emergent_x", "origin": "emergent", "status": "active", "progress": 1.0, "theme": "x", "created_at_ms": NOW}]
    out = aself._retire_projects(projects, now_ms=NOW)
    assert out[0]["status"] == "retired"
    assert out[0]["retired_reason"] == "completed"


def test_retire_idle_emergent() -> None:
    old = NOW - 20 * DAY_MS
    projects = [{"project_id": "emergent_x", "origin": "emergent", "status": "active", "progress": 0.2, "theme": "x", "created_at_ms": old, "last_worked_at_ms": old}]
    out = aself._retire_projects(projects, now_ms=NOW)
    assert out[0]["status"] == "retired"
    assert out[0]["retired_reason"] == "idle"


def test_active_emergent_not_retired_when_fresh() -> None:
    projects = [{"project_id": "emergent_x", "origin": "emergent", "status": "active", "progress": 0.3, "theme": "x", "created_at_ms": NOW, "last_worked_at_ms": NOW}]
    out = aself._retire_projects(projects, now_ms=NOW)
    assert out[0]["status"] == "active"


def test_default_project_never_retired() -> None:
    # 真实默认项目没有 origin 字段，progress 满也不退休
    projects = [{"project_id": "improve_lightnovel_recommendation", "status": "active", "progress": 1.0}]
    out = aself._retire_projects(projects, now_ms=NOW)
    assert out[0]["status"] == "active"


def test_prune_old_retired_emergent() -> None:
    old = NOW - 40 * DAY_MS
    projects = [{"project_id": "emergent_x", "origin": "emergent", "status": "retired", "retired_at_ms": old, "theme": "x"}]
    out = aself._retire_projects(projects, now_ms=NOW)
    assert out == []


# ── 端到端：经 apply_autonomous_self_cycle 生效 ──

def test_cycle_surfaces_emergent_project() -> None:
    ledger = _ledger()
    _seed(ledger, THEME_LINES)
    state = lc.get_life_state(ledger, "default")
    after = aself.apply_autonomous_self_cycle(ledger=ledger, person_id="default", state=state, now_ms=NOW)
    ids = {str(p.get("project_id")) for p in after.get("self_projects") or []}
    assert any(pid.startswith("emergent_") for pid in ids)
    actives = {str(p.get("project_id")) for p in after["self_growth"]["active_projects"]}
    # 默认核心项目仍在
    assert "improve_lightnovel_recommendation" in ids
