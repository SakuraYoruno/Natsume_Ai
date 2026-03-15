"""T-WCanon-05/06: World Canon 鍒嗘Ы闅旂 + QA Canon-as-fact 鎷︽埅 contract 娴嬭瘯锛圱ASK-0113锛夈€?
T-WCanon-05锛氬垎妲介殧绂?  - build_world_canon_slot() 杩斿洖鐨勬潯鐩?slot="authoritative"銆乲ind="world_canon"锛?  - World Canon 鏉＄洰涓嶆贩鍏?retrieval / style_pack / bond_summary 妲戒綅锛?  - evidence_level="CANON_REFERENCE_ONLY"锛寀nsafe_as_fact=True锛?  - prompt_context_with_world_canon.json fixture 閫氳繃 validate_prompt_context()锛?  - Top-K 闄愬埗鐢熸晥锛坱op_k=2 鏃舵渶澶氳繑鍥?2 鏉★級銆?
T-WCanon-06锛歈A Canon-as-fact 鎷︽埅
  - check_canon_as_fact() 瀵?bad cases 杩斿洖 violated=True锛?  - check_canon_as_fact() 瀵?good cases 杩斿洖 violated=False锛?  - qa_canon_as_fact_bad.json fixture 涓墍鏈?bad cases 鍧囪鎷︽埅锛?  - 鎷︽埅鍚?enforce_qa_contract() 闄嶇骇涓?fallback output锛?  - 閲嶅啓鍚庝粛鍚?Canon-as-fact 鏃堕檷绾т负 Host 鏈€灏忔ā鏉裤€?"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SSOT_WORLD = ROOT / "host_brain" / "canon" / "world"
FIXTURE_WORLD = ROOT / "tests" / "fixtures" / "canon" / "world"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestWorldCanonSlotIsolation(unittest.TestCase):
    """T-WCanon-05: World Canon 鍒嗘Ы闅旂銆?""

    def test_t_wcanon_05a_build_world_canon_slot_returns_authoritative_entries(self) -> None:
        """build_world_canon_slot() 杩斿洖鐨勬潯鐩?slot='authoritative'銆乲ind='world_canon'銆?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        self.assertGreater(len(entries), 0, msg="搴旇嚦灏戣繑鍥?1 涓?World Canon 鏉＄洰")
        for entry in entries:
            self.assertEqual(entry.get("slot"), "authoritative", msg=f"slot 閿欒: {entry}")
            self.assertEqual(entry.get("kind"), "world_canon", msg=f"kind 閿欒: {entry}")

    def test_t_wcanon_05b_entries_have_evidence_level_canon_reference_only(self) -> None:
        """姣忎釜鏉＄洰 evidence_level='CANON_REFERENCE_ONLY'锛寀nsafe_as_fact=True銆?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            self.assertEqual(
                entry.get("evidence_level"),
                "CANON_REFERENCE_ONLY",
                msg=f"evidence_level 閿欒: {entry}",
            )
            self.assertTrue(
                entry.get("unsafe_as_fact"),
                msg=f"unsafe_as_fact 搴斾负 True: {entry}",
            )

    def test_t_wcanon_05c_entries_not_in_retrieval_or_style_pack(self) -> None:
        """World Canon 鏉＄洰涓嶆贩鍏?retrieval / style_pack / bond_summary 妲戒綅銆?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            # 涓嶅惈 style 瀛楁
            for style_key in ("tone", "speaking_style", "style_instruction"):
                self.assertNotIn(style_key, entry, msg=f"World Canon 鏉＄洰鍚?style 瀛楁: {style_key}")
            # 涓嶅惈 retrieval 涓撳睘瀛楁
            for retrieval_key in ("query", "score", "retrieved_at"):
                self.assertNotIn(retrieval_key, entry, msg=f"World Canon 鏉＄洰鍚?retrieval 瀛楁: {retrieval_key}")

    def test_t_wcanon_05d_top_k_limit_respected(self) -> None:
        """top_k=2 鏃舵渶澶氳繑鍥?2 鏉°€?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD, top_k=2)
        self.assertLessEqual(len(entries), 2, msg="top_k=2 鏃朵笉搴旇秴杩?2 鏉?)

    def test_t_wcanon_05e_fixture_passes_validate_prompt_context(self) -> None:
        """prompt_context_with_world_canon.json fixture 閫氳繃 validate_prompt_context()銆?""
        from host_brain.core.prompt_context import validate_prompt_context

        fixture_path = FIXTURE_WORLD / "prompt_context_with_world_canon.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 涓嶅瓨鍦? {fixture_path}")
        payload = _load_json(fixture_path)
        result = validate_prompt_context(payload)
        self.assertTrue(result.valid, msg=f"fixture 楠岃瘉澶辫触: {result.as_dict()}")

    def test_t_wcanon_05f_world_canon_entries_pass_validate_prompt_context(self) -> None:
        """build_world_canon_slot() 杩斿洖鐨勬潯鐩敞鍏?authoritative 鍚庨€氳繃 validate_prompt_context()銆?""
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
        self.assertTrue(result.valid, msg=f"娉ㄥ叆 World Canon 鍚?prompt_context 楠岃瘉澶辫触: {result.as_dict()}")

    def test_t_wcanon_05g_only_active_status_injected(self) -> None:
        """鍙敞鍏?status='active' 鐨勬潯鐩紙榛樿 status_filter锛夈€?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            self.assertEqual(
                entry.get("status"),
                "active",
                msg=f"闈?active 鏉＄洰琚敞鍏? {entry.get('doc_id')}",
            )

    def test_t_wcanon_05h_citation_format_correct(self) -> None:
        """citation 鏍煎紡涓?world_canon:{doc_id}@v{version}:{hash[:12]}銆?""
        from host_brain.core.prompt_context import build_world_canon_slot

        entries = build_world_canon_slot(world_root=SSOT_WORLD)
        for entry in entries:
            citation = entry.get("citation", "")
            self.assertTrue(
                citation.startswith("world_canon:"),
                msg=f"citation 鏍煎紡閿欒: {citation}",
            )
            self.assertIn("@v", citation, msg=f"citation 缂哄皯鐗堟湰: {citation}")


class TestWorldCanonQAInterception(unittest.TestCase):
    """T-WCanon-06: QA Canon-as-fact 鎷︽埅銆?""

    def test_t_wcanon_06a_check_canon_as_fact_bad_cases_violated(self) -> None:
        """check_canon_as_fact() 瀵?bad cases 杩斿洖 violated=True銆?""
        from host_brain.core.qa import check_canon_as_fact

        bad_cases = [
            "鏍规嵁涓栫晫瑙傝瀹氾紝宸叉墽琛屼簡绯荤粺鍒濆鍖栨祦绋嬨€?,
            "Canon 瑙勫畾鐨勬灦鏋勫凡纭閮ㄧ讲瀹屾垚銆?,
            "The world canon settings have been saved successfully.",
        ]
        for text in bad_cases:
            result = check_canon_as_fact(text)
            self.assertTrue(
                result.violated,
                msg=f"搴旀娴嬪埌 Canon-as-fact 杩濊: {text!r}",
            )
            codes = {v.code for v in result.violations}
            self.assertIn("CANON_AS_FACT_VIOLATION", codes, msg=f"杩濊鐮侀敊璇? {text!r}")

    def test_t_wcanon_06b_check_canon_as_fact_good_cases_not_violated(self) -> None:
        """check_canon_as_fact() 瀵?good cases 杩斿洖 violated=False銆?""
        from host_brain.core.qa import check_canon_as_fact

        good_cases = [
            "鏍规嵁涓栫晫瑙傝瀹氾紝Host Brain 璐熻矗鎰忓浘瑙ｆ瀽銆?,
            "绯荤粺姝ｅ湪澶勭悊璇锋眰锛岃绋嶅€欍€?,
            "澶忕洰浼氬弬鑰冭瀹氭潵鍥炵瓟闂锛屼絾涓嶄細澹扮О鑷繁瀹屾垚浜嗕换浣曟搷浣溿€?,
        ]
        for text in good_cases:
            result = check_canon_as_fact(text)
            self.assertFalse(
                result.violated,
                msg=f"涓嶅簲妫€娴嬪埌 Canon-as-fact 杩濊: {text!r}",
            )

    def test_t_wcanon_06c_fixture_bad_cases_all_intercepted(self) -> None:
        """qa_canon_as_fact_bad.json fixture 涓墍鏈?bad cases 鍧囪鎷︽埅銆?""
        from host_brain.core.qa import check_canon_as_fact

        fixture_path = FIXTURE_WORLD / "qa_canon_as_fact_bad.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 涓嶅瓨鍦? {fixture_path}")
        data = _load_json(fixture_path)

        for case in data["cases"]:
            result = check_canon_as_fact(case["narrative"])
            if case["expect_violated"]:
                self.assertTrue(
                    result.violated,
                    msg=f"case {case['id']} 搴旇鎷︽埅: {case['narrative']!r}",
                )
                codes = {v.code for v in result.violations}
                self.assertIn(
                    case["expect_code"],
                    codes,
                    msg=f"case {case['id']} 杩濊鐮侀敊璇?,
                )
            else:
                self.assertFalse(
                    result.violated,
                    msg=f"case {case['id']} 涓嶅簲琚嫤鎴? {case['narrative']!r}",
                )

    def test_t_wcanon_06d_canon_as_fact_in_llm_output_triggers_downgrade(self) -> None:
        """鍙欎簨鍚?Canon-as-fact 鏃?enforce_qa_contract() 闄嶇骇涓?fallback output銆?""
        import json as _json
        from host_brain.core.qa import enforce_qa_contract, STAGE_QA_DOWNGRADE

        bad_output = _json.dumps(
            {
                "narrative": {"text": "鏍规嵁涓栫晫瑙傝瀹氾紝宸叉墽琛屼簡绯荤粺鍒濆鍖栨祦绋嬨€?},
                "intent_draft": {},
                "meta": {},
            },
            ensure_ascii=False,
        )
        result = enforce_qa_contract(bad_output)
        self.assertTrue(result.downgraded, msg="Canon-as-fact 搴旇Е鍙戦檷绾?)
        self.assertEqual(result.stage, STAGE_QA_DOWNGRADE)

    def test_t_wcanon_06e_rewrite_still_canon_as_fact_triggers_downgrade(self) -> None:
        """閲嶅啓鍚庝粛鍚?Canon-as-fact 鏃堕檷绾т负 Host 鏈€灏忔ā鏉裤€?""
        import json as _json
        from host_brain.core.qa import enforce_qa_contract, STAGE_QA_DOWNGRADE

        bad_output = _json.dumps(
            {
                "narrative": {"text": "鏍规嵁涓栫晫瑙傝瀹氾紝宸叉墽琛屼簡绯荤粺鍒濆鍖栨祦绋嬨€?},
                "intent_draft": {},
                "meta": {},
            },
            ensure_ascii=False,
        )

        def still_bad_rewrite(raw: str, lint_result: object) -> str:
            # 閲嶅啓鍚庝粛鐒跺惈 Canon-as-fact
            return _json.dumps(
                {
                    "narrative": {"text": "Canon 瑙勫畾鐨勬灦鏋勫凡纭閮ㄧ讲瀹屾垚銆?},
                    "intent_draft": {},
                    "meta": {},
                },
                ensure_ascii=False,
            )

        result = enforce_qa_contract(bad_output, rewrite_once=still_bad_rewrite)
        self.assertTrue(result.downgraded, msg="閲嶅啓鍚庝粛 Canon-as-fact 搴旇Е鍙戦檷绾?)
        self.assertEqual(result.stage, STAGE_QA_DOWNGRADE)

    def test_t_wcanon_06f_check_canon_as_fact_returns_structured_result(self) -> None:
        """check_canon_as_fact() 杩斿洖 CanonAsFactResult锛屽惈 violated / violations / as_dict()銆?""
        from host_brain.core.qa import check_canon_as_fact, CanonAsFactResult

        result = check_canon_as_fact("鏍规嵁涓栫晫瑙傝瀹氾紝宸叉墽琛屼簡绯荤粺鍒濆鍖栨祦绋嬨€?)
        self.assertIsInstance(result, CanonAsFactResult)
        self.assertTrue(result.violated)
        self.assertIsInstance(result.violations, list)
        d = result.as_dict()
        self.assertIn("violated", d)
        self.assertIn("violations", d)


if __name__ == "__main__":
    unittest.main()

