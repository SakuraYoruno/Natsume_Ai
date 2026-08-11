from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REPOSITORY_IMPORT_BOUNDARIES = {
    "host_brain/persistence/evolution_governance_ledger.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_feedback.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_knowledge.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_identity.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_decision.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_lab_ledger_repository.py": (
        "host_brain.evolution_lab",
        "host_brain.contracts.evolution_lab",
    ),
    "host_brain/persistence/evolution_lab_v2_repository.py": (
        "host_brain.evolution_lab",
        "host_brain.contracts.evolution_lab",
    ),
    "host_brain/persistence/skill_lifecycle_v2_repository.py": (
        "host_brain.skills.skill_lifecycle_v2",
        "host_brain.contracts.skill_lifecycle_v2",
    ),
    "host_brain/persistence/world_observation_journal.py": (
        "host_brain.runtime.context_contracts",
        "host_brain.contracts.runtime_context",
    ),
}

CONTRACT_MODULES = (
    "host_brain/contracts/evolution.py",
    "host_brain/contracts/evolution_lab.py",
    "host_brain/contracts/skill_lifecycle.py",
    "host_brain/contracts/skill_lifecycle_v2.py",
    "host_brain/contracts/runtime_context.py",
    "host_brain/contracts/semantic_recall.py",
)

COMPATIBILITY_MODULES = {
    "host_brain/evolution/contracts.py": {
        "DecisionRecordV1",
        "EvolutionTransitionReceiptV1",
        "FeedbackProposalV1",
        "IdentityMigrationPlanV2",
        "IdentityMigrationPlanV1",
        "KnowledgeEntryV2",
        "KnowledgeEntryV1",
        "KnowledgeEvolutionContractError",
        "VersionActivationDecisionV1",
    },
    "host_brain/evolution_lab/contracts.py": {
        "CounterfactualPairV1",
        "CounterfactualPairV2",
        "DatasetManifestV1",
        "EffectAuditReceiptV1",
        "EvolutionLabContractError",
        "EvolutionProposalV1",
        "EvolutionProposalV2",
        "ExperimentReportV1",
        "ExperimentReportV2",
        "ExperimentRunReceiptV1",
        "ExperimentRunReceiptV2",
        "ExperimentSpecV1",
        "ExperimentSpecV2",
        "LabDatasetV1",
        "PolicyVariantV1",
        "RedactionReceiptV1",
        "SandboxReceiptV1",
        "VerificationReceiptV1",
        "canonical_evolution_lab_digest",
    },
    "host_brain/runtime/context_contracts.py": {
        "ContextContractError",
        "ContextConstraintV1",
        "ContextPackV2",
        "IntentArbitrationV1",
        "IntentFrameV1",
        "SceneDecisionV2",
        "SceneResolutionInputV1",
        "SceneTransitionV1",
        "WorldFactExpiryV1",
        "WorldFactV2",
        "WorldObservationBatchV1",
        "WorldObservationHeadV1",
        "WorldSnapshotV2",
    },
}

IDENTITY_PAIRS = (
    ("host_brain.contracts.evolution", "host_brain.evolution"),
    ("host_brain.contracts.evolution_lab", "host_brain.evolution_lab"),
    ("host_brain.contracts.runtime_context", "host_brain.runtime.context_contracts"),
    ("host_brain.contracts.semantic_recall", "host_brain.app.recall.semantic_recall"),
)


def _tree(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _absolute_imports(tree: ast.Module) -> set[str]:
    modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def test_persistence_repositories_depend_on_neutral_contracts() -> None:
    for relative_path, (forbidden_prefix, required_module) in (
        REPOSITORY_IMPORT_BOUNDARIES.items()
    ):
        imports = _absolute_imports(_tree(relative_path))
        assert required_module in imports
        assert not any(
            module == forbidden_prefix or module.startswith(f"{forbidden_prefix}.")
            for module in imports
        )


def test_neutral_contract_modules_have_no_upper_layer_dependency() -> None:
    for relative_path in CONTRACT_MODULES:
        imports = _absolute_imports(_tree(relative_path))
        assert not any(module.startswith("host_brain.") for module in imports)


def test_compatibility_modules_do_not_redefine_contract_types() -> None:
    for relative_path, migrated_names in COMPATIBILITY_MODULES.items():
        tree = _tree(relative_path)
        local_definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert local_definitions.isdisjoint(migrated_names)

def test_legacy_and_neutral_contract_exports_have_identical_objects() -> None:
    for canonical_name, compatibility_name in IDENTITY_PAIRS:
        canonical = importlib.import_module(canonical_name)
        compatibility = importlib.import_module(compatibility_name)
        for export_name in canonical.__all__:
            assert getattr(compatibility, export_name) is getattr(
                canonical, export_name
            )
