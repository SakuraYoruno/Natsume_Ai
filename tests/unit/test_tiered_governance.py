"""M42 分层治理架构测试（T-TG-TOKEN / T-TG-TIER / T-TG-CACHE / T-TG-ENGINE / T-TG-AUDIT）。"""

from __future__ import annotations

import time
import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.route_token import RouteToken, compile_route_decision
from host_brain.governor.tier_policy import ApprovalTier, AuditTier, GovernorTierPolicy
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry


# ===========================================================================
# 辅助工厂
# ===========================================================================

def _make_meta(
    skill_name: str = "test_skill",
    risk_level: int = 1,
    reversible: bool = True,
    clearance: str = "LOW",
    require_confirm: bool = False,
) -> SkillMeta:
    if risk_level >= 4 and not require_confirm:
        require_confirm = True
    return SkillMeta(
        skill_name=skill_name,
        clearance=clearance,
        risk_level=risk_level,
        scope="host",
        reversible=reversible,
        handler=lambda args, ctx: {"status": "OK", "manifest_ref": "mref::test"},
        require_confirm=require_confirm,
    )


def _make_intent(
    skill_name: str = "test_skill",
    session_id: str = "session::test",
    seq: int = 1,
    intent_id: str | None = None,
    nonce: str | None = None,
    ts_ms_offset: int = -1000,
) -> dict:
    now = now_ms()
    iid = intent_id or f"intent::{skill_name}::{seq}"
    return {
        "schema_version": "Natsume.intent.v1",
        "id": iid,
        "ts_ms": now + ts_ms_offset,
        "session_id": session_id,
        "seq": seq,
        "type": "skill_call",
        "action": skill_name,
        "target": "host",
        "args": {"text": "test"},
        "nonce": nonce or f"nonce::{iid}",
    }


# ===========================================================================
# T-TG-TOKEN-01~04：RouteToken 新增分层令牌
# ===========================================================================

class TestTieredGovernanceTokenM42(unittest.TestCase):
    """T-TG-TOKEN-01~04：验证 RouteToken 新增令牌和 compile_route_decision tier 参数。"""

    def test_route_token_has_cached(self) -> None:
        """T-TG-TOKEN-01：RouteToken 枚举包含 GOV_OK_CACHED 和 GOV_OK_RULE。"""
        self.assertIn(RouteToken.GOV_OK_CACHED, RouteToken)
        self.assertIn(RouteToken.GOV_OK_RULE, RouteToken)
        self.assertEqual("GOV_OK_CACHED", RouteToken.GOV_OK_CACHED.value)
        self.assertEqual("GOV_OK_RULE", RouteToken.GOV_OK_RULE.value)

    def test_compile_cache_tier(self) -> None:
        """T-TG-TOKEN-02：compile_route_decision(approved=True, tier="cache") 返回 GOV_OK_CACHED。"""
        result = compile_route_decision(
            approved=True,
            reason_code="APPROVE",
            committed_at_ms=now_ms(),
            tier="cache",
        )
        self.assertEqual(RouteToken.GOV_OK_CACHED, result.token)

    def test_compile_rule_tier(self) -> None:
        """T-TG-TOKEN-03：compile_route_decision(approved=True, tier="rule") 返回 GOV_OK_RULE。"""
        result = compile_route_decision(
            approved=True,
            reason_code="APPROVE",
            committed_at_ms=now_ms(),
            tier="rule",
        )
        self.assertEqual(RouteToken.GOV_OK_RULE, result.token)

    def test_compile_full_tier_backward_compat(self) -> None:
        """T-TG-TOKEN-04：compile_route_decision(approved=True, tier="full") 返回 GOV_OK_RENDER（向后兼容）。"""
        result = compile_route_decision(
            approved=True,
            reason_code="APPROVE",
            committed_at_ms=now_ms(),
            tier="full",
        )
        self.assertEqual(RouteToken.GOV_OK_RENDER, result.token)

    def test_compile_no_tier_backward_compat(self) -> None:
        """未指定 tier 时默认返回 GOV_OK_RENDER（向后兼容）。"""
        result = compile_route_decision(
            approved=True,
            reason_code="APPROVE",
            committed_at_ms=now_ms(),
        )
        self.assertEqual(RouteToken.GOV_OK_RENDER, result.token)

    def test_compile_airlock_tier(self) -> None:
        """compile_route_decision(approved=True, tier="airlock") 返回 GOV_AIRLOCK_REVIEW。"""
        result = compile_route_decision(
            approved=True,
            reason_code="AIRLOCK_REQUIRED",
            committed_at_ms=now_ms(),
            tier="airlock",
        )
        self.assertEqual(RouteToken.GOV_AIRLOCK_REVIEW, result.token)


# ===========================================================================
# T-TG-TIER-01~08：GovernorTierPolicy 分层策略
# ===========================================================================

class TestGovernorTierPolicyM42(unittest.TestCase):
    """T-TG-TIER-01~08：验证 GovernorTierPolicy 分层映射规则。"""

    def setUp(self) -> None:
        self.policy = GovernorTierPolicy()

    def test_risk_level_1_is_cache(self) -> None:
        """T-TG-TIER-01：risk_level=1 → ApprovalTier.CACHE。"""
        meta = _make_meta(risk_level=1)
        self.assertEqual(ApprovalTier.CACHE, self.policy.get_approval_tier(meta))

    def test_risk_level_2_is_rule(self) -> None:
        """T-TG-TIER-02：risk_level=2 → ApprovalTier.RULE。"""
        meta = _make_meta(risk_level=2)
        self.assertEqual(ApprovalTier.RULE, self.policy.get_approval_tier(meta))

    def test_risk_level_3_is_full(self) -> None:
        """T-TG-TIER-03：risk_level=3 → ApprovalTier.FULL。"""
        meta = _make_meta(risk_level=3)
        self.assertEqual(ApprovalTier.FULL, self.policy.get_approval_tier(meta))

    def test_risk_level_4_is_full(self) -> None:
        """T-TG-TIER-04：risk_level=4 → ApprovalTier.FULL。"""
        meta = _make_meta(risk_level=4, require_confirm=True)
        self.assertEqual(ApprovalTier.FULL, self.policy.get_approval_tier(meta))

    def test_risk_level_5_is_airlock(self) -> None:
        """T-TG-TIER-05：risk_level=5 → ApprovalTier.AIRLOCK。"""
        meta = _make_meta(risk_level=5, require_confirm=True)
        self.assertEqual(ApprovalTier.AIRLOCK, self.policy.get_approval_tier(meta))

    def test_irreversible_risk3_upgrades_to_airlock(self) -> None:
        """T-TG-TIER-06：risk_level=3, reversible=False → ApprovalTier.AIRLOCK（不可逆升级）。"""
        meta = _make_meta(risk_level=3, reversible=False)
        self.assertEqual(ApprovalTier.AIRLOCK, self.policy.get_approval_tier(meta))

    def test_cache_tier_audit_is_light(self) -> None:
        """T-TG-TIER-07：CACHE 通道 → AuditTier.LIGHT。"""
        meta = _make_meta(risk_level=1)
        self.assertEqual(AuditTier.LIGHT, self.policy.get_audit_tier(meta))

    def test_airlock_tier_audit_is_full(self) -> None:
        """T-TG-TIER-08：AIRLOCK 通道 → AuditTier.FULL_AUDIT。"""
        meta = _make_meta(risk_level=5, require_confirm=True)
        self.assertEqual(AuditTier.FULL_AUDIT, self.policy.get_audit_tier(meta))

    def test_rule_tier_audit_is_standard(self) -> None:
        """RULE 通道 → AuditTier.STANDARD。"""
        meta = _make_meta(risk_level=2)
        self.assertEqual(AuditTier.STANDARD, self.policy.get_audit_tier(meta))

    def test_full_tier_audit_is_standard(self) -> None:
        """FULL 通道 → AuditTier.STANDARD。"""
        meta = _make_meta(risk_level=3)
        self.assertEqual(AuditTier.STANDARD, self.policy.get_audit_tier(meta))


# ===========================================================================
# T-TG-CACHE-01~06：GovernorPolicyCache TTL 缓存
# ===========================================================================

class TestGovernorPolicyCacheM42(unittest.TestCase):
    """T-TG-CACHE-01~06：验证 GovernorPolicyCache TTL 缓存行为。"""

    def setUp(self) -> None:
        self.cache = GovernorPolicyCache(ttl_ms=500)  # 500ms TTL 便于测试

    def test_put_then_get_within_ttl(self) -> None:
        """T-TG-CACHE-01：put 后 get 在 TTL 内返回缓存值。"""
        self.cache.put("skill_a", 80, True)
        result = self.cache.get("skill_a", 80)
        self.assertTrue(result)

    def test_get_after_ttl_returns_none(self) -> None:
        """T-TG-CACHE-02：get 在 TTL 过期后返回 None。"""
        cache = GovernorPolicyCache(ttl_ms=50)  # 50ms TTL
        cache.put("skill_b", 80, True)
        time.sleep(0.1)  # 等待 100ms，超过 TTL
        result = cache.get("skill_b", 80)
        self.assertIsNone(result)

    def test_invalidate_clears_skill(self) -> None:
        """T-TG-CACHE-03：invalidate(skill_name) 后 get 返回 None。"""
        self.cache.put("skill_c", 80, True)
        self.cache.put("skill_c", 50, False)
        self.cache.invalidate("skill_c")
        self.assertIsNone(self.cache.get("skill_c", 80))
        self.assertIsNone(self.cache.get("skill_c", 50))

    def test_clear_removes_all(self) -> None:
        """T-TG-CACHE-04：clear() 后所有缓存清空。"""
        self.cache.put("skill_d", 80, True)
        self.cache.put("skill_e", 60, False)
        self.cache.clear()
        self.assertIsNone(self.cache.get("skill_d", 80))
        self.assertIsNone(self.cache.get("skill_e", 60))
        self.assertEqual(0, self.cache.size())

    def test_different_trust_levels_independent(self) -> None:
        """T-TG-CACHE-05：不同 trust_level 的缓存互不干扰。"""
        self.cache.put("skill_f", 80, True)
        self.cache.put("skill_f", 30, False)
        self.assertTrue(self.cache.get("skill_f", 80))
        self.assertFalse(self.cache.get("skill_f", 30))

    def test_put_false_cached(self) -> None:
        """T-TG-CACHE-06：put(approved=False) 后 get 返回 False（拒绝结果也可缓存）。"""
        self.cache.put("skill_g", 80, False)
        result = self.cache.get("skill_g", 80)
        self.assertFalse(result)
        self.assertIsNotNone(result)  # 不是 None，是 False

    def test_miss_returns_none(self) -> None:
        """未命中时返回 None。"""
        result = self.cache.get("nonexistent_skill", 80)
        self.assertIsNone(result)


# ===========================================================================
# T-TG-ENGINE-01~08：IntentDecisionEngine 分层路由
# ===========================================================================

class TestTieredGovernanceEngineM42(unittest.TestCase):
    """T-TG-ENGINE-01~08：验证 IntentDecisionEngine 分层路由行为。"""

    def setUp(self) -> None:
        self.now_ms = now_ms()
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=60_000, confirm_ttl_ms=60_000, undo_ttl_ms=60_000)
        self.registry = SkillRegistry()
        self.policy_cache = GovernorPolicyCache(ttl_ms=60_000)

        # 注册各风险等级的测试 Skill
        for risk, name in [(1, "low_risk_skill"), (2, "medium_risk_skill"), (3, "high_risk_skill")]:
            self.registry.register(_make_meta(skill_name=name, risk_level=risk))
        self.registry.register(_make_meta(skill_name="critical_skill", risk_level=5, require_confirm=True))

        self.engine_with_registry = IntentDecisionEngine(
            ledger=self.ledger,
            token_service=self.tokens,
            registry=self.registry,
            policy_cache=self.policy_cache,
        )
        self.engine_no_registry = IntentDecisionEngine(
            ledger=SQLiteLedger(db_path=":memory:"),
            token_service=self.tokens,
        )
        self.snapshot = {"trust_level": 80, "energy_level": 60}

    def _evaluate(self, engine: IntentDecisionEngine, skill_name: str, seq: int = 1) -> dict:
        intent = _make_intent(skill_name=skill_name, seq=seq, session_id=f"session::{skill_name}::{seq}")
        return engine.evaluate_intent(
            intent,
            current_ms=self.now_ms,
            governance_snapshot=self.snapshot,
        )

    def test_cache_hit_returns_gov_ok_cached(self) -> None:
        """T-TG-ENGINE-01：risk_level=1 且缓存命中 → GOV_OK_CACHED。"""
        # 第一次：缓存未命中，写入缓存，返回 GOV_OK_RULE
        result1 = self._evaluate(self.engine_with_registry, "low_risk_skill", seq=1)
        self.assertEqual("APPROVE", result1["decision"])
        token1 = result1["committed_route_token"]["token"]
        self.assertIn(token1, (RouteToken.GOV_OK_RULE.value, RouteToken.GOV_OK_CACHED.value))

        # 第二次：缓存命中，返回 GOV_OK_CACHED
        result2 = self._evaluate(self.engine_with_registry, "low_risk_skill", seq=2)
        self.assertEqual("APPROVE", result2["decision"])
        self.assertEqual(RouteToken.GOV_OK_CACHED.value, result2["committed_route_token"]["token"])

    def test_cache_miss_returns_gov_ok_rule(self) -> None:
        """T-TG-ENGINE-02：risk_level=1 且缓存未命中 → GOV_OK_RULE，结果写入缓存。"""
        # 清空缓存确保未命中
        self.policy_cache.clear()
        result = self._evaluate(self.engine_with_registry, "low_risk_skill", seq=10)
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(RouteToken.GOV_OK_RULE.value, result["committed_route_token"]["token"])
        # 验证缓存已写入
        trust_level = self.snapshot.get("trust_level", 50)
        cached = self.policy_cache.get("low_risk_skill", trust_level)
        self.assertTrue(cached)

    def test_risk_level_2_returns_gov_ok_rule(self) -> None:
        """T-TG-ENGINE-03：risk_level=2 → GOV_OK_RULE。"""
        result = self._evaluate(self.engine_with_registry, "medium_risk_skill", seq=1)
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(RouteToken.GOV_OK_RULE.value, result["committed_route_token"]["token"])

    def test_risk_level_3_returns_gov_ok_render(self) -> None:
        """T-TG-ENGINE-04：risk_level=3 → GOV_OK_RENDER。"""
        result = self._evaluate(self.engine_with_registry, "high_risk_skill", seq=1)
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(RouteToken.GOV_OK_RENDER.value, result["committed_route_token"]["token"])

    def test_risk_level_5_returns_airlock(self) -> None:
        """T-TG-ENGINE-05：risk_level=5 → GOV_AIRLOCK_REVIEW。"""
        result = self._evaluate(self.engine_with_registry, "critical_skill", seq=1)
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(RouteToken.GOV_AIRLOCK_REVIEW.value, result["committed_route_token"]["token"])
        intent = _make_intent(
            skill_name="critical_skill",
            seq=1,
            session_id="session::critical_skill::1",
        )
        validation = self.tokens.validate_bound_token(
            result["tokens"]["approval_token_ref"],
            expected_type="approval",
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        self.assertFalse(validation.valid, "AIRLOCK must never expose an executable approval token")

    def test_no_registry_backward_compat(self) -> None:
        """T-TG-ENGINE-06：未注入 registry → 保持现有行为（GOV_OK_RENDER），向后兼容。"""
        # 使用合法的静态 action（ACTION_ALLOWLIST 中的 noop）
        intent = _make_intent(skill_name="noop", seq=1, session_id="session::no_reg::1")
        intent["args"] = {}  # noop 不需要 args
        result = self.engine_no_registry.evaluate_intent(
            intent,
            current_ms=self.now_ms,
            governance_snapshot=self.snapshot,
        )
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(RouteToken.GOV_OK_RENDER.value, result["committed_route_token"]["token"])

    def test_committed_route_token_has_approval_tier(self) -> None:
        """T-TG-ENGINE-07：committed_route_token 包含 approval_tier 字段。"""
        result = self._evaluate(self.engine_with_registry, "high_risk_skill", seq=2)
        self.assertIn("approval_tier", result["committed_route_token"])

    def test_gate_reject_skips_tier_routing(self) -> None:
        """T-TG-ENGINE-08：R1-R7 拒绝时，分层路由不执行（先门控后分层）。"""
        result = self._evaluate(
            IntentDecisionEngine(
                ledger=SQLiteLedger(db_path=":memory:"),
                token_service=self.tokens,
                registry=self.registry,
                policy_cache=self.policy_cache,
            ),
            "low_risk_skill",
            seq=1,
        )
        # 用低 trust_level 触发 R1 拒绝
        intent = _make_intent(skill_name="low_risk_skill", seq=99, session_id="session::gate_reject::1")
        engine_low_trust = IntentDecisionEngine(
            ledger=SQLiteLedger(db_path=":memory:"),
            token_service=self.tokens,
            registry=self.registry,
            policy_cache=GovernorPolicyCache(),
        )
        result_reject = engine_low_trust.evaluate_intent(
            intent,
            current_ms=self.now_ms,
            governance_snapshot={"trust_level": 0},  # 触发 R1 拒绝
        )
        self.assertEqual("REJECT", result_reject["decision"])
        # 拒绝时不应有 GOV_OK_CACHED / GOV_OK_RULE
        token = result_reject.get("committed_route_token", {}).get("token", "")
        self.assertNotIn(token, (RouteToken.GOV_OK_CACHED.value, RouteToken.GOV_OK_RULE.value))


# ===========================================================================
# T-TG-AUDIT-05：退役 SkillLoader 不得恢复执行权
# ===========================================================================

class TestTieredGovernanceAuditM42(unittest.TestCase):
    """只验证退役边界仍然拒绝 token-only Skill 执行。"""

    def test_retired_loader_tier_cannot_restore_execution_authority(self) -> None:
        """旧审计 tier 只能被读取，不能恢复 token-only Skill 执行。"""
        ledger = SQLiteLedger(db_path=":memory:")
        tokens = TokenService(approval_ttl_ms=60_000, confirm_ttl_ms=60_000, undo_ttl_ms=60_000)
        registry = SkillRegistry()
        registry.register(_make_meta(skill_name="light_skill", risk_level=1))
        loader = SkillLoader(registry=registry, token_service=tokens, ledger=ledger)

        now = now_ms()
        intent = _make_intent(skill_name="light_skill", seq=1, session_id="session::light::1")
        intent["request_id"] = "req::light::1"

        # 构造带 approval_tier=cache 的 governor_output
        approval_ref = tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name="light_skill",
            session_id=intent["session_id"],
            seq=1,
            now_ts_ms=now,
        )
        gov_output = {
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": approval_ref},
            "committed_route_token": {
                "token": "GOV_OK_CACHED",
                "reason_code": "APPROVE_CACHED",
                "committed_at_ms": now,
                "proposer_node_id": "governor",
                "approval_tier": "cache",
            },
        }
        result = loader.execute(intent=intent, governor_output=gov_output, now_ts_ms=now)
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(
            "GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED",
            result.result_code,
        )
        self.assertFalse(result.runtime_payload["side_effects_executed"])


if __name__ == "__main__":
    unittest.main()
