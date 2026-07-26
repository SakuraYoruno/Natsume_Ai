"""Negative regression for the removed SkillLoader executor dispatch."""

from __future__ import annotations

import inspect

from host_brain.core.errors import GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
from host_brain.skills import loader as loader_module


def test_skill_loader_exposes_no_executor_or_authority_binding() -> None:
    source = inspect.getsource(loader_module)

    assert "dispatch_executor_payload" not in source
    assert "bind_validated_skill_authority" not in source
    assert "meta.handler" not in source
    assert "_dispatch_executor" not in source
    assert GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED in source
