"""VTS 表情映射表持久化存储（TASK-0214 M24-B）。

emotion_tag→expression_id 映射表支持持久化版本管理。
映射表变更通过 persistence repository 写入不可变变更 journal。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import (
    AVATAR_INVALID_EMOTION_TAG,
    VTS_MAPPING_LOAD_FAILED,
)
from host_brain.channels.vts_runtime_mapping import DEFAULT_EMOTION_TO_HOTKEY
from host_brain.persistence.vts_mapping_repository import (
    VTS_MAPPING_NOT_FOUND,
    VTSMappingRepository,
    VTSMappingRepositoryError,
)


logger = logging.getLogger(__name__)

# 默认映射表（与 channels/vts.py / configs/vts/vts_hotkey_config.yaml 保持一致）
DEFAULT_EMOTION_MAPPING: dict[str, str] = dict(DEFAULT_EMOTION_TO_HOTKEY)

@dataclass(frozen=True)
class VTSMappingVersion:
    """映射表版本信息。"""
    version: str
    mapping: dict[str, str]
    content_hash: str
    created_ts_ms: int


class VTSMappingStore:
    """VTS 表情映射表持久化存储（TASK-0214 M24-B）。

    映射表通过 persistence repository 持久化并管理不可变版本。
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        """初始化映射表存储。

        Args:
            db_path: 持久化数据库路径。None 时使用内存数据库（仅用于测试）。
        """
        self._db_path = db_path or ":memory:"
        if self._db_path == ":memory:":
            logger.warning(
                "VTSMappingStore 使用内存数据库（db_path=':memory:'），"
                "重启后映射表数据将丢失，生产环境请指定持久化路径。"
            )
        self._repository = VTSMappingRepository(self._db_path)
        self._repository.reconcile_unjournaled_versions()
        self._ensure_default_mapping()

    def _ensure_default_mapping(self) -> None:
        """确保默认映射表存在（首次初始化时写入）。"""
        try:
            self._repository.load_mapping(version="v1.0.0")
            return
        except VTSMappingRepositoryError as exc:
            if exc.reason_code != VTS_MAPPING_NOT_FOUND:
                raise
        self._repository.save_mapping(
            DEFAULT_EMOTION_MAPPING,
            version="v1.0.0",
            changed_by="system_init",
        )

    def save_mapping(
        self,
        mapping: dict[str, str],
        *,
        version: str,
        changed_by: str = "system",
    ) -> str:
        """保存映射表并原子写入不可变变更 journal。

        Args:
            mapping: emotion_tag→expression_id 映射字典。
            version: 新版本号（如 "v1.1.0"）。
            changed_by: 变更来源（用于审计）。

        Returns:
            新版本的内容 hash。
        """
        record = self._repository.save_mapping(
            mapping,
            version=version,
            changed_by=changed_by,
        )
        return str(record["result"]["content_hash"])

    def load_mapping(self, version: str | None = None) -> VTSMappingVersion:
        """按版本加载映射表。

        Args:
            version: 版本号。None 时加载最新版本。

        Returns:
            VTSMappingVersion：映射表版本信息。

        Raises:
            RuntimeError: 映射表不存在或加载失败。
        """
        try:
            record = self._repository.load_mapping(version=version)
        except VTSMappingRepositoryError as exc:
            if exc.reason_code == VTS_MAPPING_NOT_FOUND:
                raise RuntimeError(f"{VTS_MAPPING_LOAD_FAILED}: {exc.detail}") from exc
            raise
        return VTSMappingVersion(
            version=str(record["version"]),
            mapping=dict(record["mapping"]),
            content_hash=str(record["content_hash"]),
            created_ts_ms=int(record["created_ts_ms"]),
        )

    def get_expression_id(self, emotion_tag: str, *, version: str | None = None) -> str:
        """查询 emotion_tag 对应的 expression_id。

        Args:
            emotion_tag: 情绪标签（大小写不敏感）。
            version: 版本号。None 时使用最新版本。

        Returns:
            expression_id 字符串。

        Raises:
            ValueError: emotion_tag 非法（不在映射表中），reason_code=AVATAR_INVALID_EMOTION_TAG。
        """
        normalized = str(emotion_tag or "").strip().upper()
        if not normalized:
            raise ValueError(f"{AVATAR_INVALID_EMOTION_TAG}: emotion_tag 不能为空")

        try:
            mapping_version = self.load_mapping(version=version)
        except RuntimeError as exc:
            raise ValueError(f"{VTS_MAPPING_LOAD_FAILED}: {exc}") from exc

        expression_id = mapping_version.mapping.get(normalized)
        if not expression_id:
            raise ValueError(
                f"{AVATAR_INVALID_EMOTION_TAG}: emotion_tag '{normalized}' 不在映射表中 "
                f"(version={mapping_version.version}, "
                f"valid_tags={sorted(mapping_version.mapping.keys())})"
            )
        return expression_id

    def list_versions(self) -> list[dict[str, Any]]:
        """列出所有版本的变更日志。"""
        return self._repository.list_versions()

    def close(self) -> None:
        """关闭数据库连接。"""
        self._repository.close()

    # ------------------------------------------------------------------
    # M26-F：从 YAML 配置文件加载映射表（FR-VTS-CFG-001~002）
    # ------------------------------------------------------------------

    @classmethod
    def load_from_config_file(cls, path: str) -> dict[str, str]:
        """从 YAML 配置文件加载 emotion_tag → expression_id 映射表。

        读取 `configs/vts/vts_hotkey_config.yaml`（或指定路径）中的
        `emotion_mapping` 节点，返回映射字典。

        YAML 文件缺失时抛出 FileNotFoundError（含明确错误信息）。
        YAML 格式非法或 emotion_mapping 节点缺失时抛出 ValueError（含字段名）。
        未在 YAML 中配置的 emotion_tag 使用 DEFAULT_EMOTION_MAPPING 作为 fallback。

        Args:
            path: YAML 配置文件的绝对或相对路径。

        Returns:
            合并后的 emotion_tag → expression_id 映射字典
            （YAML 配置优先，DEFAULT_EMOTION_MAPPING 作为 fallback）。

        Raises:
            FileNotFoundError: 配置文件不存在。
            ValueError: 配置文件格式非法或 emotion_mapping 节点缺失/类型错误。
        """
        import os

        try:
            import yaml  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "load_from_config_file() 需要 PyYAML，请执行: pip install pyyaml"
            ) from exc

        # 文件存在性校验
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"VTS 热键配置文件不存在: {path!r}，"
                f"请参考 docs/30_devspec/vts_hotkey_config_guide.md 创建配置文件。"
            )

        # 解析 YAML
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ValueError(
                f"VTS 热键配置文件格式非法 ({path!r}): {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"VTS 热键配置文件根节点必须为 mapping 类型，实际为 {type(data).__name__!r} "
                f"(path={path!r})"
            )

        # 提取 emotion_mapping 节点
        emotion_mapping = data.get("emotion_mapping")
        if emotion_mapping is None:
            raise ValueError(
                f"VTS 热键配置文件缺少必要字段 'emotion_mapping' (path={path!r})，"
                f"请参考 docs/30_devspec/vts_hotkey_config_guide.md 补充该节点。"
            )
        if not isinstance(emotion_mapping, dict):
            raise ValueError(
                f"VTS 热键配置文件字段 'emotion_mapping' 类型错误：期望 dict，"
                f"实际为 {type(emotion_mapping).__name__!r} (path={path!r})"
            )

        # 合并：YAML 配置优先，DEFAULT_EMOTION_MAPPING 作为 fallback
        merged: dict[str, str] = dict(DEFAULT_EMOTION_MAPPING)
        for tag, expr_id in emotion_mapping.items():
            if not isinstance(tag, str) or not isinstance(expr_id, str):
                raise ValueError(
                    f"VTS 热键配置文件 'emotion_mapping' 中存在非字符串键值对: "
                    f"{tag!r} -> {expr_id!r} (path={path!r})"
                )
            merged[str(tag).strip().upper()] = str(expr_id).strip()

        logger.info(
            "VTSMappingStore: 从配置文件加载映射表成功，共 %d 条 (path=%r)",
            len(merged),
            path,
        )
        return merged


# ---------------------------------------------------------------------------
# M26-H：sub_emotion 细粒度表情热键映射（FR-EMOTION-DRIVE-005）
# ---------------------------------------------------------------------------

# sub_emotion 枚举 → VTS 热键 ID 映射（对应 configs/vts/vts_hotkey_config.yaml）
SUB_EMOTION_HOTKEY_MAP: dict[str, str] = {
    "blush":         "hotkey_exp_blush",          # 脸红（害羞/被夸）
    "star_eye":      "hotkey_exp_star_eye",        # 星星眼（崇拜/惊叹）
    "heart_eye":     "hotkey_exp_heart_eye",       # 爱心眼（喜爱/撒娇）
    "cry":           "hotkey_exp_cry",             # 哭哭（委屈/感动）
    "scissors_eye":  "hotkey_exp_scissors_eye",    # 剪刀眼（调皮/得意）
    "dizzy":         "hotkey_exp_dizzy",           # 眩晕（被整蒙/混乱）
    "disdain":       "hotkey_exp_disdain_dark",    # 鄙视脸黑（不屑/吐槽）
    "peace_sign":    "hotkey_exp_peace_sign",      # 比耶（开心打招呼）
}


def sub_emotion_to_hotkey(sub_emotion: str | None) -> str | None:
    """将 sub_emotion 枚举值映射到 VTS 热键 ID。

    Args:
        sub_emotion: 细粒度情感标签（blush/star_eye/heart_eye/cry/scissors_eye/dizzy/disdain/peace_sign）。
                     None 或未知值返回 None（不触发额外热键，不报错）。

    Returns:
        VTS 热键 ID 字符串，或 None（未知 sub_emotion 时）。
    """
    if not sub_emotion:
        return None
    normalized = str(sub_emotion).strip().lower()
    return SUB_EMOTION_HOTKEY_MAP.get(normalized)
