"""Compatibility facade for the persistence-owned Task Runtime repository."""

from __future__ import annotations

from host_brain.persistence import task_repository as _repository

__all__ = [name for name in dir(_repository) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_repository, name)


def __dir__() -> list[str]:
    return sorted(set(globals()).union(__all__))
