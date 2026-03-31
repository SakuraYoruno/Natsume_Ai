"""AIRLOCK 凭证/敏感信息过滤器。

对文本中的凭证类敏感信息进行检测和脱敏，防止通过云端 LLM 泄露。
纯正则实现，零外部依赖，延迟 < 1ms。

设计哲学：
  对于 AI 伴侣来说，知道主人的手机号、邮箱、地址是很自然的事。
  真正需要拦截的是**凭证类信息**——密码、API 密钥、身份证号、银行卡号等，
  这些信息一旦泄露给云端 LLM 提供商可能造成实际损失。

支持的敏感类型：
  - 密码/口令（明文出现在对话中的 password=xxx 等模式）
  - API 密钥/Token（sk-xxx, ghp_xxx, AKIA 等常见格式）
  - 身份证号（18 位）
  - 银行卡号（16-19 位）
  - 含敏感参数的 URL（带 token/key/secret/password 参数的 URL）

配置（环境变量）：
  HOST_PII_FILTER_ENABLED   — 功能开关（默认 false）
  HOST_PII_FILTER_MODE      — redact / log_only / block（默认 redact）
  HOST_PII_FILTER_RULES     — 启用的规则（逗号分隔，默认全部凭证规则）
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
    # ── 凭证类（真正不该泄露给云端的）─────────────────────────────
    "password": PiiRule(
        rule_id="password",
        label="密码/口令",
        pattern=re.compile(
            r"(?i)"
            r"(?:password|passwd|pwd|口令|密码)"
            r"\s*[:=：]\s*"
            r"(\S{4,64})",
        ),
        placeholder="[密码]",
    ),
    "api_key": PiiRule(
        rule_id="api_key",
        label="API密钥/Token",
        pattern=re.compile(
            r"(?:"
            # OpenAI / Anthropic style
            r"sk-[a-zA-Z0-9_-]{20,}"
            # GitHub PAT
            r"|ghp_[a-zA-Z0-9]{36,}"
            r"|gho_[a-zA-Z0-9]{36,}"
            r"|github_pat_[a-zA-Z0-9_]{22,}"
            # AWS
            r"|AKIA[0-9A-Z]{16}"
            # Generic long hex/base64 tokens (32+ chars after key= or token=)
            r"|(?i:(?:api[_-]?key|secret[_-]?key|access[_-]?token|bearer)\s*[:=]\s*)[a-zA-Z0-9_/+=-]{20,}"
            r")",
        ),
        placeholder="[密钥]",
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

_DEFAULT_RULE_IDS = ("password", "api_key", "cn_id_card", "cn_bank_card", "url_with_params")

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
