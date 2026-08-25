from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PACKAGE = ROOT / "host_brain" / "contracts" / "planning_search"
PERSISTENCE_MODULES = (
    ROOT / "host_brain" / "persistence" / "code_benchmark_ledger_repository.py",
    ROOT / "host_brain" / "persistence" / "planning_search_calibration_repository.py",
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_codec.py",
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_models.py",
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_repository.py",
)
RESEARCH_LEDGER_REPOSITORY = (
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_repository.py"
)
RESEARCH_LEDGER_HELPERS = (
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_codec.py",
    ROOT / "host_brain" / "persistence" / "research_shadow_ledger_models.py",
)
CONTRACT_MODULES = (
    "benchmarking",
    "calibration",
    "code_benchmark_ledger",
    "code_benchmark_models",
    "code_benchmark_verification_contracts",
    "contracts",
    "research_benchmark_contracts",
    "research_domain_models",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _absolute_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_planning_search_repositories_depend_only_on_canonical_contracts() -> None:
    for path in PERSISTENCE_MODULES:
        source = path.read_text(encoding="utf-8-sig")
        imports = _absolute_imports(path)
        assert "host_brain.contracts.planning_search" in source
        assert not any(name.startswith("host_brain.planning_search") for name in imports)


def test_research_shadow_ledger_split_preserves_one_sqlite_uow_owner() -> None:
    repository = RESEARCH_LEDGER_REPOSITORY.read_text(encoding="utf-8-sig")

    assert len(repository.splitlines()) < 1_500
    assert repository.count("sqlite3.connect") == 1
    assert "def _connection(" in repository
    assert "def _transaction(" in repository

    for helper_path in RESEARCH_LEDGER_HELPERS:
        helper = helper_path.read_text(encoding="utf-8-sig")
        assert len(helper.splitlines()) < 800
        assert "sqlite3.connect" not in helper
        assert "def _connection(" not in helper
        assert "def _transaction(" not in helper
        assert "INSERT INTO " not in helper
        assert "UPDATE " not in helper
        assert "DELETE FROM " not in helper


def test_research_shadow_ledger_public_types_are_compatibility_reexports() -> None:
    from host_brain.persistence import research_shadow_ledger_models as models
    from host_brain.persistence import research_shadow_ledger_repository as repository

    for name in (
        "ResearchArmExecutionSnapshot",
        "ResearchArmOperationSnapshot",
        "ResearchArmOperationState",
        "ResearchPairedRunSpecSnapshot",
        "ResearchShadowLedgerError",
        "ResearchTerminalReceiptSnapshot",
    ):
        assert getattr(repository, name) is getattr(models, name)


def test_canonical_planning_search_contracts_do_not_reverse_import_runtime_layers() -> None:
    forbidden_prefixes = (
        "host_brain.app",
        "host_brain.composition",
        "host_brain.persistence",
        "host_brain.planning_search",
    )
    for path in sorted(CANONICAL_PACKAGE.glob("*.py")):
        imports = _absolute_imports(path)
        assert not any(
            name.startswith(forbidden_prefixes)
            for name in imports
        ), f"{path.relative_to(ROOT)} reverse-imports {sorted(imports)}"


def test_planning_search_compatibility_modules_are_removed() -> None:
    for module_name in CONTRACT_MODULES:
        assert not (ROOT / "host_brain" / "planning_search" / f"{module_name}.py").exists()


def test_canonical_search_budget_keeps_serialization_shape() -> None:
    from host_brain.contracts.planning_search.contracts import SearchBudget as CanonicalBudget

    canonical = CanonicalBudget(
        max_candidates=8,
        max_expansions=6,
        max_transitions=7,
        max_rollouts=4,
        max_depth=2,
        branching_factor=3,
        exploration=1.25,
        mcts_score_gap=0.2,
    )
    restored = CanonicalBudget(**canonical.to_dict())

    assert isinstance(restored, CanonicalBudget)
    assert restored == canonical
    assert restored.to_dict() == canonical.to_dict()
