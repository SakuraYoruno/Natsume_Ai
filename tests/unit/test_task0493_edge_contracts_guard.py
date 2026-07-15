from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.architecture import edge_contracts_guard as guard
from tools.static_scan import run_all
from host_brain.executive_runtime.skill_execution import _flatten_result_text


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_preexisting_host_syntax_debt_is_repaired_for_complete_reverse_scan() -> None:
    assert _flatten_result_text(["first", {"result": "second"}, None]) == (
        "first\nsecond"
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in (
        "edge_client/__init__.py",
        "host_brain/__init__.py",
        "natsume_contracts/__init__.py",
    ):
        _write(root, relative, "")
    return root


def _edge(
    edge_id: str,
    direction: str,
    source_path: str,
    target_module: str,
) -> dict[str, object]:
    return {
        "edge_id": edge_id,
        "direction": direction,
        "source_path": source_path,
        "target_module": target_module,
        "owner": "FIXTURE_OWNER",
        "reason": "fixture legacy dependency",
        "migration_target": "fixture contracts port",
        "remove_by": "TASK-0581",
        "priority": "P1",
    }


def _freeze(
    root: Path,
    *,
    edges: list[dict[str, object]] | None = None,
) -> Path:
    policy_dir = root / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    migration = {
        "schema_version": guard.SCHEMA_VERSION,
        "policy_id": guard.POLICY_ID,
        "edges": list(edges or []),
    }
    migration_path = policy_dir / "migration.json"
    migration_path.write_text(
        json.dumps(migration, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    inventory = guard.inventory_report(root)
    assert inventory["scan_violations"] == []
    baseline = {
        "schema_version": guard.SCHEMA_VERSION,
        "policy_id": guard.POLICY_ID,
        "baseline_commit": "0" * 40,
        "expected_fingerprints": inventory["fingerprints"],
        "fingerprints_sha256": inventory["fingerprints_sha256"],
        "migration_manifest": "migration.json",
        "migration_sha256": guard.content_digest(migration),
    }
    baseline_path = policy_dir / "baseline.json"
    baseline_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return baseline_path


def test_modes_default_freeze_report_and_enforce_have_distinct_authority(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _write(root, "edge_client/legacy.py", "from host_brain.core.errors import LEGACY\n")
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "E2H-FIXTURE",
                "EDGE_TO_HOST",
                "edge_client/legacy.py",
                "host_brain.core.errors",
            )
        ],
    )

    assert guard.load_guard_mode({}).mode is guard.GuardMode.FREEZE_NEW
    assert guard.scan_report(root, baseline_path=baseline)["passed"] is True

    enforce = guard.scan_report(
        root,
        baseline_path=baseline,
        mode=guard.GuardMode.ENFORCE,
    )
    assert enforce["passed"] is False
    assert {row["reason_code"] for row in enforce["blocking_violations"]} == {
        guard.REASON_ENFORCE_IMPORT
    }

    _write(root, "edge_client/new_debt.py", "from host_brain.app.main import app\n")
    report = guard.scan_report(
        root,
        baseline_path=baseline,
        mode=guard.GuardMode.REPORT,
    )
    assert report["passed"] is True
    assert guard.REASON_NEW_IMPORT in {
        row["reason_code"] for row in report["violations"]
    }


def test_freeze_rejects_new_and_stale_fingerprints_but_ignores_line_moves(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    legacy = _write(
        root,
        "edge_client/legacy.py",
        "from host_brain.core.errors import LEGACY\n",
    )
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "E2H-FIXTURE",
                "EDGE_TO_HOST",
                "edge_client/legacy.py",
                "host_brain.core.errors",
            )
        ],
    )
    original_digest = guard.inventory_report(root)["fingerprints_sha256"]

    legacy.write_text(
        "\n\nfrom host_brain.core.errors import LEGACY\n",
        encoding="utf-8",
    )
    moved = guard.scan_report(root, baseline_path=baseline)
    assert moved["passed"] is True
    assert moved["current_fingerprints_sha256"] == original_digest

    _write(
        root,
        "edge_client/new_debt.py",
        "def load():\n    from host_brain.core.ids import now_ms\n    return now_ms\n",
    )
    added = guard.scan_report(root, baseline_path=baseline)
    assert added["passed"] is False
    assert guard.REASON_NEW_IMPORT in {
        row["reason_code"] for row in added["blocking_violations"]
    }

    legacy.unlink()
    stale = guard.scan_report(root, baseline_path=baseline)
    assert guard.REASON_STALE_BASELINE in {
        row["reason_code"] for row in stale["blocking_violations"]
    }


@pytest.mark.parametrize(
    "source",
    [
        "import host_brain.core.ids as ids\n",
        (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from host_brain.core.ids import now_ms\n"
        ),
        (
            "try:\n"
            "    from host_brain.core.ids import now_ms\n"
            "except ImportError:\n"
            "    now_ms = None\n"
        ),
    ],
)
def test_all_static_import_forms_are_guarded(tmp_path: Path, source: str) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "edge_client/new_static.py", source)

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert guard.REASON_NEW_IMPORT in {
        row["reason_code"] for row in report["blocking_violations"]
    }


def test_duplicate_import_occurrence_cannot_hide_behind_an_existing_fingerprint(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    source = _write(
        root,
        "edge_client/legacy.py",
        "from host_brain.core.ids import now_ms\n",
    )
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "E2H-FIXTURE",
                "EDGE_TO_HOST",
                "edge_client/legacy.py",
                "host_brain.core.ids",
            )
        ],
    )
    source.write_text(
        "from host_brain.core.ids import now_ms\n"
        "from host_brain.core.ids import now_ms\n",
        encoding="utf-8",
    )

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert any(row["occurrence"] == 2 for row in report["new_findings"])


@pytest.mark.parametrize(
    "source",
    [
        (
            "import importlib as il\n"
            "loader = il.import_module\n"
            "def load():\n"
            "    return loader('host_' + 'brain.core.ids')\n"
        ),
        (
            "import builtins as b\n"
            "def load():\n"
            "    return b.__import__('host_brain.core.ids')\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return getattr(importlib, 'import_module')('.core.ids', package='host_brain')\n"
        ),
        (
            "import builtins\n"
            "loader = getattr(builtins, '__import__')\n"
            "def load():\n"
            "    return loader('host_brain.core.ids')\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return importlib.import_module(name='host_brain.core.ids')\n"
        ),
        (
            "import importlib\n"
            "lib = importlib\n"
            "def load():\n"
            "    return lib.import_module(name='host_brain.core.ids')\n"
        ),
        "def load():\n    return __import__(name='host_brain.core.ids')\n",
        (
            "import importlib\n"
            "def load():\n"
            "    return importlib.__dict__['import_module']('host_brain.core.ids')\n"
        ),
        "from importlib import *\ndef load():\n    return import_module('host_brain.core.ids')\n",
        "import runpy\ndef load():\n    return runpy.run_module('host_brain.core.ids')\n",
        (
            "import importlib.util\n"
            "def load():\n"
            "    spec = importlib.util.find_spec('host_brain.core.ids')\n"
            "    module = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(module)\n"
            "    return module\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return getattr(importlib.util, 'find_spec')('host_brain.core.ids')\n"
        ),
        (
            "import runpy\n"
            "def load():\n"
            "    return getattr(runpy, 'run_module')('host_brain.core.ids')\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return importlib.util.__dict__['find_spec']('host_brain.core.ids')\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return importlib.__import__('host_brain.core.ids')\n"
        ),
        (
            "import pydoc\n"
            "def load():\n"
            "    return pydoc.safeimport('host_brain.core.ids')\n"
        ),
        (
            "import pkgutil\n"
            "def load():\n"
            "    return pkgutil.get_loader('host_brain.core.ids').load_module('host_brain.core.ids')\n"
        ),
        "def load():\n    return [__import__][0]('host_brain.core.ids')\n",
        (
            "import importlib\n"
            "dictionary = importlib.__dict__\n"
            "def load():\n"
            "    return dictionary['import_module']('host_brain.core.ids')\n"
        ),
    ],
)
def test_constant_dynamic_import_aliases_are_detected(
    tmp_path: Path,
    source: str,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "edge_client/dynamic.py", source)

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert any(row["kind"] == "dynamic" for row in report["new_findings"])
    assert guard.REASON_NEW_IMPORT in {
        row["reason_code"] for row in report["blocking_violations"]
    }


@pytest.mark.parametrize(
    "source",
    [
        "def load(name):\n    return __import__(name)\n",
        "def load(source):\n    exec(source)\n",
        "def load(source):\n    return eval(source)\n",
        "def load(source):\n    return compile(source, '<edge>', 'exec')\n",
        (
            "def load(name):\n"
            "    import importlib as il\n"
            "    loader = il.import_module\n"
            "    return loader(name)\n"
        ),
        "runner = exec\ndef load(source):\n    runner(source)\n",
        "def load(source):\n    runner(source)\nrunner = exec\n",
        (
            "import importlib\n"
            "def load(name, attribute):\n"
            "    return getattr(importlib, attribute)(name)\n"
        ),
        (
            "import builtins\n"
            "def load(source, attribute):\n"
            "    return getattr(builtins, attribute)(source)\n"
        ),
        (
            "import importlib\n"
            "def load():\n"
            "    return list(map(importlib.import_module, ['host_brain.core.ids']))\n"
        ),
        "runner, = (exec,)\ndef load(source):\n    runner(source)\n",
        (
            "import importlib\n"
            "def load(name, loader=importlib.import_module):\n"
            "    return loader(name)\n"
        ),
        (
            "import builtins\n"
            "def load(source):\n"
            "    return builtins.__dict__['exec'](source)\n"
        ),
        "def load(source):\n    return [exec][0](source)\n",
        "class X:\n    run = exec\ndef load(source):\n    X.run(source)\n",
        (
            "import importlib\n"
            "def factory():\n"
            "    return importlib.import_module\n"
            "def load():\n"
            "    return factory()('host_brain.core.ids')\n"
        ),
        (
            "import zipimport\n"
            "def load():\n"
            "    return zipimport.zipimporter('mods.zip').load_module('host_brain.core.ids')\n"
        ),
    ],
)
def test_unresolved_dynamic_loading_is_a_terminal_scan_failure(
    tmp_path: Path,
    source: str,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "edge_client/dynamic.py", source)

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert guard.REASON_DYNAMIC_UNRESOLVED in {
        row["reason_code"] for row in report["blocking_violations"]
    }


def test_report_mode_cannot_downgrade_incomplete_contracts_dynamic_scan(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(
        root,
        "natsume_contracts/dynamic.py",
        "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
    )

    report = guard.scan_report(
        root,
        baseline_path=baseline,
        mode=guard.GuardMode.REPORT,
    )

    assert report["passed"] is False
    assert guard.REASON_DYNAMIC_UNRESOLVED in {
        row["reason_code"] for row in report["blocking_violations"]
    }


def test_host_dynamic_risk_is_content_addressed_and_must_be_migrated(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    source = _write(
        root,
        "host_brain/dynamic.py",
        "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
    )
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "H2E-DYNAMIC",
                "HOST_TO_EDGE",
                "host_brain/dynamic.py",
                "edge_client.__dynamic_risk__",
            )
        ],
    )
    initial = guard.scan_report(root, baseline_path=baseline)
    assert initial["passed"] is True
    assert initial["findings"][0]["kind"] == "dynamic_risk"
    assert initial["findings"][0]["binding"].startswith("sha256:")

    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = guard.scan_report(root, baseline_path=baseline)

    assert changed["passed"] is False
    assert {row["reason_code"] for row in changed["blocking_violations"]} >= {
        guard.REASON_NEW_IMPORT,
        guard.REASON_STALE_BASELINE,
    }


def test_host_importlib_util_pipeline_is_frozen_as_dynamic_edge_debt(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(
        root,
        "host_brain/dynamic.py",
        "import importlib.util\n"
        "spec = importlib.util.find_spec('edge_client.rpc.health')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n",
    )

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert {row["kind"] for row in report["new_findings"]} == {
        "dynamic",
        "dynamic_risk",
    }
    assert all(
        row["direction"] == "HOST_TO_EDGE" for row in report["new_findings"]
    )


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nDB = Path('host_brain/data/audit.sqlite3')\n",
        "from pathlib import Path\nROOT = Path('.')\nDB = ROOT / ('host_' + 'brain')\n",
        "from pathlib import Path\nHOST_DIR = 'host_brain'\nDB = Path('.') / HOST_DIR\n",
        "from pathlib import Path\nDB = Path(f\"{'host'}_brain/data/audit.sqlite3\")\n",
    ],
)
def test_new_host_filesystem_coupling_is_frozen_like_an_import(
    tmp_path: Path,
    source: str,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "edge_client/path_coupling.py", source)

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert any(row["kind"] == "path" for row in report["new_findings"])


def test_contracts_are_pure_and_host_composition_is_the_only_reverse_exception(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _write(
        root,
        "host_brain/composition/wire_edge.py",
        "from edge_client.rpc import health\n",
    )
    baseline = _freeze(root)
    assert guard.scan_report(root, baseline_path=baseline)["passed"] is True

    _write(
        root,
        "natsume_contracts/bad_contract.py",
        "from host_brain.core.ids import now_ms\n",
    )
    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert report["new_findings"][0]["direction"] == "CONTRACTS_TO_HOST"


@pytest.mark.parametrize(
    ("module", "direction"),
    [
        ("fastapi", "CONTRACTS_TO_FRAMEWORK"),
        ("sqlite3", "CONTRACTS_TO_STORAGE"),
        ("openai", "CONTRACTS_TO_PROVIDER"),
        ("httpx", "CONTRACTS_TO_NETWORK"),
        ("langchain_google_genai", "CONTRACTS_TO_EXTERNAL"),
        ("langchain_openai", "CONTRACTS_TO_EXTERNAL"),
    ],
)
def test_contracts_reject_framework_storage_provider_and_network_implementations(
    tmp_path: Path,
    module: str,
    direction: str,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "natsume_contracts/bad.py", f"import {module}\n")

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert report["new_findings"][0]["direction"] == direction


def test_contracts_external_allowlist_cannot_be_bypassed_by_callable_container(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(
        root,
        "natsume_contracts/bad.py",
        "provider = [__import__][0]('langchain_google_genai')\n",
    )

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    assert any(
        row["direction"] == "CONTRACTS_TO_EXTERNAL"
        for row in report["new_findings"]
    )


def test_scope_type_and_control_context_are_part_of_the_fingerprint(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    source = _write(
        root,
        "edge_client/legacy.py",
        "def Holder():\n    from host_brain.core.ids import now_ms\n",
    )
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "E2H-SCOPE",
                "EDGE_TO_HOST",
                "edge_client/legacy.py",
                "host_brain.core.ids",
            )
        ],
    )

    source.write_text(
        "class Holder:\n    from host_brain.core.ids import now_ms\n",
        encoding="utf-8",
    )
    changed_scope = guard.scan_report(root, baseline_path=baseline)
    assert changed_scope["passed"] is False
    assert changed_scope["new_findings"][0]["scope"] == "class:Holder"

    source.write_text(
        "def Holder():\n"
        "    if True:\n"
        "        from host_brain.core.ids import now_ms\n",
        encoding="utf-8",
    )
    changed_context = guard.scan_report(root, baseline_path=baseline)
    assert changed_context["passed"] is False
    assert changed_context["new_findings"][0]["context"].startswith("if:sha256:")


def test_if_predicate_change_cannot_activate_frozen_debt(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = _write(
        root,
        "edge_client/legacy.py",
        "if False:\n    from host_brain.core.ids import now_ms\n",
    )
    baseline = _freeze(
        root,
        edges=[
            _edge(
                "E2H-PREDICATE",
                "EDGE_TO_HOST",
                "edge_client/legacy.py",
                "host_brain.core.ids",
            )
        ],
    )
    before = guard.scan_report(root, baseline_path=baseline)

    source.write_text(
        "if True:\n    from host_brain.core.ids import now_ms\n",
        encoding="utf-8",
    )
    after = guard.scan_report(root, baseline_path=baseline)

    assert before["passed"] is True
    assert after["passed"] is False
    assert after["new_findings"][0]["context"] != before["findings"][0]["context"]


def test_content_digest_tamper_and_stale_migration_fail_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["fingerprints_sha256"] = "sha256:" + "f" * 64
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    tampered = guard.scan_report(root, baseline_path=baseline)
    assert tampered["passed"] is False
    assert guard.REASON_BASELINE_INVALID in {
        row["reason_code"] for row in tampered["blocking_violations"]
    }

    stale_root = _repo(tmp_path / "stale")
    stale_baseline = _freeze(
        stale_root,
        edges=[
            _edge(
                "STALE-EDGE",
                "EDGE_TO_HOST",
                "edge_client/removed.py",
                "host_brain.core.errors",
            )
        ],
    )
    stale = guard.scan_report(stale_root, baseline_path=stale_baseline)
    assert guard.REASON_STALE_MIGRATION in {
        row["reason_code"] for row in stale["blocking_violations"]
    }


def test_migration_digest_tamper_has_a_distinct_configuration_reason(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["migration_sha256"] = "sha256:" + "f" * 64
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    report = guard.scan_report(root, baseline_path=baseline)

    assert report["passed"] is False
    reasons = {row["reason_code"] for row in report["blocking_violations"]}
    assert guard.REASON_MIGRATION_INVALID in reasons
    assert guard.REASON_BASELINE_INVALID not in reasons


def test_delivery_adapter_cannot_be_downgraded_by_report_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    baseline = _freeze(root)
    _write(root, "edge_client/new_debt.py", "import host_brain.app.main\n")
    monkeypatch.setattr(guard, "DEFAULT_BASELINE", baseline)
    monkeypatch.setenv(guard.MODE_ENV, guard.GuardMode.REPORT.value)

    violations = guard.scan_repo(root)

    assert guard.REASON_NEW_IMPORT in {row["reason_code"] for row in violations}


def test_static_scan_aggregator_maps_edge_guard_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmptyScanner:
        @staticmethod
        def scan_repo(_root: Path, **_kwargs: object) -> list[dict[str, object]]:
            return []

    class FailingGuard:
        @staticmethod
        def scan_repo(_root: Path) -> list[dict[str, object]]:
            return [
                {
                    "rule_id": guard.POLICY_ID,
                    "reason_code": guard.REASON_NEW_IMPORT,
                    "path": "edge_client/new_debt.py",
                    "line": 7,
                    "message": "new Edge to Host implementation import",
                }
            ]

    monkeypatch.setattr(run_all, "_load_bond_scan_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_no_rule_degrade_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_nle_boundary_scan_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_edge_contracts_guard_module", lambda: FailingGuard())

    report = run_all.run_static_scans(tmp_path)

    assert report["status"] == "failed"
    assert report["failures"][0]["failed_artifact"] == "edge_client/new_debt.py"
    assert report["failures"][0]["rule_id"] == guard.POLICY_ID
