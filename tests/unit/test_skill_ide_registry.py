from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills.ide_registry import IDESkillProvider
from host_brain.skills.registry import SkillRegistry


def test_ide_provider_registration_contains_only_ide_skills() -> None:
    registry = SkillRegistry()
    registry.register_decorated(IDESkillProvider())
    assert set(registry.list_skill_names()) == {
        "open_cursor",
        "run_pycharm_action",
        "open_file_in_editor",
    }


def test_ide_skill_handlers_return_normalized_payload() -> None:
    provider = IDESkillProvider()
    registry = SkillRegistry()
    registry.register_decorated(provider)

    open_cursor = registry.require("open_cursor").handler({"path": "D:/repo"}, {"request_id": "req-1"})
    run_action = registry.require("run_pycharm_action").handler({"action_id": "ReformatCode"}, {"request_id": "req-2"})
    open_file = registry.require("open_file_in_editor").handler({"path": "D:/repo/a.py"}, {"request_id": "req-3"})

    assert open_cursor["tool"] == "cursor"
    assert open_cursor["path"] == "D:/repo"
    assert run_action["tool"] == "pycharm"
    assert run_action["action"] == "ReformatCode"
    assert open_file["tool"] == "open_file_in_editor"
    assert open_file["path"] == "D:/repo/a.py"
    assert "action" not in open_file
