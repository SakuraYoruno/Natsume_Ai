"""Edge 视觉定位器：截图 → VLM 推理 → 返回控件坐标。

设计原则：
  - 调用本地 Ollama HTTP API（/api/chat），不依赖 host_brain LLM 链
  - 输出永远是坐标 + 置信度，不包含任何裁决或执行事实
  - 不可用时快速失败，返回结构化错误，不阻塞主流程
  - 支持 base64 图像直接传入（来自 DesktopController.capture_screenshot）
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("Natsume.edge.actuators.vision_locator")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

REASON_VLM_UNAVAILABLE = "VLM_UNAVAILABLE"
REASON_VLM_PARSE_FAILED = "VLM_PARSE_FAILED"
REASON_VLM_TIMEOUT = "VLM_TIMEOUT"
REASON_VLM_DEPENDENCY_MISSING = "VLM_DEPENDENCY_MISSING"
REASON_VLM_NO_TARGET = "VLM_NO_TARGET"

_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5vl:7b"
_DEFAULT_TIMEOUT_S = 30.0

# 提示词模板：要求模型只返回 JSON，不输出多余文字
_LOCATE_PROMPT_TEMPLATE = """你是一个 UI 视觉定位助手。
请在截图中找到以下目标控件，并返回其中心点坐标（像素）。

目标描述：{instruction}

要求：
1. 只返回 JSON，格式如下，不要输出任何其他文字：
{{"found": true, "x": <整数>, "y": <整数>, "confidence": <0.0-1.0>, "description": "<简短说明>"}}
2. 如果找不到目标，返回：
{{"found": false, "x": 0, "y": 0, "confidence": 0.0, "description": "<原因>"}}
3. 坐标为图像像素坐标，原点在左上角。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LocateResult:
    """视觉定位结果。"""
    success: bool
    found: bool
    x: int
    y: int
    confidence: float
    description: str
    latency_ms: int
    reason_code: str | None = None
    raw_response: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "found": self.found,
            "x": self.x,
            "y": self.y,
            "confidence": self.confidence,
            "description": self.description,
            "latency_ms": self.latency_ms,
            "reason_code": self.reason_code,
        }


@dataclass
class VisionLocatorConfig:
    """VisionLocator 配置。"""
    ollama_base_url: str = _DEFAULT_OLLAMA_BASE_URL
    model: str = _DEFAULT_MODEL
    timeout_s: float = _DEFAULT_TIMEOUT_S
    # 置信度阈值：低于此值视为未找到
    confidence_threshold: float = 0.3
    # 最大图像宽度（超出则等比缩放，减少 token 消耗）
    max_image_width: int = 1280


# ---------------------------------------------------------------------------
# 视觉定位器
# ---------------------------------------------------------------------------

class VisionLocator:
    """Edge 视觉定位器：调用本地 Ollama VLM 对截图进行控件定位。

    Args:
        config: VisionLocatorConfig 配置对象
    """

    def __init__(self, config: VisionLocatorConfig | None = None) -> None:
        self._config = config or VisionLocatorConfig()
        self._stats: dict[str, int] = {
            "locate_calls": 0,
            "locate_success": 0,
            "locate_failed": 0,
            "locate_not_found": 0,
        }

    # ------------------------------------------------------------------
    # 主接口
    # ------------------------------------------------------------------

    def locate(
        self,
        image_b64: str,
        instruction: str,
        *,
        image_mime: str = "image/png",
        image_width: int | None = None,
        image_height: int | None = None,
    ) -> LocateResult:
        """在截图中定位目标控件。

        Args:
            image_b64: base64 编码的截图（来自 DesktopController.capture_screenshot）
            instruction: 自然语言描述，例如 "微信发送按钮" / "搜索框"
            image_mime: 图像 MIME 类型，默认 image/png
            image_width: 原始图像宽度（用于缩放判断）
            image_height: 原始图像高度（用于缩放判断）

        Returns:
            LocateResult，包含坐标、置信度、延迟等信息
        """
        t0 = time.monotonic()
        self._stats["locate_calls"] += 1

        if not image_b64:
            self._stats["locate_failed"] += 1
            return self._fail(
                reason_code=REASON_VLM_UNAVAILABLE,
                description="image_b64 为空，无法定位",
                latency_ms=0,
            )

        if not instruction or not instruction.strip():
            self._stats["locate_failed"] += 1
            return self._fail(
                reason_code=REASON_VLM_UNAVAILABLE,
                description="instruction 为空，无法定位",
                latency_ms=0,
            )

        # 若图像过宽，等比缩放以减少 token 消耗
        actual_b64 = image_b64
        if image_width and image_width > self._config.max_image_width:
            actual_b64 = self._resize_image_b64(
                image_b64,
                max_width=self._config.max_image_width,
                image_mime=image_mime,
            )

        prompt = _LOCATE_PROMPT_TEMPLATE.format(instruction=instruction.strip())

        try:
            raw_response = self._call_ollama(actual_b64, prompt, image_mime=image_mime)
        except TimeoutError:
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._stats["locate_failed"] += 1
            return self._fail(
                reason_code=REASON_VLM_TIMEOUT,
                description=f"Ollama 调用超时（>{self._config.timeout_s}s）",
                latency_ms=latency_ms,
            )
        except ImportError as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._stats["locate_failed"] += 1
            return self._fail(
                reason_code=REASON_VLM_DEPENDENCY_MISSING,
                description=f"依赖缺失: {exc}",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._stats["locate_failed"] += 1
            logger.warning("VisionLocator: Ollama 调用失败: %s", exc)
            return self._fail(
                reason_code=REASON_VLM_UNAVAILABLE,
                description=f"Ollama 调用失败: {exc}",
                latency_ms=latency_ms,
            )

        latency_ms = int((time.monotonic() - t0) * 1000)

        # 解析 VLM 输出
        parsed = self._parse_response(raw_response)
        if parsed is None:
            self._stats["locate_failed"] += 1
            return self._fail(
                reason_code=REASON_VLM_PARSE_FAILED,
                description=f"VLM 输出解析失败，原始响应: {raw_response[:200]}",
                latency_ms=latency_ms,
                raw_response=raw_response,
            )

        found = bool(parsed.get("found", False))
        x = int(parsed.get("x") or 0)
        y = int(parsed.get("y") or 0)
        confidence = float(parsed.get("confidence") or 0.0)
        description = str(parsed.get("description") or "")

        # 置信度过低视为未找到
        if found and confidence < self._config.confidence_threshold:
            found = False
            description = f"置信度过低（{confidence:.2f} < {self._config.confidence_threshold}）: {description}"

        if not found:
            self._stats["locate_not_found"] += 1
            return LocateResult(
                success=True,
                found=False,
                x=0,
                y=0,
                confidence=confidence,
                description=description,
                latency_ms=latency_ms,
                reason_code=REASON_VLM_NO_TARGET,
                raw_response=raw_response,
            )

        self._stats["locate_success"] += 1
        logger.debug(
            "VisionLocator: 定位成功 instruction=%r x=%d y=%d confidence=%.2f latency=%dms",
            instruction, x, y, confidence, latency_ms,
        )
        return LocateResult(
            success=True,
            found=True,
            x=x,
            y=y,
            confidence=confidence,
            description=description,
            latency_ms=latency_ms,
            reason_code=None,
            raw_response=raw_response,
        )

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """返回定位器健康快照。"""
        return {
            "ollama_base_url": self._config.ollama_base_url,
            "model": self._config.model,
            "timeout_s": self._config.timeout_s,
            "confidence_threshold": self._config.confidence_threshold,
            "max_image_width": self._config.max_image_width,
            "stats": dict(self._stats),
        }

    def ping_ollama(self) -> bool:
        """检查 Ollama 服务是否可达。"""
        try:
            import urllib.request
            url = f"{self._config.ollama_base_url.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3.0):
                return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_ollama(
        self,
        image_b64: str,
        prompt: str,
        *,
        image_mime: str = "image/png",
    ) -> str:
        """调用 Ollama /api/chat 接口（同步，使用标准库 urllib）。"""
        import urllib.request

        url = f"{self._config.ollama_base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self._config.model,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._config.timeout_s) as resp:
            raw = resp.read().decode("utf-8")

        data = json.loads(raw)
        # Ollama /api/chat 响应格式：{"message": {"content": "..."}, ...}
        content = data.get("message", {}).get("content", "")
        return str(content)

    def _parse_response(self, raw: str) -> dict[str, Any] | None:
        """从 VLM 输出中提取 JSON 对象。"""
        text = raw.strip()
        # 尝试直接解析
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # 尝试从文本中提取第一个 {...} 块
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return None

    def _resize_image_b64(
        self,
        image_b64: str,
        *,
        max_width: int,
        image_mime: str = "image/png",
    ) -> str:
        """等比缩放图像（需要 Pillow）。失败时返回原始 base64。"""
        try:
            import base64
            from io import BytesIO
            from PIL import Image

            raw_bytes = base64.b64decode(image_b64)
            img = Image.open(BytesIO(raw_bytes))
            w, h = img.size
            if w <= max_width:
                return image_b64
            ratio = max_width / w
            new_h = int(h * ratio)
            img = img.resize((max_width, new_h), Image.LANCZOS)
            buf = BytesIO()
            fmt = "PNG" if "png" in image_mime.lower() else "JPEG"
            img.save(buf, format=fmt)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as exc:
            logger.debug("VisionLocator: 图像缩放失败（使用原图）: %s", exc)
            return image_b64

    @staticmethod
    def _fail(
        *,
        reason_code: str,
        description: str,
        latency_ms: int,
        raw_response: str | None = None,
    ) -> LocateResult:
        return LocateResult(
            success=False,
            found=False,
            x=0,
            y=0,
            confidence=0.0,
            description=description,
            latency_ms=latency_ms,
            reason_code=reason_code,
            raw_response=raw_response,
        )
