from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "doc_lint" / "doc_authority_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("doc_authority_gate", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load authority gate: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


authority = _load_module()


def _write(path: Path, content: str = "placeholder\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _minimal_repo(root: Path) -> None:
    for relative in authority.REQUIRED_AUTHORITY_PATHS:
        if relative.endswith("current_architecture_status.json"):
            _write(
                root / relative,
                json.dumps(
                    {
                        "overall_status": "INCOMPLETE",
                        "active_wave": "NE-00",
                        "active_wave_status": "IN_PROGRESS",
                        "historical_receipts_are_scope_bound": True,
                        "active_execution_contract": (
                            "docs/30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md"
                        ),
                    }
                ),
            )
        elif relative.endswith("NEXT_GENERATION_EVOLUTION_EXECUTION.md"):
            _write(root / relative, "状态：NE-00_IN_PROGRESS / PROGRAM_INCOMPLETE\n")
        else:
            _write(root / relative)
    for basename in authority.LEGACY_DOCUMENTS:
        _write(root / authority.ARCHIVE_ROOT / basename)


def test_repository_document_authority_passes() -> None:
    assert authority.scan_repository(ROOT) == []


def test_legacy_document_outside_history_fails(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    basename = authority.LEGACY_DOCUMENTS[0]
    _write(tmp_path / "docs" / "30_devspec" / basename)

    failures = authority.scan_repository(tmp_path)

    assert any(row["reason_code"] == "DOC_LEGACY_OUTSIDE_HISTORY" for row in failures)


def test_unqualified_legacy_reference_fails(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    basename = authority.LEGACY_DOCUMENTS[1]
    _write(tmp_path / "docs" / "20_prd" / "bad.md", f"see docs/30_devspec/{basename}\n")

    failures = authority.scan_repository(tmp_path)

    assert any(row["reason_code"] == "DOC_LEGACY_REFERENCE_ACTIVE" for row in failures)


def test_legacy_stage_heading_cannot_reenter_current_plan(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    _write(tmp_path / "docs" / "30_devspec" / "DevSpec.md", "### M49 current stage\n")

    failures = authority.scan_repository(tmp_path)

    assert any(row["reason_code"] == "DOC_LEGACY_STAGE_ACTIVE" for row in failures)


def test_machine_wave_status_must_match_execution_contract(tmp_path: Path) -> None:
    _minimal_repo(tmp_path)
    status_path = tmp_path / "configs" / "release" / "current_architecture_status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    payload["active_wave_status"] = "COMPLETED"
    status_path.write_text(json.dumps(payload), encoding="utf-8")

    failures = authority.scan_repository(tmp_path)

    assert any(
        row["reason_code"] == "DOC_MACHINE_STATUS_INVALID"
        and "NE-00_COMPLETED" in row["message"]
        for row in failures
    )
