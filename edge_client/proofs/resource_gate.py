"""为语音资源闸门导出只读观测证明，供跨进程诊断而非执行仲裁使用。"""

from __future__ import annotations

from typing import Any

from edge_client.rpc.resource_gate import S_VOICE_RESOURCE_GATE, VoiceResourceGate
from natsume_contracts.edge.v1 import now_ms


def build_resource_gate_proof(
    *,
    gate: VoiceResourceGate,
    request_id: str,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """封装某次请求观察到的闸门状态。

    ``snapshot`` 是调用瞬间的诊断切片：并发请求可在它生成后立即改变闸门，
    所以恢复或重试逻辑只能把它当作解释证据，仍须回到 ``VoiceResourceGate``
    重新申请资源。这里不获取、不释放也不修改闸门，避免观测通路拥有执行权。
    """
    return {
        "request_id": request_id,
        "stage": S_VOICE_RESOURCE_GATE,
        "snapshot": gate.snapshot(),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }
