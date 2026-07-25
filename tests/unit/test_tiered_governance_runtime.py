"""M44 分层治理运行时接入测试（T-TG-RT / T-TG-BUILTIN / T-TG-RESP）。

验证 HostGovernanceRuntimeOwner 已正确注入 registry + policy_cache，
以及内置 Skill 注册表和 ChatResponse.approval_tier 字段。
"""

from __future__ import annotations

import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.host_governance_runtime import (
    HostGovernanceRuntimeOwner,
    HostGovernanceRuntimeSettings,
)
from host_brain.governor.policy_cache import GovernorPolicyCache
from host_brain.governor.tier_policy import ApprovalTier, GovernorTierPolicy
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.registry import SkillRegistry


# ===========================================================================
# T-TG-BUILTIN-01~04：内置 Skill 注册表
# ===========================================================================

class TestBuiltinSkillRegistryM44(unittest.TestCase):
    """T-TG-BUILTIN-01~04：验证 build_builtin_skill_registry() 内置 Skill 注册。"""

    def setUp(self) -> None:
        self.registry = build_builtin_skill_registry()
        self.policy = GovernorTierPolicy()

    def test_registry_contains_four_builtin_skills(self) -> None:
        """T-TG-BUILTIN-01：build_builtin_skill_registry() 返回包含内置 Skill 的 SkillRegistry（当前 7 个）。"""
        names = self.registry.list_skill_names()
        self.assertIn("narrative_only", names)
        self.assertIn("memory_recall", names)
        self.assertIn("noop", names)
        self.assertIn("read_only_status", names)
        # M44 后新增：emotion_query / bond_query / session_summary
        self.assertIn("emotion_query", names)
        self.assertIn("bond_query", names)
        self.assertIn("session_summary", names)
        self.assertEqual(7, len(names))

    def test_narrative_only_is_cache_tier(self) -> None:
        """T-TG-BUILTIN-02：narrative_only 的 risk_level=1，GovernorTierPolicy 映射为 ApprovalTier.CACHE。"""
        meta = self.registry.get("narrative_only")
        self.assertIsNotNone(meta)
        self.assertEqual(1, meta.risk_level)
        tier = self.policy.get_approval_tier(meta)
        self.assertEqual(ApprovalTier.CACHE, tier)

    def test_memory_recall_is_cache_tier(self) -> None:
        """T-TG-BUILTIN-03：memory_recall 的 risk_level=1，映射为 ApprovalTier.CACHE。"""
        meta = self.registry.get("memory_recall")
        self.assertIsNotNone(meta)
        self.assertEqual(1, meta.risk_level)
        tier = self.policy.get_approval_tier(meta)
        self.assertEqual(ApprovalTier.CACHE, tier)

    def test_read_only_status_is_cache_tier(self) -> None:
        """T-TG-BUILTIN-04：read_only_status 的 risk_level=1，映射为 ApprovalTier.CACHE。"""
        meta = self.registry.get("read_only_status")
        self.assertIsNotNone(meta)
        self.assertEqual(1, meta.risk_level)
        tier = self.policy.get_approval_tier(meta)
        self.assertEqual(ApprovalTier.CACHE, tier)

    def test_noop_is_cache_tier(self) -> None:
        """noop 的 risk_level=1，映射为 ApprovalTier.CACHE。"""
        meta = self.registry.get("noop")
        self.assertIsNotNone(meta)
        self.assertEqual(1, meta.risk_level)
        tier = self.policy.get_approval_tier(meta)
        self.assertEqual(ApprovalTier.CACHE, tier)

    def test_all_builtin_skills_are_reversible(self) -> None:
        """所有内置 Skill 均为只读操作，reversible=False（无副作用，不需要撤销）。"""
        for name in self.registry.list_skill_names():
            meta = self.registry.get(name)
            self.assertIsNotNone(meta)
            self.assertFalse(meta.reversible, f"{name} should be reversible=False (read-only, no side effects)")

    def test_all_builtin_skills_have_low_clearance(self) -> None:
        """所有内置 Skill 均为 clearance='LOW'。"""
        for name in self.registry.list_skill_names():
            meta = self.registry.get(name)
            self.assertIsNotNone(meta)
            self.assertEqual("LOW", meta.clearance, f"{name} should have LOW clearance")

    def test_build_returns_new_instance_each_time(self) -> None:
        """build_builtin_skill_registry() 每次返回新实例（工厂函数，非单例）。"""
        r1 = build_builtin_skill_registry()
        r2 = build_builtin_skill_registry()
        self.assertIsNot(r1, r2)
        # 但内容相同
        self.assertEqual(r1.list_skill_names(), r2.list_skill_names())


# ===========================================================================
# T-TG-RT-01~04：dependencies 工厂函数注入验证
# ===========================================================================

class TestIntentEngineInjectionM44(unittest.TestCase):
    """T-TG-RT-01~04：验证 Host owner 注入 registry + policy_cache。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(":memory:")
        self.owner = HostGovernanceRuntimeOwner(
            ledger_provider=lambda: self.ledger,
            persistence_db_path_provider=lambda: ":memory:",
            settings_provider=lambda: HostGovernanceRuntimeSettings(
                mcp_registry_db_path=":memory:",
                mcp_bridge_enabled=False,
                skill_provider_flags=(),
            ),
            skill_provider_registrar=lambda *_args, **_kwargs: 0,
        )
        self.owner.begin()

    def tearDown(self) -> None:
        self.owner.close()
        self.ledger.close()

    def test_owner_intent_engine_has_registry(self) -> None:
        """T-TG-RT-01：owner 返回的 IntentDecisionEngine 持有 registry。"""
        engine = self.owner.require_intent_engine()
        self.assertIsNotNone(engine._registry)
        self.assertIsInstance(engine._registry, SkillRegistry)

    def test_owner_intent_engine_has_policy_cache(self) -> None:
        """T-TG-RT-02：owner 返回的 engine 持有唯一 policy cache。"""
        engine = self.owner.require_intent_engine()
        self.assertIsNotNone(engine._policy_cache)
        self.assertIsInstance(engine._policy_cache, GovernorPolicyCache)

    def test_intent_engine_is_exact_within_owner_epoch(self) -> None:
        """T-TG-RT-03：同一 owner epoch 多次解析返回 exact engine。"""
        engine1 = self.owner.require_intent_engine()
        engine2 = self.owner.require_intent_engine()
        self.assertIs(engine1, engine2)

    def test_owner_skill_registry_contains_builtin_skills(self) -> None:
        """T-TG-RT-04：owner 的 SkillRegistry 包含内置 Skill。"""
        registry = self.owner.require_skill_registry()
        names = registry.list_skill_names()
        self.assertIn("narrative_only", names)
        self.assertIn("memory_recall", names)
        self.assertIn("noop", names)
        self.assertIn("read_only_status", names)

    def test_policy_cache_is_owned_by_the_intent_engine(self) -> None:
        """Policy cache follows the composed engine instead of a second singleton."""
        from host_brain.composition import process_services

        engine1 = self.owner.require_intent_engine()
        engine2 = self.owner.require_intent_engine()

        self.assertIs(engine1._policy_cache, engine2._policy_cache)
        self.assertFalse(hasattr(process_services, "get_policy_cache"))

    def test_intent_engine_policy_cache_has_correct_ttl(self) -> None:
        """The engine-owned GovernorPolicyCache keeps the 60-second TTL."""
        cache = self.owner.require_intent_engine()._policy_cache
        self.assertEqual(60_000, cache._ttl_ms)


# ===========================================================================
# T-TG-RESP-01~02：ChatResponse.approval_tier 字段
# ===========================================================================

class TestChatResponseApprovalTierM44(unittest.TestCase):
    """T-TG-RESP-01~02：验证 ChatResponse 包含 approval_tier 字段。"""

    def test_chat_response_has_approval_tier_field(self) -> None:
        """T-TG-RESP-01：ChatResponse 包含 approval_tier 字段。"""
        from host_brain.app.schemas import ChatResponse
        from host_brain.core.ids import now_ms
        resp = ChatResponse(
            request_id="req::test",
            narrative="test",
            decision="APPROVE",
            reason_code=None,
            approval_token_ref=None,
            approval_tier="cache",
            quality={"stage": "S_COMPLETE"},
            audit_refs={},
            timestamp=now_ms(),
        )
        self.assertEqual("cache", resp.approval_tier)

    def test_chat_response_approval_tier_none_backward_compat(self) -> None:
        """T-TG-RESP-02：approval_tier 为 None 时不影响响应结构（向后兼容）。"""
        from host_brain.app.schemas import ChatResponse
        from host_brain.core.ids import now_ms
        resp = ChatResponse(
            request_id="req::test",
            narrative="test",
            decision="APPROVE",
            reason_code=None,
            approval_token_ref=None,
            approval_tier=None,
            quality={"stage": "S_COMPLETE"},
            audit_refs={},
            timestamp=now_ms(),
        )
        self.assertIsNone(resp.approval_tier)
        # 确保其他字段不受影响
        self.assertEqual("req::test", resp.request_id)
        self.assertEqual("APPROVE", resp.decision)

    def test_chat_response_approval_tier_all_valid_values(self) -> None:
        """approval_tier 支持所有合法值（cache/rule/full/airlock）。"""
        from host_brain.app.schemas import ChatResponse
        from host_brain.core.ids import now_ms
        for tier in ("cache", "rule", "full", "airlock"):
            resp = ChatResponse(
                request_id=f"req::{tier}",
                narrative="test",
                decision="APPROVE",
                reason_code=None,
                approval_token_ref=None,
                approval_tier=tier,
                quality={"stage": "S_COMPLETE"},
                audit_refs={},
                timestamp=now_ms(),
            )
            self.assertEqual(tier, resp.approval_tier)


# ===========================================================================
# 集成验证：分层治理在 narrative_only 请求中真正生效
# ===========================================================================

class TestTieredGovernanceEndToEndM44(unittest.TestCase):
    """验证分层治理在 narrative_only 请求中真正生效（缓存命中路径）。"""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(":memory:")
        self.owner = HostGovernanceRuntimeOwner(
            ledger_provider=lambda: self.ledger,
            persistence_db_path_provider=lambda: ":memory:",
            settings_provider=lambda: HostGovernanceRuntimeSettings(
                mcp_registry_db_path=":memory:",
                mcp_bridge_enabled=False,
                skill_provider_flags=(),
            ),
            skill_provider_registrar=lambda *_args, **_kwargs: 0,
        )
        self.owner.begin()

    def tearDown(self) -> None:
        self.owner.close()
        self.ledger.close()

    def test_narrative_only_uses_cache_or_rule_tier(self) -> None:
        """narrative_only 请求应走 CACHE 或 RULE 通道（不走 FULL 通道）。"""
        from host_brain.core.ids import now_ms

        engine = self.owner.require_intent_engine()
        now = now_ms()
        intent = {
            "schema_version": "Natsume.intent.v1",
            "id": "intent::m44::e2e::1",
            "ts_ms": now - 500,
            "session_id": "session::m44::e2e::1",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "M44 分层治理集成测试"},
            "nonce": "nonce::m44::e2e::1",
        }
        result = engine.evaluate_intent(
            raw_intent=intent,
            current_ms=now,
            governance_snapshot={"trust_level": 80, "energy_level": 60},
        )
        self.assertEqual("APPROVE", result["decision"])
        token = result["committed_route_token"]["token"]
        # narrative_only 是 risk_level=1，应走 CACHE 或 RULE 通道
        self.assertIn(token, ("GOV_OK_CACHED", "GOV_OK_RULE"),
                      f"Expected CACHE or RULE tier, got: {token}")
        # 验证 approval_tier 字段存在
        self.assertIn("approval_tier", result["committed_route_token"])

    def test_second_narrative_only_hits_cache(self) -> None:
        """第二次 narrative_only 请求应命中 Policy Cache，走 CACHE 通道。"""
        from host_brain.core.ids import now_ms

        engine = self.owner.require_intent_engine()
        now = now_ms()
        snapshot = {"trust_level": 80, "energy_level": 60}

        # 第一次：缓存未命中，走 RULE 通道
        intent1 = {
            "schema_version": "Natsume.intent.v1",
            "id": "intent::m44::cache::1",
            "ts_ms": now - 500,
            "session_id": "session::m44::cache::1",
            "seq": 1,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "第一次请求"},
            "nonce": "nonce::m44::cache::1",
        }
        result1 = engine.evaluate_intent(
            raw_intent=intent1,
            current_ms=now,
            governance_snapshot=snapshot,
        )
        self.assertEqual("APPROVE", result1["decision"])

        # 第二次：缓存命中，走 CACHE 通道
        intent2 = {
            "schema_version": "Natsume.intent.v1",
            "id": "intent::m44::cache::2",
            "ts_ms": now - 400,
            "session_id": "session::m44::cache::2",
            "seq": 2,
            "type": "query",
            "action": "narrative_only",
            "target": "host",
            "args": {"text": "第二次请求"},
            "nonce": "nonce::m44::cache::2",
        }
        result2 = engine.evaluate_intent(
            raw_intent=intent2,
            current_ms=now,
            governance_snapshot=snapshot,
        )
        self.assertEqual("APPROVE", result2["decision"])
        token2 = result2["committed_route_token"]["token"]
        self.assertEqual("GOV_OK_CACHED", token2,
                         f"Second request should hit cache, got: {token2}")


if __name__ == "__main__":
    unittest.main()
