from __future__ import annotations

import inspect

from host_brain.persistence import host_command_repository
from host_brain.persistence import host_command_repository_codec
from host_brain.persistence import host_command_repository_types


def test_host_command_repository_is_a_focused_unit_of_work() -> None:
    source = inspect.getsource(host_command_repository)

    assert len(source.splitlines()) < 1_500
    assert "sqlite3.connect" in source
    assert "def work_order_roundtrip" not in source
    assert "def assert_current_graph_identity" not in source


def test_host_command_codec_owns_no_connection_or_transaction() -> None:
    source = inspect.getsource(host_command_repository_codec)

    assert "sqlite3.connect" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "INSERT INTO host_command_journal" not in source


def test_public_error_and_constants_are_stable_reexports() -> None:
    assert (
        host_command_repository.HostCommandRepositoryError
        is host_command_repository_types.HostCommandRepositoryError
    )
    assert (
        host_command_repository.HOST_COMMAND_INVALID
        == host_command_repository_types.HOST_COMMAND_INVALID
    )
