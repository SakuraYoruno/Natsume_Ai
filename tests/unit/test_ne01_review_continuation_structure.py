from __future__ import annotations

import ast
import inspect

from host_brain.persistence import review_continuation_binding
from host_brain.persistence import review_continuation_codec
from host_brain.persistence import review_continuation_store


def test_review_continuation_store_is_a_focused_persistence_owner() -> None:
    source = inspect.getsource(review_continuation_store)

    assert len(source.splitlines()) < 1_500
    definitions = {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert "parse_task_review_subject" not in definitions
    assert "_review_item_from_row" not in definitions
    assert "_row_dict" not in definitions


def test_review_continuation_public_contracts_remain_compatible() -> None:
    assert (
        review_continuation_store.ReviewContinuationStoreError
        is review_continuation_binding.ReviewContinuationStoreError
    )
    assert (
        review_continuation_store.TaskReviewSubjectBinding
        is review_continuation_binding.TaskReviewSubjectBinding
    )
    assert (
        review_continuation_store.parse_task_review_subject
        is review_continuation_binding.parse_task_review_subject
    )
    assert (
        review_continuation_store.REVIEW_CONTINUATION_TABLE
        == review_continuation_binding.REVIEW_CONTINUATION_TABLE
    )
    assert len(inspect.getsource(review_continuation_binding).splitlines()) < 1_500
    assert len(inspect.getsource(review_continuation_codec).splitlines()) < 1_500
