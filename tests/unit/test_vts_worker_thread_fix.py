"""测试 vts.py worker 线程 bug 修复（T-VTS-THREAD-01~03）。

Bug：resolve_vts_expression 在 asyncio.to_thread 的 worker 线程中被调用时，
     原来的 asyncio.ensure_future 是线程不安全的，会导致 _fire_edge_vts 协程
     从未被调度执行（VTS 表情驱动静默失败）。

修复：改用 asyncio.run_coroutine_threadsafe，线程安全地将协程提交到主事件循环。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestVTSWorkerThreadFix:
    """T-VTS-THREAD-01~03：验证 worker 线程中 VTS 驱动协程能正确调度。"""

    @pytest.mark.asyncio
    async def test_t_vts_thread_01_fire_edge_vts_called_from_worker_thread(self):
        """T-VTS-THREAD-01：通过 asyncio.to_thread 调用 resolve_vts_expression 时，
        _fire_edge_vts 协程应被正确调度并执行（post_vts 被调用）。"""
        from host_brain.channels.vts import resolve_vts_expression, STATUS_APPLIED

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        post_vts_called = asyncio.Event()
        loop = asyncio.get_running_loop()

        # 注册主事件循环（模拟 runtime_lifespan 启动时的行为）
        import host_brain.channels.vts as _vts_mod
        _vts_mod.register_main_loop(loop)

        mock_post_vts = AsyncMock(side_effect=lambda **kwargs: loop.call_soon_threadsafe(post_vts_called.set) or None)

        with patch(
            "host_brain.channels.edge_rpc_client.post_vts",
            new=mock_post_vts,
        ):
            # 模拟 chat_stream_handler 中的调用方式：通过 asyncio.to_thread 在 worker 线程中执行
            result = await asyncio.to_thread(
                resolve_vts_expression,
                emotion_tag="HAPPY",
                ledger=mock_ledger,
                request_id="req_thread_01",
                edge_available=True,
            )

        # 审计结果应为 APPLIED
        assert result.status == STATUS_APPLIED
        from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID

        assert result.expression_id == EMOTION_TO_EXPRESSION_ID["HAPPY"]

        # 等待 _fire_edge_vts 协程被调度执行（最多 2 秒）
        try:
            await asyncio.wait_for(post_vts_called.wait(), timeout=2.0)
            vts_fired = True
        except asyncio.TimeoutError:
            vts_fired = False

        assert vts_fired, (
            "post_vts 未被调用！说明 _fire_edge_vts 协程在 worker 线程中未被正确调度。"
            "这是 asyncio.ensure_future 线程不安全 bug 的复现。"
        )

    @pytest.mark.asyncio
    async def test_t_vts_thread_02_run_coroutine_threadsafe_used(self):
        """T-VTS-THREAD-02：验证修复后使用 run_coroutine_threadsafe 而非 ensure_future。"""
        import inspect
        import host_brain.channels.vts as vts_module

        source = inspect.getsource(vts_module.resolve_vts_expression)

        # 确认修复后的代码使用 run_coroutine_threadsafe
        assert "run_coroutine_threadsafe" in source, (
            "resolve_vts_expression 应使用 asyncio.run_coroutine_threadsafe"
        )
        # 确认 ensure_future 仅出现在注释中，不作为实际调用
        # 统计 ensure_future 出现次数：只允许出现在注释行（以 # 开头的行）
        lines_with_ensure_future = [
            line.strip() for line in source.splitlines()
            if "ensure_future" in line
        ]
        non_comment_uses = [
            line for line in lines_with_ensure_future
            if not line.startswith("#")
        ]
        assert len(non_comment_uses) == 0, (
            f"resolve_vts_expression 不应在非注释代码中使用 asyncio.ensure_future（线程不安全）。"
            f"发现以下非注释行：{non_comment_uses}"
        )

    @pytest.mark.asyncio
    async def test_t_vts_thread_03_no_crash_when_no_event_loop(self):
        """T-VTS-THREAD-03：当无法获取事件循环时，resolve_vts_expression 应静默跳过，
        不抛出异常，审计结果仍为 APPLIED。"""
        from host_brain.channels.vts import resolve_vts_expression, STATUS_APPLIED

        mock_ledger = MagicMock()
        mock_ledger.log_avatar_action = MagicMock()

        # 模拟 get_event_loop 抛出 RuntimeError（无事件循环场景）
        original_get_running_loop = asyncio.get_running_loop
        original_get_event_loop = asyncio.get_event_loop

        def raise_runtime_error():
            raise RuntimeError("no running event loop")

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running loop")):
            with patch("asyncio.get_event_loop", side_effect=RuntimeError("no event loop")):
                # 直接同步调用（非 worker 线程场景）
                result = resolve_vts_expression(
                    emotion_tag="CALM",
                    ledger=mock_ledger,
                    request_id="req_thread_03",
                    edge_available=True,
                )

        # 即使无事件循环，审计结果仍应为 APPLIED，不应崩溃
        assert result.status == STATUS_APPLIED
        from host_brain.channels.vts import EMOTION_TO_EXPRESSION_ID

        assert result.expression_id == EMOTION_TO_EXPRESSION_ID["CALM"]
