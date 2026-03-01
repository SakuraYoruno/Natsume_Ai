import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_POINTER = ROOT / "flows" / "active.pointer"
FLOW_INDEX = ROOT / "docs" / "60_trace" / "flows" / "index.md"


def _load_active_pointer() -> dict:
    return json.loads(ACTIVE_POINTER.read_text(encoding="utf-8"))


class TestFlowAssetsContract(unittest.TestCase):
    def test_t_flow_01_active_pointer_shape_and_paths(self) -> None:
        pointer = _load_active_pointer()
        self.assertEqual(set(pointer.keys()), {"dev", "staging", "prod"})

        for env, rel_path in pointer.items():
            self.assertTrue(isinstance(rel_path, str) and rel_path.strip(), f"{env} path is empty")
            abs_path = ROOT / rel_path
            self.assertTrue(abs_path.exists(), f"{env} points to missing file: {rel_path}")

    def test_t_flow_02_index_matches_active_pointer(self) -> None:
        pointer = _load_active_pointer()
        index_text = FLOW_INDEX.read_text(encoding="utf-8")

        for env, rel_path in pointer.items():
            self.assertIn(rel_path, index_text, f"{env} path not found in flow index: {rel_path}")
