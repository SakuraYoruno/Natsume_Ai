from __future__ import annotations

from pathlib import Path

import host_brain.skills.install_repair as install_repair


def test_external_skill_auto_repair_never_spawns_or_reads_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle = tmp_path / "untrusted"
    bundle.mkdir()
    (bundle / "requirements.txt").write_text("malicious-package\n", encoding="utf-8")
    (bundle / "package.json").write_text("{}", encoding="utf-8")
    spawned: list[object] = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: spawned.append((_args, _kwargs)),
    )

    result = install_repair.repair_skill_loading_failure(
        skill_dir=bundle,
        exc="No module named 'attacker_chosen'",
    )

    assert result == {
        "success": False,
        "actions": [],
        "missing_modules": [],
        "requirements_used": False,
        "package_json_used": False,
        "reason_code": "SKILL_SUPPLY_CHAIN_AUTO_REPAIR_DISABLED",
    }
    assert spawned == []
