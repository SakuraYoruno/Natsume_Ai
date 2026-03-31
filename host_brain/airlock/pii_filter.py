"""AIRLOCK PII 过滤器。

对文本中的个人身份信息（PII）进行检测和脱敏。
纯正则实现，零外部依赖，延迟 < 1ms。

支持的 PII 类型：
  - 中国手机号（11 位）
  - 身份证号（18 位）
  - 银行卡号（16-19 位）
  - 邮箱地址
  - IPv4 地址
  - 中国车牌号
  - 含敏感参数的 URL

配置（环境变量）：
  HOST_PII_FILTER_ENABLED   — 功能开关（默认 false）
  HOST_PII_FILTER_MODE      — redact / log_only / block（默认 redact）
  HOST_PII_FILTER_RULES     — 启用的规则（逗号分隔，默认 cn_phone,cn_id_card,cn_bank_card,email）
  HOST_PII_FILTER_INGRESS   — 入口脱敏开关（默认 true）
  HOST_PII_FILTER_EGRESS    — 出口脱敏开关（默认 true）
  HOST_PII_FILTER_RETRIEVAL — 检索结果脱敏开关（默认 true）
  HOST_PII_FILTER_MEMORY    — 记忆写入脱敏开关（默认 true）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.airlock.pii_filter")

# ---------------------------------------------------------------------------
# 规则定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PiiRule:
    """一条 PII 检测规则。"""

    rule_id: str
    label: str
    pattern: re.Pattern[str]
    placeholder: str


# 预编译规则集（模块加载时一次性编译）
_ALL_RULES: dict[str, PiiRule] = {
    "cn_phone": PiiRule(
        rule_id="cn_phone",
        label="中国手机号",
        pattern=re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        placeholder="[手机号]",
    ),
    "cn_id_card": PiiRule(
        rule_id="cn_id_card",
        label="身份证号",
        pattern=re.compile(
            r"(?<!\d)[1-9]\d{5}"
            r"(?:19|20)\d{2}"
            r"(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])"
            r"\d{3}[\dXx](?!\d)"
        ),
        placeholder="[身份证]",
    ),
    "cn_bank_card": PiiRule(
        rule_id="cn_bank_card",
        label="银行卡号",
        pattern=re.compile(r"(?<!\d)(?:62|4\d|5[1-5])\d{13,17}(?!\d)"),
        placeholder="[银行卡]",
    ),
    "email": PiiRule(
        rule_id="email",
        label="邮箱地址",
        pattern=re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        placeholder="[邮箱]",
    ),
    "ip_addr": PiiRule(
        rule_id="ip_addr",
        label="IP地址",
        pattern=re.compile(
            r"(?<!\d)"
            r"(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
            r"(?!\d)"
        ),
        placeholder="[IP]",
    ),
    "cn_plate": PiiRule(
        rule_id="cn_plate",
        label="车牌号",
        pattern=re.compile(
            r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁]"
            r"[A-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]"
        ),
        placeholder="[车牌]",
    ),
    "url_with_params": PiiRule(
        rule_id="url_with_params",
        label="敏感URL",
        pattern=re.compile(
            r"https?://\S+[?&]\S*(?:token|key|secret|password|auth)\S*",
            re.IGNORECASE,
        ),
        placeholder="[敏感URL]",
    ),
}

_DEFAULT_RULE_IDS = ("cn_phone", "cn_id_card", "cn_bank_card", "email")

# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------


def _is_enabled() -> bool:
    return os.environ.get("HOST_PII_FILTER_ENABLED", "").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def _get_mode() -> str:
    return os.environ.get("HOST_PII_FILTER_MODE", "redact").strip().lower()


def _get_active_rules() -> list[PiiRule]:
    raw = os.environ.get("HOST_PII_FILTER_RULES", "").strip()
    if not raw:
        rule_ids = list(_DEFAULT_RULE_IDS)
    else:
        rule_ids = [r.strip().lower() for r in raw.split(",") if r.strip()]
    return [_ALL_RULES[rid] for rid in rule_ids if rid in _ALL_RULES]


def _check_channel(channel: str) -> bool:
    """检查某个脱敏通道是否启用。"""
    env_key = f"HOST_PII_FILTER_{channel.upper()}"
    return os.environ.get(env_key, "true").strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------


@dataclass
class PiiScrubResult:
    """PII 脱敏操作结果。"""

    scrubbed_text: str
    detections: list[dict[str, Any]] = field(default_factory=list)
    total_detections: int = 0
    mode: str = "redact"

    @property
    def has_pii(self) -> bool:
        return self.total_detections > 0


# ---------------------------------------------------------------------------
# 核心 API
# ---------------------------------------------------------------------------


def pii_scrub_text(text: str, *, context: str = "unknown") -> PiiScrubResult:
    """对文本执行 PII 检测和脱敏。

    Args:
        text: 待检测的文本。
        context: 调用上下文标识（用于日志）。

    Returns:
        PiiScrubResult，包含脱敏后的文本和检测统计。
    """
    if not _is_enabled():
        return PiiScrubResult(scrubbed_text=text, mode="disabled")

    if not text or not text.strip():
        return PiiScrubResult(scrubbed_text=text, mode=_get_mode())

    mode = _get_mode()
    rules = _get_active_rules()
    detections: list[dict[str, Any]] = []
    scrubbed = text

    for rule in rules:
        matches = list(rule.pattern.finditer(scrubbed))
        if not matches:
            continue

        # 从后往前替换，保持索引正确
        for match in reversed(matches):
            detections.append(
                {
                    "rule_id": rule.rule_id,
                    "label": rule.label,
                    "start": match.start(),
                    "end": match.end(),
                    "matched_length": match.end() - match.start(),
                    # 安全起见不记录匹配的原文，只记录长度
                }
            )

            if mode == "redact":
                scrubbed = (
                    scrubbed[: match.start()]
                    + rule.placeholder
                    + scrubbed[match.end() :]
                )

    total = len(detections)

    if total > 0:
        _logger.info(
            "[PII] context=%s mode=%s detections=%d rules=%s",
            context,
            mode,
            total,
            ",".join(sorted({d["rule_id"] for d in detections})),
        )

    if mode == "block" and total > 0:
        scrubbed = "[PII_BLOCKED]"

    return PiiScrubResult(
        scrubbed_text=scrubbed,
        detections=detections,
        total_detections=total,
        mode=mode,
    )


def pii_scrub_if_enabled(text: str, *, context: str = "unknown") -> str:
    """便捷函数：如果 PII 过滤已启用，返回脱敏后的文本；否则原样返回。"""
    return pii_scrub_text(text, context=context).scrubbed_text
