from __future__ import annotations

import ast
import inspect

from host_brain.app import acceptance_effect_handler_common
from host_brain.app import acceptance_effect_handlers
from host_brain.app import acceptance_surface_effect_handler


def test_acceptance_effect_handlers_are_split_below_focused_file_gates() -> None:
    assert len(inspect.getsource(acceptance_effect_handlers).splitlines()) < 1_300
    assert len(inspect.getsource(acceptance_surface_effect_handler).splitlines()) < 600
    assert len(inspect.getsource(acceptance_effect_handler_common).splitlines()) < 100


def test_surface_handler_owns_gateway_logic_without_persistence_authority() -> None:
    handlers = acceptance_effect_handlers.build_production_acceptance_effect_handlers()
    surface_handler = handlers["surface.reply_delivery"]
    assert type(surface_handler).__module__ == (
        "host_brain.app.acceptance_surface_effect_handler"
    )

    surface_source = inspect.getsource(acceptance_surface_effect_handler)
    for forbidden in (
        "import sqlite3",
        "host_brain.persistence",
        "CREATE TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
    ):
        assert forbidden not in surface_source

    main_source = inspect.getsource(acceptance_effect_handlers)
    assert "class _AcceptedSurfaceReplyDeliveryHandler" not in main_source
    assert "async def _deliver_accepted_surface_reply" not in main_source


def test_common_helpers_remain_pure_and_surface_helpers_have_one_owner() -> None:
    tree = ast.parse(inspect.getsource(acceptance_effect_handler_common))
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    for name in (
        "_deliver_accepted_surface_reply",
        "_accepted_surface_request",
        "_surface_delivery_id",
        "_surface_gateway_payload_digest",
    ):
        assert not hasattr(acceptance_effect_handlers, name)
        assert callable(getattr(acceptance_surface_effect_handler, name))
