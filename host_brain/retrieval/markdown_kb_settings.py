"""可选 Markdown 知识库回退的类型化冷配置。

配置只描述是否启用及数据库发现范围；实际查询仍由只读存储适配器执行。未知环境
字段被忽略，防止部署杂项意外扩展本地知识库的读取权限。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarkdownKbSettings(BaseSettings):
    """将受支持环境变量收敛为稳定快照，供召回链一次性读取。"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=True, alias="SEMANTIC_RECALL_MARKDOWN_KB_ENABLED")
    db_paths: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_DBS")
    db_path: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_DB")
    kb_ids: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_IDS")

    def as_environment(self) -> dict[str, str]:
        """导出显式环境视图，避免下游直接依赖 Pydantic 内部表示。"""
        return {
            "SEMANTIC_RECALL_MARKDOWN_KB_ENABLED": "true" if self.enabled else "false",
            "SEMANTIC_RECALL_MARKDOWN_KB_DBS": str(self.db_paths or ""),
            "SEMANTIC_RECALL_MARKDOWN_KB_DB": str(self.db_path or ""),
            "SEMANTIC_RECALL_MARKDOWN_KB_IDS": str(self.kb_ids or ""),
        }


__all__ = ["MarkdownKbSettings"]
