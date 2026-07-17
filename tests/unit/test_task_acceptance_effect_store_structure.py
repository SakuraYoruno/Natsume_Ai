from __future__ import annotations

import ast
import inspect

from host_brain.persistence import (
    task_effect_reconciliation_common,
    task_record_codec,
    task_repository,
    task_terminal_reconciliation_codec_validation,
    task_terminal_reconciliation_common,
    task_terminal_reconciliation_decision_authority,
    task_terminal_reconciliation_projection,
    task_terminal_reconciliation_schema,
)
from host_brain.persistence.task_acceptance_effect_delivery import (
    TaskAcceptanceEffectDeliveryMixin,
)
from host_brain.persistence.task_acceptance_effect_staging import (
    TaskAcceptanceEffectStagingMixin,
)
from host_brain.persistence.task_graph_cancellation_continuation_store import (
    TaskGraphCancellationContinuationStoreMixin,
)
from host_brain.persistence.task_outbox_delivery_store import (
    TaskOutboxDeliveryStoreMixin,
)
from host_brain.persistence.task_parallel_success_store import (
    TaskParallelSuccessStoreMixin,
)
from host_brain.persistence.task_parallel_settlement_store import (
    TaskParallelSettlementStoreMixin,
)
from host_brain.persistence.task_graph_cancellation_request import (
    TaskGraphCancellationRequestMixin,
)
from host_brain.persistence.task_graph_cancellation_finalization import (
    TaskGraphCancellationFinalizationMixin,
)
from host_brain.persistence.task_retry_mutation_store import (
    TaskRetryMutationStoreMixin,
)
from host_brain.persistence.task_retry_replay_store import (
    TaskRetryReplayStoreMixin,
)
from host_brain.persistence.task_atomic_failure_store import (
    TaskAtomicFailureStoreMixin,
)
from host_brain.persistence.task_operation_reconciliation_store import (
    TaskOperationReconciliationStoreMixin,
)
from host_brain.persistence.task_operation_checkpoint_store import (
    TaskOperationCheckpointStoreMixin,
)
from host_brain.persistence.task_effect_reconciliation_store import (
    TaskEffectReconciliationStoreMixin,
    build_effect_reconciliation_attestation_binding,
)
from host_brain.persistence.task_effect_reconciliation_mutation_store import (
    TaskEffectReconciliationMutationMixin,
)
from host_brain.persistence.task_effect_reconciliation_read_store import (
    TaskEffectReconciliationReadMixin,
)
from host_brain.persistence.task_graph_cancellation_effect_overlay_store import (
    TaskGraphCancellationEffectOverlayMixin,
)
from host_brain.persistence.task_terminal_reconciliation_store import (
    TaskTerminalReconciliationStoreMixin,
)
from host_brain.persistence.task_terminal_reconciliation_mutation_store import (
    TaskTerminalReconciliationMutationMixin,
)
from host_brain.persistence.task_terminal_reconciliation_read_store import (
    TaskTerminalReconciliationReadMixin,
)
from host_brain.persistence.task_budget_usage_store import (
    TaskBudgetUsageStoreMixin,
)
from host_brain.persistence.task_node_artifact_store import (
    TaskNodeArtifactStoreMixin,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.task_dag_state_store import (
    TaskDagStateStoreMixin,
)
from host_brain.persistence.task_ready_claim_store import (
    TaskReadyClaimStoreMixin,
)


STAGING_METHODS = (
    "stage_acceptance_effect_intent",
    "transition_node_and_stage_acceptance_effects",
    "transition_graph_and_stage_acceptance_effects",
    "_normalize_acceptance_effect_intent_specs",
    "_normalize_acceptance_effect_intent_spec",
    "_stage_acceptance_effect_intent_in_transaction",
)
DELIVERY_METHODS = (
    "assert_acceptance_effect_intent_ready",
    "get_acceptance_effect_intent",
    "list_acceptance_effect_intents",
    "claim_acceptance_effect_intent",
    "commit_acceptance_effect_intent",
    "fail_acceptance_effect_intent",
    "reschedule_acceptance_effect_intent",
    "_require_acceptance_effect_intent",
    "_assert_acceptance_effect_claim",
)
EFFECT_RECONCILIATION_READ_METHODS = (
    "assert_effect_reconciliation_ready",
    "assert_effect_reconciliation_production_ready",
    "list_effect_reconciliation_cases",
    "list_effect_reconciliation_candidates",
    "read_graph_cancellation_effect_reconciliation",
    "_read_validated_effect_reconciliation_proof_in_transaction",
    "_list_effect_reconciliation_cases_in_transaction",
    "_effect_has_reconciliation_proof_in_transaction",
    "_derive_effect_reconciliation_case",
    "_effect_reconciliation_subject",
    "_validated_effect_reconciliation_proof",
)
EFFECT_RECONCILIATION_MUTATION_METHODS = (
    "record_graph_cancellation_effect_reconciliation_proof",
    "_record_graph_cancellation_effect_reconciliation_proof",
    "_insert_effect_reconciliation_case",
)
EFFECT_RECONCILIATION_OVERLAY_METHODS = (
    "_assert_graph_cancellation_decision_trusted",
    "_assert_graph_cancellation_effect_reconciliation_overlay",
    "_graph_cancellation_pending_effect_ids",
)
TERMINAL_RECONCILIATION_READ_METHODS = (
    "assert_terminal_reconciliation_ready",
    "assert_terminal_reconciliation_production_ready",
    "list_terminal_reconciliation_inventory_candidates",
    "list_terminal_reconciliation_cases",
    "list_terminal_reconciliation_candidates",
    "read_terminal_reconciliation_case",
    "_discover_terminal_reconciliation_cases_in_transaction",
    "_terminal_reconciliation_subject",
)
TERMINAL_RECONCILIATION_MUTATION_METHODS = (
    "inventory_terminal_reconciliation_cases",
    "record_terminal_reconciliation_proof",
    "_record_terminal_reconciliation_proof",
    "_insert_terminal_reconciliation_case",
)


def test_acceptance_effect_mixins_preserve_api_and_borrow_store_transactions() -> None:
    assert task_repository.DurableTaskStore.__bases__ == (
        TaskOutboxDeliveryStoreMixin,
        TaskGraphCancellationContinuationStoreMixin,
        TaskAcceptanceEffectStagingMixin,
        TaskAcceptanceEffectDeliveryMixin,
        TaskParallelSuccessStoreMixin,
        TaskParallelSettlementStoreMixin,
        TaskGraphCancellationRequestMixin,
        TaskGraphCancellationFinalizationMixin,
        TaskRetryMutationStoreMixin,
        TaskRetryReplayStoreMixin,
        TaskAtomicFailureStoreMixin,
        TaskOperationCheckpointStoreMixin,
        TaskOperationReconciliationStoreMixin,
        TaskEffectReconciliationStoreMixin,
        TaskGraphCancellationEffectOverlayMixin,
        TaskTerminalReconciliationStoreMixin,
        TaskNodeArtifactStoreMixin,
        TaskBudgetUsageStoreMixin,
        TaskDagStateStoreMixin,
        TaskReadyClaimStoreMixin,
    )
    for name in STAGING_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskAcceptanceEffectStagingMixin,
            name,
        )
    assert task_repository.DurableTaskStore.commit_node_acceptance is (
        TaskNodeArtifactStoreMixin.commit_node_acceptance
    )
    for name in DELIVERY_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskAcceptanceEffectDeliveryMixin,
            name,
        )
    assert (
        task_repository.build_acceptance_effect_payload_digest
        is task_record_codec.build_acceptance_effect_payload_digest
    )

    for module in (
        inspect.getmodule(TaskAcceptanceEffectStagingMixin),
        inspect.getmodule(TaskAcceptanceEffectDeliveryMixin),
    ):
        assert module is not None
        tree = ast.parse(inspect.getsource(module))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert "host_brain.persistence.task_repository" not in imported_modules

        mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        defined_methods = {
            node.name for node in mixin.body if isinstance(node, ast.FunctionDef)
        }
        assert not {"__init__", "_connect", "_transaction"} & defined_methods

        forbidden_attributes = {
            "connect",
            "commit",
            "rollback",
            "executemany",
            "executescript",
        }
        assert not {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"_connect", "_transaction"}:
                assert isinstance(node.func.value, ast.Name)
                assert node.func.value.id == "self"

        sql_literals = (
            str(node.value).upper()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        assert not any(
            sql.strip().startswith("BEGIN ")
            or sql.strip() in {"COMMIT", "ROLLBACK"}
            for sql in sql_literals
        )


def test_effect_reconciliation_split_preserves_mro_method_owners_and_ceilings() -> None:
    assert TaskEffectReconciliationStoreMixin.__bases__ == (
        TaskEffectReconciliationMutationMixin,
        TaskEffectReconciliationReadMixin,
    )
    for name in EFFECT_RECONCILIATION_READ_METHODS:
        assert getattr(TaskEffectReconciliationStoreMixin, name) is getattr(
            TaskEffectReconciliationReadMixin,
            name,
        )
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskEffectReconciliationReadMixin,
            name,
        )
    for name in EFFECT_RECONCILIATION_MUTATION_METHODS:
        assert getattr(TaskEffectReconciliationStoreMixin, name) is getattr(
            TaskEffectReconciliationMutationMixin,
            name,
        )
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskEffectReconciliationMutationMixin,
            name,
        )
    for name in EFFECT_RECONCILIATION_OVERLAY_METHODS:
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskGraphCancellationEffectOverlayMixin,
            name,
        )

    focused_modules = {
        task_effect_reconciliation_common: 1_000,
        inspect.getmodule(TaskEffectReconciliationStoreMixin): 200,
        inspect.getmodule(TaskEffectReconciliationReadMixin): 1_500,
        inspect.getmodule(TaskEffectReconciliationMutationMixin): 1_000,
        inspect.getmodule(TaskGraphCancellationEffectOverlayMixin): 500,
        inspect.getmodule(Ed25519ReconciliationProofVerifier): 500,
    }
    assert None not in focused_modules
    for module, ceiling in focused_modules.items():
        source = inspect.getsource(module)
        assert len(source.splitlines()) < ceiling
        assert "host_brain.persistence.task_repository" not in source

    assert (
        build_effect_reconciliation_attestation_binding
        is task_effect_reconciliation_common.build_effect_reconciliation_attestation_binding
    )
    assert (
        task_repository.Ed25519ReconciliationProofVerifier
        is Ed25519ReconciliationProofVerifier
    )

    assert len(inspect.getsource(task_repository).splitlines()) < 4_600


def test_terminal_reconciliation_split_preserves_one_store_and_one_uow() -> None:
    assert TaskTerminalReconciliationStoreMixin.__bases__ == (
        TaskTerminalReconciliationMutationMixin,
        TaskTerminalReconciliationReadMixin,
    )
    for name in TERMINAL_RECONCILIATION_READ_METHODS:
        assert getattr(TaskTerminalReconciliationStoreMixin, name) is getattr(
            TaskTerminalReconciliationReadMixin,
            name,
        )
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskTerminalReconciliationReadMixin,
            name,
        )
    for name in TERMINAL_RECONCILIATION_MUTATION_METHODS:
        assert getattr(TaskTerminalReconciliationStoreMixin, name) is getattr(
            TaskTerminalReconciliationMutationMixin,
            name,
        )
        assert getattr(task_repository.DurableTaskStore, name) is getattr(
            TaskTerminalReconciliationMutationMixin,
            name,
        )

    focused_modules = {
        task_terminal_reconciliation_codec_validation: 500,
        task_terminal_reconciliation_common: 800,
        task_terminal_reconciliation_decision_authority: 500,
        task_terminal_reconciliation_projection: 800,
        task_terminal_reconciliation_schema: 250,
        inspect.getmodule(TaskTerminalReconciliationStoreMixin): 100,
        inspect.getmodule(TaskTerminalReconciliationReadMixin): 1_500,
        inspect.getmodule(TaskTerminalReconciliationMutationMixin): 1_000,
    }
    assert None not in focused_modules
    for module, ceiling in focused_modules.items():
        source = inspect.getsource(module)
        assert len(source.splitlines()) < ceiling
        assert "host_brain.persistence.task_repository" not in source

    mutation_tree = ast.parse(
        inspect.getsource(inspect.getmodule(TaskTerminalReconciliationMutationMixin))
    )
    assert "sqlite3.connect" not in ast.unparse(mutation_tree)
    assert "conn.commit" not in ast.unparse(mutation_tree)
    assert "conn.rollback" not in ast.unparse(mutation_tree)
