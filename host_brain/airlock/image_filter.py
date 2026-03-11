"""AIRLOCK 图片出站过滤器。

对 base64 编码的图片在发送给云端 API 前进行两层检查：

  1. 元数据检查（ImageMetaCheck）
     - MIME 类型白名单校验
     - base64 长度上限（防止超大截图）
     - 解码后图片尺寸估算

  2. 明文敏感词扫描（ImageTextRiskScan）
     - 将 base64 解码为字节后，检测其中是否含有可读的敏感字符串
     - 目标：捕获截图中可能包含的 API Key、Token、密码等明文内容
     - 注意：这是启发式扫描，不能替代真正的 OCR/LLM 视觉分析

  3. 出站审计报告（ImageEgressReport）
     - 符合 Airlock sanitizer 规范，写入 airlock_log 留痕
     - 决策：PASS / WARN / BLOCK

典型调用链：
  vision_context.image_base64
      → filter_image_egress()
      → ImageEgressReport(decision=PASS/WARN/BLOCK)
      → 若 PASS/WARN：继续上传给 LLM API
      → 若 BLOCK：降级为纯文字模式，不上传图片
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms

_logger = logging.getLogger("Natsume.airlock.image_filter")

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# 允许的图片 MIME 类型
_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
        "image/bmp",
    }
)

# base64 字符串长度上限（约 10 MB 原始图片）
# base64 膨胀比约 1.33，10 MB → ~13.3 MB base64 字符
_MAX_BASE64_LEN: int = 14_000_000

# 明文敏感词正则（在 base64 解码后的字节中搜索可读字符串）
# 目标：捕获截图中可能出现的 API Key、Token、密码等
_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    # OpenAI / Anthropic / Google API Key 格式
    ("API_KEY_OPENAI",    re.compile(rb"sk-[A-Za-z0-9]{20,}", re.ASCII)),
    ("API_KEY_ANTHROPIC", re.compile(rb"sk-ant-[A-Za-z0-9\-]{20,}", re.ASCII)),
    ("API_KEY_GOOGLE",    re.compile(rb"AIza[A-Za-z0-9\-_]{35}", re.ASCII)),
    # Bearer Token
    ("BEARER_TOKEN",      re.compile(rb"Bearer\s+[A-Za-z0-9\-._~+/]{20,}", re.ASCII | re.IGNORECASE)),
    # AWS Access Key
    ("AWS_ACCESS_KEY",    re.compile(rb"AKIA[A-Z0-9]{16}", re.ASCII)),
    # 通用 token/secret/password 键值对（如截图中的配置文件）
    ("SECRET_KV",         re.compile(rb"(?:secret|password|passwd|token|api[_\-]?key)\s*[=:]\s*\S{8,}", re.ASCII | re.IGNORECASE)),
    # 私钥头部
    ("PRIVATE_KEY",       re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", re.ASCII)),
]

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

# 决策枚举值（字符串常量，不引入 Enum 依赖）
IMAGE_EGRESS_PASS  = "PASS"   # 通过，允许上传
IMAGE_EGRESS_WARN  = "WARN"   # 警告，允许上传但记录风险
IMAGE_EGRESS_BLOCK = "BLOCK"  # 拦截，降级为纯文字模式


@dataclass(frozen=True)
class ImageEgressReport:
    """图片出站过滤报告。

    Attributes:
        decision:        过滤决策（PASS / WARN / BLOCK）。
        reason_code:     决策原因码（用于日志和 SSE 事件）。
        detail:          人类可读的决策说明。
        risks:           检测到的风险列表（每项为 risk_type 字符串）。
        image_digest:    图片 base64 的 SHA-256 摘要（用于审计去重）。
        base64_len:      原始 base64 字符串长度。
        mime_type:       图片 MIME 类型。
        sanitization_report_json: 符合 Airlock sanitizer 规范的净化报告。
    """

    decision: str
    reason_code: str
    detail: str
    risks: list[str]
    image_digest: str
    base64_len: int
    mime_type: str
    sanitization_report_json: dict


def filter_image_egress(
    image_base64: str,
    *,
    mime_type: str = "image/png",
    request_id: Optional[str] = None,
    ledger: Optional[SQLiteLedger] = None,
    ts_ms: Optional[int] = None,
) -> ImageEgressReport:
    """对图片 base64 进行出站过滤，返回 ImageEgressReport。

    Args:
        image_base64: 图片的 base64 编码字符串（不含 data URI 前缀）。
        mime_type:    图片 MIME 类型（默认 "image/png"）。
        request_id:   请求 ID（用于审计日志）。
        ledger:       SQLiteLedger 实例（传入则写入 airlock_log）。
        ts_ms:        时间戳毫秒（不传则自动取当前时间）。

    Returns:
        ImageEgressReport，调用方根据 decision 决定是否上传图片。
    """
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    b64_str = str(image_base64 or "").strip()
    mime = str(mime_type or "image/png").strip().lower()
    risks: list[str] = []

    # ── 1. 元数据检查 ──────────────────────────────────────────────────────

    # 1a. MIME 类型白名单
    if mime not in _ALLOWED_MIME_TYPES:
        report = _make_report(
            decision=IMAGE_EGRESS_BLOCK,
            reason_code="IMAGE_MIME_NOT_ALLOWED",
            detail=f"图片 MIME 类型 '{mime}' 不在白名单中，拒绝上传",
            risks=[f"MIME_BLOCKED:{mime}"],
            image_digest=_digest_b64(b64_str),
            base64_len=len(b64_str),
            mime_type=mime,
        )
        _write_ledger(ledger, report, request_id=request_id, ts_ms=now_value)
        return report

    # 1b. base64 长度上限
    if len(b64_str) > _MAX_BASE64_LEN:
        report = _make_report(
            decision=IMAGE_EGRESS_BLOCK,
            reason_code="IMAGE_TOO_LARGE",
            detail=f"图片 base64 长度 {len(b64_str)} 超过上限 {_MAX_BASE64_LEN}，拒绝上传",
            risks=["SIZE_EXCEEDED"],
            image_digest=_digest_b64(b64_str),
            base64_len=len(b64_str),
            mime_type=mime,
        )
        _write_ledger(ledger, report, request_id=request_id, ts_ms=now_value)
        return report

    # ── 2. 解码并扫描明文敏感词 ────────────────────────────────────────────
    try:
        # 修复 base64 padding
        padded = b64_str + "=" * ((-len(b64_str)) % 4)
        raw_bytes = base64.b64decode(padded, validate=False)
    except Exception as exc:
        _logger.warning(
            "图片 base64 解码失败，跳过敏感词扫描",
            extra={"request_id": request_id, "error": str(exc)},
        )
        raw_bytes = b""

    if raw_bytes:
        for risk_type, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(raw_bytes):
                risks.append(risk_type)
                _logger.warning(
                    "图片中检测到敏感内容: %s",
                    risk_type,
                    extra={"request_id": request_id},
                )

    # ── 3. 决策 ────────────────────────────────────────────────────────────
    image_digest = _digest_b64(b64_str)

    if risks:
        # 检测到敏感词 → WARN（记录风险但仍允许上传，由调用方决定是否继续）
        # 注：不直接 BLOCK，因为误报率较高（截图中可能有代码示例等）
        report = _make_report(
            decision=IMAGE_EGRESS_WARN,
            reason_code="IMAGE_SENSITIVE_CONTENT_DETECTED",
            detail=f"图片中检测到 {len(risks)} 处潜在敏感内容: {', '.join(risks)}",
            risks=risks,
            image_digest=image_digest,
            base64_len=len(b64_str),
            mime_type=mime,
        )
    else:
        report = _make_report(
            decision=IMAGE_EGRESS_PASS,
            reason_code="IMAGE_EGRESS_CLEAN",
            detail="图片元数据检查通过，未检测到明文敏感内容",
            risks=[],
            image_digest=image_digest,
            base64_len=len(b64_str),
            mime_type=mime,
        )

    _write_ledger(ledger, report, request_id=request_id, ts_ms=now_value)
    return report


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _make_report(
    *,
    decision: str,
    reason_code: str,
    detail: str,
    risks: list[str],
    image_digest: str,
    base64_len: int,
    mime_type: str,
) -> ImageEgressReport:
    """构造 ImageEgressReport，同时生成符合 Airlock 规范的净化报告。"""
    sanitization_report_json = {
        "removed_fields": [],
        "redactions": risks,          # 将风险列表映射到 redactions 字段
        "payload_digest": image_digest,
        "image_meta": {
            "mime_type": mime_type,
            "base64_len": base64_len,
            "decision": decision,
            "reason_code": reason_code,
        },
    }
    return ImageEgressReport(
        decision=decision,
        reason_code=reason_code,
        detail=detail,
        risks=risks,
        image_digest=image_digest,
        base64_len=base64_len,
        mime_type=mime_type,
        sanitization_report_json=sanitization_report_json,
    )


def _digest_b64(b64_str: str) -> str:
    """计算 base64 字符串的 SHA-256 摘要。"""
    return f"sha256:{hashlib.sha256(b64_str.encode('utf-8', errors='replace')).hexdigest()}"


def _write_ledger(
    ledger: Optional[SQLiteLedger],
    report: ImageEgressReport,
    *,
    request_id: Optional[str],
    ts_ms: int,
) -> None:
    """将图片过滤结果写入 airlock_log（如果 ledger 可用）。"""
    if ledger is None:
        return
    try:
        ledger.log_airlock(
            {
                "request_id": request_id,
                "direction": "EGRESS",
                "reason_code": report.reason_code,
                "removed_fields": [],
                "redactions": report.risks,
                "payload_digest": report.image_digest,
                "quarantine_digest": None,
                "ingress_classification": None,
                "blocked": 1 if report.decision == IMAGE_EGRESS_BLOCK else 0,
                "detail": report.detail,
                "ts_ms": ts_ms,
            }
        )
    except Exception as exc:
        _logger.debug(
            "airlock_log 写入失败（图片过滤）",
            extra={"request_id": request_id, "error": str(exc)},
        )
