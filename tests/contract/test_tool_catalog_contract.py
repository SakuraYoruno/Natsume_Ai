import json
import unittest
from pathlib import Path

from host_brain.core.prompt_context import (
    CP_TOOL_META_FORBIDDEN_FIELD,
    validate_prompt_context,
)
from host_brain.tools.catalog import TOOL_META_REQUIRED_KEYS, ToolCatalog
from host_brain.tools.selector import select_tool_meta_pack


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tools"
CTX_FIX = ROOT / "tests" / "fixtures" / "prompt_context" / "good" / "minimal_valid.json"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestToolCatalogContract(unittest.TestCase):
    def test_t_tool_01_tool_meta_pack_is_metadata_only_and_context_safe(self) -> None:
        catalog_items = _load_json(FIXTURES / "good" / "catalog_mixed.json")
        catalog = ToolCatalog()
        for item in catalog_items:
            catalog.register(item)

        exported = catalog.export_meta(include_write=True)
        self.assertEqual(4, len(exported))
        for item in exported:
            self.assertEqual(set(TOOL_META_REQUIRED_KEYS), set(item.keys()))
            self.assertNotIn("args", item)
            self.assertNotIn("tool_input", item)
            self.assertNotIn("approval_token_ref", item)

        prompt_context = _load_json(CTX_FIX)
        prompt_context["tool_meta_pack"] = catalog.export_meta(include_write=False)
        result = validate_prompt_context(prompt_context)
        self.assertTrue(result.valid, msg=result.as_dict())

        bad_meta = _load_json(FIXTURES / "bad" / "tool_meta_with_exec_fields.json")
        prompt_context_bad = _load_json(CTX_FIX)
        prompt_context_bad["tool_meta_pack"] = [bad_meta]
        bad_result = validate_prompt_context(prompt_context_bad)
        codes = {v.code for v in bad_result.violations}
        self.assertIn(CP_TOOL_META_FORBIDDEN_FIELD, codes, msg=bad_result.as_dict())

    def test_t_tool_02_top_k_selection_hides_write_by_default(self) -> None:
        catalog_items = _load_json(FIXTURES / "good" / "catalog_mixed.json")
        catalog = ToolCatalog()
        for item in catalog_items:
            catalog.register(item)

        full_ids = {item["id"] for item in catalog.export_meta(include_write=True)}
        read_only_selection = select_tool_meta_pack(
            catalog,
            query="delete file",
            k=3,
            include_write=False,
        )
        self.assertLess(len(read_only_selection.selected_ids), len(full_ids))
        self.assertTrue(set(read_only_selection.selected_ids).issubset(full_ids))
        for item in read_only_selection.tool_meta_pack:
            self.assertEqual("READ", item["side_effect_level"])

        read_only_repeat = select_tool_meta_pack(
            catalog,
            query="delete file",
            k=3,
            include_write=False,
        )
        self.assertEqual(read_only_selection.selected_ids, read_only_repeat.selected_ids)
        self.assertEqual(
            read_only_selection.tool_visibility_hash,
            read_only_repeat.tool_visibility_hash,
        )

        write_allowed = select_tool_meta_pack(
            catalog,
            query="delete file",
            k=3,
            include_write=True,
        )
        self.assertTrue(
            any(item["side_effect_level"] == "WRITE" for item in write_allowed.tool_meta_pack)
        )
        self.assertNotEqual(
            read_only_selection.tool_visibility_hash,
            write_allowed.tool_visibility_hash,
        )


if __name__ == "__main__":
    unittest.main()

