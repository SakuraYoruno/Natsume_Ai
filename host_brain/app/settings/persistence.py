"""Typed cold settings for the canonical Host persistence spine."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersistenceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    canonical_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        min_length=1,
        alias="NATSUME_AUDIT_DB",
        validation_alias=AliasChoices(
            "NATSUME_AUDIT_DB",
            "HOST_AUDIT_SQLITE_PATH",
            "HOST_AUDIT_DB_PATH",
            "AUDIT_DB_PATH",
        ),
    )
    world_canon_root: Path = Field(
        default=Path("host_brain/canon/world"),
        alias="Natsume_WORLD_CANON_ROOT",
    )


@lru_cache(maxsize=1)
def get_persistence_settings() -> PersistenceSettings:
    return PersistenceSettings()


__all__ = ["PersistenceSettings", "get_persistence_settings"]
