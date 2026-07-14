"""Compatibility URL prefix backed by the governed scheduler service."""

from host_brain.scheduler.scheduler_routes import build_scheduler_router


router = build_scheduler_router(prefix="")


__all__ = ["router"]
