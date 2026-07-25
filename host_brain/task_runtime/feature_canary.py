"""Compatibility re-exports for the neutral Task Runtime canary contract."""

from host_brain.contracts.task_runtime_feature_canary import (
    BOUNDED_FANOUT_FEATURE_KEY,
    FeatureCanarySignatureVerifier,
    TASK_RUNTIME_FEATURE_CANARY_EXPIRED,
    TASK_RUNTIME_FEATURE_CANARY_INVALID,
    TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
    TASK_RUNTIME_FEATURE_CANARY_REQUIRED,
    TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION,
    TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
    TaskRuntimeFeatureCanaryError,
    TaskRuntimeFeatureCanaryReceipt,
    bounded_fanout_configuration_digest,
    require_bounded_fanout_canary,
    task_runtime_feature_canary_signing_message,
)

__all__ = [
    "BOUNDED_FANOUT_FEATURE_KEY",
    "FeatureCanarySignatureVerifier",
    "TASK_RUNTIME_FEATURE_CANARY_EXPIRED",
    "TASK_RUNTIME_FEATURE_CANARY_INVALID",
    "TASK_RUNTIME_FEATURE_CANARY_MISMATCH",
    "TASK_RUNTIME_FEATURE_CANARY_REQUIRED",
    "TASK_RUNTIME_FEATURE_CANARY_SCHEMA_VERSION",
    "TASK_RUNTIME_FEATURE_CANARY_UNATTESTED",
    "TaskRuntimeFeatureCanaryError",
    "TaskRuntimeFeatureCanaryReceipt",
    "bounded_fanout_configuration_digest",
    "require_bounded_fanout_canary",
    "task_runtime_feature_canary_signing_message",
]
