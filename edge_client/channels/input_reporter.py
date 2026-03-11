"""Edge → Host 输入上报客户端。

负责将 Edge 端的两类输入源上报给 Host Brain：
  - ASR 语音识别结果（source=ASR）
  - 微信小号接收到的消息（source=WECHAT）

设计原则：
  - 同步 HTTP POST，失败静默降级（不阻断 ASR/微信主流程）
  - 支持 HMAC 签名（复用 EdgeConfig.hmac_shared_key）
  - 上报失败时写入本地缓存，供后续重试
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("Natsume.edge.channels.input_reporter")


class InputSourceType(str, Enum):
    """输入来源类型。"""
    ASR = "ASR"           # 语音识别
    WECHAT = "WECHAT"     # 微信小号消息


@dataclass(frozen=True)
class InputReportResult:
    """上报结果。"""
    success: bool
    source: InputSourceType
    reason_code: str | None
    detail: str
    cached: bool = False  # True 表示已写入本地缓存等待重试

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "cached": self.cached,
        }


@dataclass
class _PendingReport:
    """待重试的上报记录。"""
    payload: dict[str, Any]
    source: InputSourceType
    retry_count: int = 0
    created_at: float = field(default_factory=time.monotonic)


class EdgeInputReporter:
    """Edge → Host 输入上报客户端。

    线程安全，支持失败缓存和后台重试。

    Args:
        host_url: Host Brain 地址，如 "http://192.168.1.100:8000"
        hmac_key: HMAC 签名密钥（来自 EdgeConfig.hmac_shared_key），None 时跳过签名
        timeout_s: HTTP 请求超时秒数
        max_cache: 本地缓存最大条数（超出时丢弃最旧的）
        max_retry: 最大重试次数
        retry_interval_s: 后台重试间隔秒数
    """

    INPUT_ENDPOINT = "/v1/host/input"

    def __init__(
        self,
        *,
        host_url: str,
        hmac_key: str | None = None,
        timeout_s: float = 3.0,
        max_cache: int = 500,
        max_retry: int = 5,
        retry_interval_s: float = 10.0,
    ) -> None:
        self._host_url = host_url.rstrip("/")
        self._hmac_key = hmac_key
        self._timeout_s = float(timeout_s)
        self._max_cache = int(max_cache)
        self._max_retry = int(max_retry)
        self._retry_interval_s = float(retry_interval_s)

        self._cache: list[_PendingReport] = []
        self._lock = threading.Lock()
        self._retry_thread: threading.Thread | None = None
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def report_asr(
        self,
        *,
        session_id: str,
        text: str,
        confidence: float,
        is_final: bool,
        seq: int,
        ts_ms: int | None = None,
        alternates: list[dict[str, Any]] | None = None,
        evidence_ref: str | None = None,
    ) -> InputReportResult:
        """上报 ASR 识别结果到 Host Brain。"""
        payload: dict[str, Any] = {
            "source": InputSourceType.ASR.value,
            "session_id": session_id,
            "text": text,
            "confidence": float(confidence),
            "is_final": bool(is_final),
            "seq": int(seq),
            "ts_ms": int(ts_ms or (time.time() * 1000)),
            "alternates": alternates or [],
        }
        if evidence_ref:
            payload["evidence_ref"] = evidence_ref
        return self._send(payload, InputSourceType.ASR)

    def report_wechat(
        self,
        *,
        from_wxid: str,
        to_wxid: str,
        content: str,
        msg_type: str = "TEXT",
        msg_id: str | None = None,
        room_wxid: str | None = None,
        ts_ms: int | None = None,
        raw_extra: dict[str, Any] | None = None,
    ) -> InputReportResult:
        """上报微信小号接收到的消息到 Host Brain。"""
        payload: dict[str, Any] = {
            "source": InputSourceType.WECHAT.value,
            "from_wxid": from_wxid,
            "to_wxid": to_wxid,
            "content": content,
            "msg_type": msg_type,
            "ts_ms": int(ts_ms or (time.time() * 1000)),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        if room_wxid:
            payload["room_wxid"] = room_wxid
        if raw_extra:
            payload["raw_extra"] = raw_extra
        return self._send(payload, InputSourceType.WECHAT)

    def start_retry_loop(self) -> None:
        """启动后台重试线程（可选，用于缓存重试）。"""
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._shutdown.clear()
        self._retry_thread = threading.Thread(
            target=self._retry_loop,
            name="edge-input-reporter-retry",
            daemon=True,
        )
        self._retry_thread.start()
        logger.info("EdgeInputReporter: 后台重试线程已启动")

    def stop_retry_loop(self) -> None:
        """停止后台重试线程。"""
        self._shutdown.set()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=5.0)
        logger.info("EdgeInputReporter: 后台重试线程已停止")

    def cache_size(self) -> int:
        """返回当前缓存条数。"""
        with self._lock:
            return len(self._cache)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _send(self, payload: dict[str, Any], source: InputSourceType) -> InputReportResult:
        """发送 HTTP POST，失败时写入缓存。"""
        try:
            self._do_http_post(payload)
            return InputReportResult(
                success=True,
                source=source,
                reason_code=None,
                detail="上报成功",
            )
        except Exception as exc:
            logger.warning(
                "EdgeInputReporter: 上报失败（source=%s），写入缓存: %s",
                source.value, exc,
            )
            self._cache_report(payload, source)
            return InputReportResult(
                success=False,
                source=source,
                reason_code="INPUT_REPORT_FAILED",
                detail=str(exc),
                cached=True,
            )

    def _do_http_post(self, payload: dict[str, Any]) -> None:
        """执行 HTTP POST，带 HMAC 签名。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Edge-Source": "edge_input_reporter",
        }
        if self._hmac_key:
            ts = str(int(time.time() * 1000))
            sig = hmac.new(
                self._hmac_key.encode("utf-8"),
                (ts + "." + body.decode("utf-8")).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Edge-Ts"] = ts
            headers["X-Edge-Sig"] = sig

        url = self._host_url + self.INPUT_ENDPOINT
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"Host 返回非 2xx 状态码: {resp.status}")

    def _cache_report(self, payload: dict[str, Any], source: InputSourceType) -> None:
        """写入本地缓存，超出上限时丢弃最旧的。"""
        with self._lock:
            if len(self._cache) >= self._max_cache:
                dropped = self._cache.pop(0)
                logger.warning(
                    "EdgeInputReporter: 缓存已满，丢弃最旧记录 source=%s",
                    dropped.source.value,
                )
            self._cache.append(_PendingReport(payload=payload, source=source))

    def _retry_loop(self) -> None:
        """后台重试线程：定期尝试重发缓存中的上报。"""
        while not self._shutdown.wait(timeout=self._retry_interval_s):
            with self._lock:
                pending = list(self._cache)
                self._cache.clear()

            if not pending:
                continue

            still_pending: list[_PendingReport] = []
            for item in pending:
                if item.retry_count >= self._max_retry:
                    logger.warning(
                        "EdgeInputReporter: 重试次数耗尽，丢弃 source=%s text=%.40s",
                        item.source.value,
                        str(item.payload.get("text") or item.payload.get("content") or ""),
                    )
                    continue
                try:
                    self._do_http_post(item.payload)
                    logger.info(
                        "EdgeInputReporter: 重试成功 source=%s retry=%d",
                        item.source.value, item.retry_count + 1,
                    )
                except Exception as exc:
                    item.retry_count += 1
                    still_pending.append(item)
                    logger.debug(
                        "EdgeInputReporter: 重试失败 source=%s retry=%d: %s",
                        item.source.value, item.retry_count, exc,
                    )

            if still_pending:
                with self._lock:
                    # 合并回缓存（新来的优先）
                    self._cache = still_pending + self._cache
                    # 超出上限时截断
                    if len(self._cache) > self._max_cache:
                        self._cache = self._cache[-self._max_cache:]
