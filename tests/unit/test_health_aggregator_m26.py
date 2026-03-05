"""
M26-B 运行时健康检查加固测试
T-HEALTH-AGG-01~04 / TASK-0223 / FR-OBS-008
"""
from __future__ import annotations

import asyncio
import unittest

from host_brain.obs.service_health_aggregator import (
    ALL_SERVICES,
    ServiceHealthAggregator,
    ServiceHealthResult,
    validate_health_response,
)


class TestHealthAggregatorM26(unittest.TestCase):
    """T-HEALTH-AGG-01~04：运行时健康检查加固"""

    # ------------------------------------------------------------------
    # T-HEALTH-AGG-01：所有已注册服务均出现在 /health 响应中
    # ------------------------------------------------------------------
    def test_t_health_agg_01_all_services_present_in_response(self) -> None:
        """所有已注册服务均出现在 aggregate() 响应的 services 列表中"""
        agg = ServiceHealthAggregator()
        result = asyncio.run(agg.aggregate())

        self.assertEqual(len(ALL_SERVICES), result["total"])
        service_ids = {s["service_id"] for s in result["services"]}
        for svc in ALL_SERVICES:
            self.assertIn(
                svc["service_id"],
                service_ids,
                f"服务 {svc['service_id']} 未出现在响应中",
            )

    # ------------------------------------------------------------------
    # T-HEALTH-AGG-02：单服务超时时该服务 status=DOWN，其余正常返回
    # ------------------------------------------------------------------
    def test_t_health_agg_02_single_service_timeout_returns_down(self) -> None:
        """单服务探测超时：该服务 status=DOWN，其余服务不受影响"""
        async def probe_timeout(service_id: str) -> ServiceHealthResult:
            await asyncio.sleep(10)  # 远超 timeout_ms
            return ServiceHealthResult(service_id, service_id, "host", "UP")

        async def probe_up(service_id: str) -> ServiceHealthResult:
            return ServiceHealthResult(service_id, service_id, "host", "UP", latency_ms=5)

        agg = ServiceHealthAggregator(timeout_ms=50)
        # 只给 sqlite_ledger 注册超时探针，其余注册正常探针
        for svc in ALL_SERVICES:
            if svc["service_id"] == "sqlite_ledger":
                agg.register_probe(svc["service_id"], probe_timeout)
            else:
                agg.register_probe(svc["service_id"], probe_up)

        result = asyncio.run(agg.aggregate())
        svc_map = {s["service_id"]: s for s in result["services"]}

        # 超时服务 status=DOWN
        self.assertEqual("DOWN", svc_map["sqlite_ledger"]["status"])
        # 其余服务 status=UP
        for svc in ALL_SERVICES:
            if svc["service_id"] != "sqlite_ledger":
                self.assertEqual("UP", svc_map[svc["service_id"]]["status"])

    # ------------------------------------------------------------------
    # T-HEALTH-AGG-03：响应结构稳定（字段不随服务状态变化而缺失）
    # ------------------------------------------------------------------
    def test_t_health_agg_03_response_structure_stable_regardless_of_status(self) -> None:
        """响应结构稳定：无论服务 UP/DOWN/UNKNOWN，顶层字段和 service 字段均完整"""
        async def probe_down(service_id: str) -> ServiceHealthResult:
            return ServiceHealthResult(
                service_id, service_id, "host", "DOWN",
                error_summary="模拟故障",
            )

        agg = ServiceHealthAggregator()
        # 注册部分 DOWN 探针
        agg.register_probe("llm_gateway", probe_down)
        agg.register_probe("neo4j", probe_down)

        result = asyncio.run(agg.aggregate())

        # 顶层字段完整
        for key in ("total", "up", "down", "unknown", "services", "timestamp"):
            self.assertIn(key, result, f"顶层字段 {key} 缺失")

        # 每个 service 条目字段完整
        for svc in result["services"]:
            for key in ("service_id", "name", "side", "status"):
                self.assertIn(key, svc, f"service 条目缺少字段 {key}")

        # validate_health_response 通过
        self.assertTrue(validate_health_response(result))

    # ------------------------------------------------------------------
    # T-HEALTH-AGG-04：validate_health_response() 对缺字段响应返回 False
    # ------------------------------------------------------------------
    def test_t_health_agg_04_validate_health_response_rejects_incomplete(self) -> None:
        """validate_health_response() 对缺字段响应返回 False"""
        # 缺少 total 字段
        self.assertFalse(validate_health_response({
            "up": 0, "down": 0, "unknown": 0, "services": [], "timestamp": 0
        }))

        # 缺少 services 字段
        self.assertFalse(validate_health_response({
            "total": 0, "up": 0, "down": 0, "unknown": 0, "timestamp": 0
        }))

        # services 中的条目缺少 status 字段
        self.assertFalse(validate_health_response({
            "total": 1, "up": 0, "down": 0, "unknown": 1, "timestamp": 0,
            "services": [{"service_id": "x", "name": "X", "side": "host"}],
        }))

        # services 不是列表
        self.assertFalse(validate_health_response({
            "total": 0, "up": 0, "down": 0, "unknown": 0,
            "services": "not_a_list", "timestamp": 0,
        }))

        # 非字典输入
        self.assertFalse(validate_health_response(None))  # type: ignore
        self.assertFalse(validate_health_response("string"))  # type: ignore

    # ------------------------------------------------------------------
    # 额外：validate_health_response() 对完整响应返回 True
    # ------------------------------------------------------------------
    def test_t_health_agg_05_validate_health_response_accepts_complete(self) -> None:
        """validate_health_response() 对完整响应返回 True"""
        agg = ServiceHealthAggregator()
        result = asyncio.run(agg.aggregate())
        self.assertTrue(validate_health_response(result))

    # ------------------------------------------------------------------
    # 额外：side_filter 过滤只返回对应侧服务
    # ------------------------------------------------------------------
    def test_t_health_agg_06_side_filter_returns_correct_subset(self) -> None:
        """side_filter='host' 只返回 host 侧服务"""
        agg = ServiceHealthAggregator()
        host_result = asyncio.run(agg.aggregate(side_filter="host"))
        edge_result = asyncio.run(agg.aggregate(side_filter="edge"))

        for svc in host_result["services"]:
            self.assertEqual("host", svc["side"])
        for svc in edge_result["services"]:
            self.assertEqual("edge", svc["side"])

        # host + edge 总数 = 全量
        all_result = asyncio.run(agg.aggregate())
        self.assertEqual(
            all_result["total"],
            host_result["total"] + edge_result["total"],
        )


if __name__ == "__main__":
    unittest.main()
