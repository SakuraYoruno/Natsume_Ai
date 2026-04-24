"""Registry for the tribe organ agent surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


TRIBE_DIR = Path(__file__).resolve().parents[1] / "tribe"


@dataclass(frozen=True, slots=True)
class OrganDescriptor:
    organ_id: str
    module: str
    file_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrganRegistry:
    def __init__(self, organs: list[OrganDescriptor]) -> None:
        self._organs = {organ.organ_id: organ for organ in organs}

    def list_organs(self) -> list[dict[str, Any]]:
        return [organ.to_dict() for organ in sorted(self._organs.values(), key=lambda item: item.organ_id)]

    def get(self, organ_id: str) -> OrganDescriptor | None:
        return self._organs.get(str(organ_id or "").strip())


def build_default_organ_registry(*, tribe_dir: Path | None = None) -> OrganRegistry:
    root = tribe_dir or TRIBE_DIR
    organs: list[OrganDescriptor] = []
    for path in sorted(root.glob("*_agent.py")):
        if path.name == "base_agent.py":
            continue
        organ_id = path.stem.removesuffix("_agent")
        organs.append(
            OrganDescriptor(
                organ_id=organ_id,
                module=f"host_brain.tribe.{path.stem}",
                file_path=str(path),
            )
        )
    return OrganRegistry(organs)
