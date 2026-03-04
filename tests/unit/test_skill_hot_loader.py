"""T-HOTLOAD-01~05: SkillHotLoader + SkillVersionRegistry 单元测试。"""

from __future__ import annotations

import pytest

from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.skills.hot_loader import (
    SkillHotLoader,
    SkillVersionRegistry,
)
from host_brain.core.errors import (
    HOTLOAD_META_INVALID,
    HOTLOAD_SKILL_NOT_FOUND,
    HOTLOAD_VERSION_NOT_FOUND,
)


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_meta(
    skill_name: str = "test_skill",
    risk_level: int = 1,
    require_confirm: bool = False,
) -> SkillMeta:
    return SkillMeta(
        skill_name=skill_name,
        clearance="LOW",
        risk_level=risk_level,
        scope="host",
        reversible=True,
        handler=lambda args, ctx: {"ok": True},
        require_confirm=require_confirm,
    )


def _make_loader(*, with_skill: SkillMeta | None = None) -> SkillHotLoader:
    registry = SkillRegistry()
    if with_skill is not None:
        registry.register(with_skill)
    return SkillHotLoader(registry=registry)


# ── T-HOTLOAD-01: reload_skill 成功，新版本生效 ───────────────────────────────

class TestReloadSkillSuccess:
    """T-HOTLOAD-01: reload_skill 成功，新版本生效。"""

    def test_new_skill_loaded(self):
        loader = _make_loader()
        meta = _make_meta("skill_a")
        result = loader.reload_skill(meta, version="1.0.0")
        assert result.success is True
        assert result.skill_name == "skill_a"
        assert result.version == "1.0.0"

    def test_skill_available_after_reload(self):
        loader = _make_loader()
        meta = _make_meta("skill_b")
        loader.reload_skill(meta, version="1.0.0")
        loaded = loader._registry.get("skill_b")
        assert loaded is not None
        assert loaded.skill_name == "skill_b"

    def test_reload_replaces_existing_skill(self):
        old_meta = _make_meta("skill_c", risk_level=1)
        loader = _make_loader(with_skill=old_meta)

        new_meta = _make_meta("skill_c", risk_level=2)
        result = loader.reload_skill(new_meta, version="2.0.0")
        assert result.success is True

        current = loader._registry.get("skill_c")
        assert current.risk_level == 2

    def test_reload_records_version(self):
        loader = _make_loader()
        meta = _make_meta("skill_d")
        loader.reload_skill(meta, version="1.5.0")
        versions = loader.list_versions("skill_d")
        assert any(v.version == "1.5.0" for v in versions)


# ── T-HOTLOAD-02: reload_skill 失败，旧版本保持不变 ──────────────────────────

class TestReloadSkillFailure:
    """T-HOTLOAD-02: reload_skill 失败时，旧版本保持不变。"""

    def test_invalid_meta_rejected(self):
        loader = _make_loader()
        # risk_level=4 但 require_confirm=False → validate() 失败
        invalid_meta = _make_meta("skill_e", risk_level=4, require_confirm=False)
        result = loader.reload_skill(invalid_meta, version="1.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_META_INVALID

    def test_old_skill_unchanged_after_failure(self):
        old_meta = _make_meta("skill_f", risk_level=1)
        loader = _make_loader(with_skill=old_meta)

        # 尝试加载无效版本
        invalid_meta = _make_meta("skill_f", risk_level=4, require_confirm=False)
        loader.reload_skill(invalid_meta, version="2.0.0")

        # 旧版本应保持不变
        current = loader._registry.get("skill_f")
        assert current.risk_level == 1

    def test_failure_written_to_audit(self):
        loader = _make_loader()
        invalid_meta = _make_meta("skill_g", risk_level=4, require_confirm=False)
        loader.reload_skill(invalid_meta, version="1.0.0")
        audit = loader.get_audit_log()
        assert len(audit) == 1
        assert audit[0]["success"] is False
        assert audit[0]["reason_code"] == HOTLOAD_META_INVALID


# ── T-HOTLOAD-03: rollback_skill 回滚到指定版本 ──────────────────────────────

class TestRollbackSkill:
    """T-HOTLOAD-03: rollback_skill 回滚到指定版本。"""

    def test_rollback_to_previous_version(self):
        old_meta = _make_meta("skill_h", risk_level=1)
        loader = _make_loader(with_skill=old_meta)

        # 先加载 v1.0.0（记录旧版本）
        loader.reload_skill(old_meta, version="1.0.0")
        # 再加载 v2.0.0
        new_meta = _make_meta("skill_h", risk_level=2)
        loader.reload_skill(new_meta, version="2.0.0")

        # 回滚到 v1.0.0
        result = loader.rollback_skill("skill_h", "1.0.0")
        assert result.success is True
        assert result.version == "1.0.0"

        current = loader._registry.get("skill_h")
        assert current.risk_level == 1

    def test_rollback_nonexistent_skill(self):
        loader = _make_loader()
        result = loader.rollback_skill("nonexistent_skill", "1.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_SKILL_NOT_FOUND

    def test_rollback_nonexistent_version(self):
        meta = _make_meta("skill_i")
        loader = _make_loader(with_skill=meta)
        result = loader.rollback_skill("skill_i", "99.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_VERSION_NOT_FOUND


# ── T-HOTLOAD-04: 热加载写 skill_reload_log（审计） ──────────────────────────

class TestHotLoadAudit:
    """T-HOTLOAD-04: 热加载写 skill_reload_log（审计）。"""

    def test_success_written_to_audit(self):
        loader = _make_loader()
        meta = _make_meta("skill_j")
        loader.reload_skill(meta, version="1.0.0")
        audit = loader.get_audit_log()
        assert len(audit) == 1
        entry = audit[0]
        assert entry["success"] is True
        assert entry["skill_name"] == "skill_j"
        assert entry["version"] == "1.0.0"
        assert "reload_id" in entry
        assert "ts_ms" in entry

    def test_rollback_written_to_audit(self):
        old_meta = _make_meta("skill_k", risk_level=1)
        loader = _make_loader(with_skill=old_meta)
        loader.reload_skill(old_meta, version="1.0.0")
        new_meta = _make_meta("skill_k", risk_level=2)
        loader.reload_skill(new_meta, version="2.0.0")
        loader.rollback_skill("skill_k", "1.0.0")

        audit = loader.get_audit_log()
        # reload v1.0.0 + reload v2.0.0 + rollback = 3 条
        assert len(audit) == 3
        assert audit[-1]["success"] is True

    def test_multiple_reloads_all_audited(self):
        loader = _make_loader()
        for i in range(3):
            meta = _make_meta(f"skill_l_{i}")
            loader.reload_skill(meta, version=f"1.{i}.0")
        assert len(loader.get_audit_log()) == 3


# ── T-HOTLOAD-05: 运行时一致性检查：SkillMeta 字段缺失 → 拒绝加载 ─────────────

class TestHotLoadConsistencyCheck:
    """T-HOTLOAD-05: SkillMeta 字段缺失 → 拒绝加载。"""

    def test_empty_skill_name_rejected(self):
        loader = _make_loader()
        # 直接构造无效 SkillMeta（绕过 dataclass 验证）
        import dataclasses
        invalid_meta = dataclasses.replace(_make_meta("skill_m"), skill_name="")
        result = loader.reload_skill(invalid_meta, version="1.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_META_INVALID

    def test_invalid_clearance_rejected(self):
        loader = _make_loader()
        import dataclasses
        invalid_meta = dataclasses.replace(_make_meta("skill_n"), clearance="INVALID")
        result = loader.reload_skill(invalid_meta, version="1.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_META_INVALID

    def test_high_risk_without_confirm_rejected(self):
        loader = _make_loader()
        # risk_level=4 但 require_confirm=False
        invalid_meta = _make_meta("skill_o", risk_level=4, require_confirm=False)
        result = loader.reload_skill(invalid_meta, version="1.0.0")
        assert result.success is False
        assert result.reason_code == HOTLOAD_META_INVALID

    def test_valid_high_risk_with_confirm_accepted(self):
        loader = _make_loader()
        valid_meta = _make_meta("skill_p", risk_level=4, require_confirm=True)
        result = loader.reload_skill(valid_meta, version="1.0.0")
        assert result.success is True
