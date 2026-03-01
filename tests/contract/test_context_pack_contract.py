import json
import unittest
from pathlib import Path

from host_brain.core.context_pack import (
    CP_BOND_NUMERIC_FORBIDDEN,
    CP_CANON_AS_FACT,
    CP_SLOT_MIXED,
    validate_context_pack,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "context_pack"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestContextPackContract(unittest.TestCase):
    def test_t_cp_01_valid_context_pack_passes(self) -> None:
        payload = _load_json(FIXTURES / "good" / "minimal_valid.json")
        result = validate_context_pack(payload)
        self.assertTrue(result.valid, msg=result.as_dict())

    def test_t_cp_01_mixed_slot_is_rejected(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "mixed_slot.json")
        result = validate_context_pack(payload)
        codes = {v.code for v in result.violations}
        self.assertIn(CP_SLOT_MIXED, codes, msg=result.as_dict())

    def test_t_cp_01_bond_summary_numeric_is_rejected(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "bond_summary_numeric.json")
        result = validate_context_pack(payload)
        codes = {v.code for v in result.violations}
        self.assertIn(CP_BOND_NUMERIC_FORBIDDEN, codes, msg=result.as_dict())

    def test_t_cp_01_canon_as_fact_is_rejected(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "canon_as_fact.json")
        result = validate_context_pack(payload)
        codes = {v.code for v in result.violations}
        self.assertIn(CP_CANON_AS_FACT, codes, msg=result.as_dict())
