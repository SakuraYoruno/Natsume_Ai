# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None else default


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _as_data_dir(default_data_dir: str) -> Path:
    """
    数据目录优先级：
    1) NATSUME_DATA_DIR 环境变量
    2) default_data_dir（通常是 ./data）
    """
    data_dir = Path(_env("NATSUME_DATA_DIR", default_data_dir)).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _normalize_dify_url(url: str) -> str:
    """
    允许传 base url 或完整 endpoint：
    - 传 http://127.0.0.1 -> 自动拼 /v1/chat-messages
    - 传 http://127.0.0.1/v1/chat-messages -> 原样使用
    """
    u = (url or "").strip()
    if not u:
        u = "http://127.0.0.1/v1/chat-messages"
    if "/v1/" not in u:
        u = u.rstrip("/") + "/v1/chat-messages"
    return u


@dataclass(frozen=True)
class Settings:
    """
    Host_Brain V2 运行时配置：
    - 环境变量优先（方便不同机器/分支切换）
    - 其次使用当前“可用配置”的本地默认值兜底
    """

    # Dify Chatflow（Ego）
    DIFY_API_URL: str
    DIFY_API_KEY: str
    DIFY_USER_ID: str

    # 存储
    SQLITE_PATH: str
    CHROMA_DIR: str
    CHROMA_EPISODIC_COLLECTION: str
    CHROMA_CANON_COLLECTION: str

    # Neo4j（Bond SSOT）
    NEO4J_URI: str
    NEO4J_USER: str
    NEO4J_PASSWORD: str
    NEO4J_BOND_SUMMARY_QUERY: str
    NEO4J_CONNECT_TIMEOUT_S: int

    # 召回参数
    WORLD_CANON_TOP_K: int
    EPISODIC_TOP_K: int
    RECENT_DIALOGUE_TURNS: int
    HARD_STATE_KEYS_LIMIT: int

    # context_pack 裁剪
    CONTEXT_PACK_MAX_CHARS: int

    @staticmethod
    def from_env() -> "Settings":

        base_dir = Path(__file__).resolve().parent
        data_dir = _as_data_dir(str(base_dir / "data"))

        default_sqlite_path = str(data_dir / "natsume_core.db")
        default_chroma_dir = str(data_dir / "chroma_db")

        default_dify_url = _normalize_dify_url(
            _env("DIFY_API_URL", _env("DIFY_BASE_URL", "http://127.0.0.1/v1/chat-messages"))
        )

        # key
        default_dify_key = _env("DIFY_API_KEY", "app-6S2HL8QazAPc5u8GrqTw7CnY")

        # collection 基名
        collection_base = _env("COLLECTION_NAME", "natsume_persona_core").strip() or "natsume_persona_core"
        default_epi_col = f"{collection_base}__episodic"
        default_canon_col = f"{collection_base}__canon"

        # Neo4j
        default_neo4j_uri = _env("NEO4J_URI", "bolt://localhost:7687")
        default_neo4j_user = _env("NEO4J_USER", "neo4j")
        default_neo4j_pwd = _env("NEO4J_PASSWORD", "natsume2026")

        # -------------------------
        # 实际生效配置（env 优先）
        # -------------------------
        sqlite_path = _env("SQLITE_PATH", default_sqlite_path)
        chroma_dir = _env("CHROMA_DIR", default_chroma_dir)

        return Settings(
            # Dify（Ego）
            DIFY_API_URL=default_dify_url,
            DIFY_API_KEY=default_dify_key,
            DIFY_USER_ID=_env("DIFY_USER_ID", "host_brain_v2"),

            # 存储
            SQLITE_PATH=sqlite_path,
            CHROMA_DIR=chroma_dir,
            CHROMA_EPISODIC_COLLECTION=_env("CHROMA_EPISODIC_COLLECTION", default_epi_col),
            CHROMA_CANON_COLLECTION=_env("CHROMA_CANON_COLLECTION", default_canon_col),

            # Neo4j
            NEO4J_URI=default_neo4j_uri,
            NEO4J_USER=default_neo4j_user,
            NEO4J_PASSWORD=default_neo4j_pwd,
            # 可选：你也可以在 env 里覆盖成你自己的查询
            NEO4J_BOND_SUMMARY_QUERY=_env("NEO4J_BOND_SUMMARY_QUERY", ""),
            NEO4J_CONNECT_TIMEOUT_S=_env_int("NEO4J_CONNECT_TIMEOUT_S", 3),

            # 召回参数（先给一个稳妥默认）
            WORLD_CANON_TOP_K=_env_int("WORLD_CANON_TOP_K", 6),
            EPISODIC_TOP_K=_env_int("EPISODIC_TOP_K", 6),
            RECENT_DIALOGUE_TURNS=_env_int("RECENT_DIALOGUE_TURNS", 6),
            HARD_STATE_KEYS_LIMIT=_env_int("HARD_STATE_KEYS_LIMIT", 200),

            # context_pack 裁剪
            CONTEXT_PACK_MAX_CHARS=_env_int("CONTEXT_PACK_MAX_CHARS", 14000),
        )
