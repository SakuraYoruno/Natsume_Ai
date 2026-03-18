"""T-WCanon-05/06: World Canon 分槽隔离 + QA Canon-as-fact 拦截 contract 测试（TASK-0113）。

T-WCanon-05：分槽隔离
  - build_world_canon_slot() 返回的条目 slot="authoritative"、kind="world_canon"；
  - World Canon 条目不混入 retrieval / style_pack / bond_summary 槽位；
  - evidence_level="CANON_REFERENCE_ONLY"，unsafe_as_fact=True；
  - prompt_context_with_world_canon.json fixture 通过 validate_prompt_context()；
  - Top-K 限制生效（top_k=2 时最多返回 2 条）。

T-WCanon-06：QA Canon-as-fact 拦截
  - check_canon_as_fact() 对 bad cases 返回 violated=True；
  - check_canon_as_fact() 对 good cases 返回 violated=False；
  - qa_canon_as_fact_bad.json fixture 中所有 bad cases 均被拦截；
  - 拦截后 enforce_qa_contract() 降级为 fallback output；
  - 重写后仍含 Canon-as-fact 时降级为 Host 最小模板。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SSOT_WORLD = ROOT / "host_brain" / "canon" / "world"
FIXTURE_WORLD = ROOT / "tests" / "fixtures" / "canon" / "world"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestWorldCanonSlotIsolation(unittest.TestCase):
    """T-WCanon-05: World Canon 分槽隔离。"""

    def test_t_wcanon_05a_build_world_canon_slot_returns_authoritative_entries(self) -> None:
        """build_world_canon_slot() 返回的条目 slot='authoritative'、kind='world_canon'。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        self.assertGreater(len(entries), 0, msg="应该至少返回 1 个 World Canon 条目")
        for entry in entries:
            self.assertEqual(entry.get("slot"), "authoritative", msg=f"slot 错误: {entry}")
            self.assertEqual(entry.get("kind"), "world_canon", msg=f"kind 错误: {entry}")

    def test_t_wcanon_05b_entries_have_evidence_level_canon_reference_only(self) -> None:
        """每个条目 evidence_level='CANON_REFERENCE_ONLY'，unsafe_as_fact=True。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            self.assertEqual(
                entry.get("evidence_level"),
                "CANON_REFERENCE_ONLY",
                msg=f"evidence_level 错误: {entry}",
            )
            self.assertTrue(
                entry.get("unsafe_as_fact"),
                msg=f"unsafe_as_fact 应为 True: {entry}",
            )

    def test_t_wcanon_05c_entries_not_in_retrieval_or_style_pack(self) -> None:
        """World Canon 条目不混入 retrieval / style_pack / bond_summary 槽位。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            # 不含 style 字段
            for style_key in ("tone", "speaking_style", "style_instruction"):
                self.assertNotIn(style_key, entry, msg=f"World Canon 条目含 style 字段: {style_key}")
            # 不含 retrieval 专属字段
            for retrieval_key in ("query", "score", "retrieved_at"):
                self.assertNotIn(retrieval_key, entry, msg=f"World Canon 条目含 retrieval 字段: {retrieval_key}")

    def test_t_wcanon_05d_top_k_limit_respected(self) -> None:
        """top_k=2 时最多返回 2 条。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD, top_k=2)
        self.assertLessEqual(len(entries), 2, msg="top_k=2 时不应超过 2 条")

    def test_t_wcanon_05e_fixture_passes_validate_prompt_context(self) -> None:
        """prompt_context_with_world_canon.json fixture 通过 validate_prompt_context()。"""
        from host_brain.core.prompt_context import validate_prompt_context

        fixture_path = FIXTURE_WORLD / "prompt_context_with_world_canon.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        payload = _load_json(fixture_path)
        result = validate_prompt_context(payload)
        self.assertTrue(result.valid, msg=f"fixture 验证失败: {result.as_dict()}")

    def test_t_wcanon_05f_world_canon_entries_pass_validate_prompt_context(self) -> None:
        """build_world_canon_slot() 返回的条目注入 authoritative 后通过 validate_prompt_context()。"""
        from host_brain.core.prompt_context import build_world_canon_slot, validate_prompt_context

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        pack = {
            "hard_snapshot": {"session_id": "test-slot-isolation"},
            "bond_summary": {},
            "authoritative": entries,
            "retrieval": [],
            "style_pack": {},
            "constraints": {},
            "tool_meta_pack": [],
        }
        result = validate_prompt_context(pack)
        self.assertTrue(result.valid, msg=f"注入 World Canon 后 prompt_context 验证失败: {result.as_dict()}")

    def test_t_wcanon_05g_only_active_status_injected(self) -> None:
        """只注入 status='active' 的条目（默认 status_filter）。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            self.assertEqual(
                entry.get("status"),
                "active",
                msg=f"非 active 条目被注入: {entry.get('doc_id')}",
            )

    def test_t_wcanon_05h_citation_format_correct(self) -> None:
        """citation 格式为 world_canon:{doc_id}@v{version}:{hash[:12]}。"""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            citation = entry.get("citation", "")
            self.assertTrue(
                citation.startswith("world_canon:"),
                msg=f"citation 格式错误: {citation}",
            )
            self.assertIn("@v", citation, msg=f"citation 缺少版本: {citation}")


class TestWorldCanonQAInterception(unittest.TestCase):
    """T-WCanon-06: QA Canon-as-fact 拦截。"""

    def test_t_wcanon_06a_check_canon_as_fact_bad_cases_violated(self) -> None:
        """check_canon_as_fact() 对 bad cases 返回 violated=True。"""
        from host_brain.core.qa import check_canon_as_fact

        bad_cases = [
            "根据世界观设定，已执行了系统初始化流程。",
            "Canon 规定的架构已确认部署完成。",
            "The world canon settings have been saved successfully.",
        ]
        for text in bad_cases:
            result = check_canon_as_fact(text)
            self.assertTrue(
                result.violated,
                msg=f"应检测到 Canon-as-fact 违规: {text!r}",
            )
            codes = {v.code for v in result.violations}
            self.assertIn("CANON_AS_FACT_VIOLATION", codes, msg=f"违规码错误: {text!r}")

    def test_t_wcanon_06b_check_canon_as_fact_good_cases_not_violated(self) -> None:
        """check_canon_as_fact() 对 good cases 返回 violated=False。"""
        from host_brain.core.qa import check_canon_as_fact

        good_cases = [
            "根据世界观设定，Host Brain 负责意图解析。",
            "系统正在处理请求，请稍候。",
            "夏目会参考设定来回答问题，但不会声称自己完成了任何操作。",
        ]
        for text in good_cases:
            result = check_canon_as_fact(text)
            self.assertFalse(
                result.violated,
                msg=f"不应检测到 Canon-as-fact 违规: {text!r}",
            )

    def test_t_wcanon_06c_fixture_bad_cases_all_intercepted(self) -> None:
        """qa_canon_as_fact_bad.json fixture 中所有 bad cases 均被拦截。"""
        from host_brain.core.qa import check_canon_as_fact

        fixture_path = FIXTURE_WORLD / "qa_canon_as_fact_bad.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        data = _load_json(fixture_path)

        for case in data["cases"]:
            result = check_canon_as_fact(case["narrative"])
            if case["expect_violated"]:
                self.assertTrue(
                    result.violated,
                    msg=f"case {case['id']} 应该拦截: {case['narrative']!r}",
                )
                codes = {v.code for v in result.violations}
                self.assertIn(
                    case["expect_code"],
                    codes,
                    msg=f"case {case['id']} 违规码错误",
                )
            else:
                self.assertFalse(
                    result.violated,
                    msg=f"case {case['id']} 不应被拦截: {case['narrative']!r}",
                )

    def test_t_wcanon_06d_canon_as_fact_in_llm_output_triggers_downgrade(self) -> None:
        """叙事含 Canon-as-fact 时 enforce_qa_contract() 降级为 fallback output。"""
        import json as _json
        from host_brain.core.qa import enforce_qa_contract, STAGE_QA_DOWNGRADE

        bad_output = _json.dumps(
            {
                "narrative": {"text": "根据世界观设定，已执行了系统初始化流程。"},
                "intent_draft": {},
                "meta": {},
            },
            ensure_ascii=False,
        )
        result = enforce_qa_contract(bad_output)
        self.assertTrue(result.downgraded, msg="Canon-as-fact 应触发降级")
        self.assertEqual(result.stage, STAGE_QA_DOWNGRADE)

    def test_t_wcanon_06e_rewrite_still_canon_as_fact_triggers_downgrade(self) -> None:
        """重写后仍含 Canon-as-fact 时降级为 Host 最小模板。"""
        import json as _json
        from host_brain.core.qa import enforce_qa_contract, STAGE_QA_DOWNGRADE

        bad_output = _json.dumps(
            {
                "narrative": {"text": "根据世界观设定，已执行了系统初始化流程。"},
                "intent_draft": {},
                "meta": {},
            },
            ensure_ascii=False,
        )

        def still_bad_rewrite(raw: str, lint_result: object) -> str:
            # 重写后仍然含 Canon-as-fact
            return _json.dumps(
                {
                    "narrative": {"text": "Canon 规定的架构已确认部署完成。"},
                    "intent_draft": {},
                    "meta": {},
                },
                ensure_ascii=False,
            )

        result = enforce_qa_contract(bad_output, rewrite_once=still_bad_rewrite)
        self.assertTrue(result.downgraded, msg="重写后仍 Canon-as-fact 应触发降级")
        self.assertEqual(result.stage, STAGE_QA_DOWNGRADE)

    def test_t_wcanon_06f_check_canon_as_fact_returns_structured_result(self) -> None:
        """check_canon_as_fact() 返回 CanonAsFactResult，含 violated / violations / as_dict()。"""
        from host_brain.core.qa import check_canon_as_fact, CanonAsFactResult

        result = check_canon_as_fact("根据世界观设定，已执行了系统初始化流程。")
        self.assertIsInstance(result, CanonAsFactResult)
        self.assertTrue(result.violated)
        self.assertIsInstance(result.violations, list)
        d = result.as_dict()
        self.assertIn("violated", d)
        self.assertIn("violations", d)


if __name__ == "__main__":
    unittest.main()
