"""Pure route bindings for committed-checkpoint budget terminals."""

from __future__ import annotations

import re
from urllib.parse import unquote


_NODE_ROUTE_KINDS = frozenset({"chat", "skill", "delegate", "tool"})
_ROUTE_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


def requires_internal_agent_authority(
    *,
    command_surface: object,
    command_route: object,
    command_target: object,
    node_route: object,
    projection_route: object,
    projection_target: object,
) -> bool:
    """Return true if any durable route fact names the protected Agent path."""

    command = (_text(command_route).lower(), _text(command_target))
    projection = (_text(projection_route).lower(), _text(projection_target))
    parsed_node = _parse_node_route(node_route)
    protected = ("delegate", "agent_runtime")
    return (
        command == protected
        or projection == protected
        or parsed_node == protected
        or (_text(command_surface).lower() == "internal" and command == protected)
    )


def canonical_checkpoint_route_authority_matches(
    *,
    command_surface: object,
    command_route: object,
    command_target: object,
    node_route: object,
    projection_route: object,
    projection_target: object,
) -> bool:
    """Bind a canonical terminal to its durable command, node and projection."""

    if requires_internal_agent_authority(
        command_surface=command_surface,
        command_route=command_route,
        command_target=command_target,
        node_route=node_route,
        projection_route=projection_route,
        projection_target=projection_target,
    ):
        return False
    command = (_text(command_route).lower(), _text(command_target))
    projection = (_text(projection_route).lower(), _text(projection_target))
    parsed_node = _parse_node_route(node_route)
    if parsed_node is None:
        return False
    node_kind, node_target = parsed_node
    if node_kind not in _NODE_ROUTE_KINDS:
        return False
    if command[0] == "plan":
        return projection == parsed_node
    return command == projection == parsed_node


def _parse_node_route(value: object) -> tuple[str, str] | None:
    raw = _text(value)
    kind, separator, encoded_target = raw.partition(":")
    kind = kind.strip().lower()
    if kind == "chat" and not separator:
        return "chat", ""
    if kind not in _NODE_ROUTE_KINDS or not separator or not encoded_target:
        return None
    try:
        target = unquote(encoded_target, errors="strict").strip()
    except (UnicodeDecodeError, ValueError):
        return None
    if not _ROUTE_TARGET.fullmatch(target):
        return None
    return kind, target


def _text(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "canonical_checkpoint_route_authority_matches",
    "requires_internal_agent_authority",
]
