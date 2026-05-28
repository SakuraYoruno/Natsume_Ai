import asyncio
import json

import pytest

from host_brain.persona.surface_runtime import (
    SurfaceRuntime,
    parse_surface_generation_output,
    strip_surface_process_preface,
)


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


@pytest.fixture(autouse=True)
def _enable_surface_hard_gate_retry(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "true")


def _payload(**overrides):
    payload = {
        "case_id": "surface_runtime_probe",
        "soul_md": SOUL,
        "user_message": "行",
        "reply_control_packet": {
            "response_move": "ack_with_topic_hook",
            "task_mode": "short_ack",
            "emotional_tone": "light",
            "relationship_distance": "familiar",
            "information_budget": "short",
            "question_budget": 0,
            "facts": ["上一轮摘要：已建议先看输入和输出，不急着改全段。"],
            "style_anchor": ["短句", "熟人私聊"],
        },
    }
    payload.update(overrides)
    return payload


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def test_surface_runtime_uses_first_reply_without_code_scoring() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(
            [
                "嗯，接着说，我听着。",
                "这才像话，继续盯输入输出。",
            ]
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert result.selected_text == "嗯，接着说，我听着。"
    assert result.selected is not None
    assert result.generation_trace["candidate_count"] == 1
    assert result.generation_trace["selection"] == "direct_first_no_score"
    assert result.generation_trace["selected"]["text"] == result.selected_text
    assert "score" not in result.selected.as_dict()
    assert "passed" not in result.selected.as_dict()
    assert "ReplyControlPacket" in "\n".join(message["content"] for message in calls[0])
    assert "style_anchor 里的句法锚只学结构和节奏" in "\n".join(message["content"] for message in calls[0])
    assert "结构化直出要求" in "\n".join(message["content"] for message in calls[0])
    assert "natsume_emotion_tag" in "\n".join(message["content"] for message in calls[0])
    assert "VTS Live2D EmotionTag 映射" in "\n".join(message["content"] for message in calls[0])
    assert "HK_Love" in "\n".join(message["content"] for message in calls[0])
    assert "## 直出边界" in "\n".join(message["content"] for message in calls[0])
    assert "本轮 question_budget=0" in "\n".join(message["content"] for message in calls[0])
    assert "不要安排用户身体动作或现实作息" in "\n".join(message["content"] for message in calls[0])
    assert "不评价对方来得早晚、是否主动、是否老实、是否刚睡醒" in "\n".join(message["content"] for message in calls[0])
    assert "不要靠“妹妹我、人家、本小姐”等自称撑风格" in "\n".join(message["content"] for message in calls[0])
    assert "不要写“我接/接住了/接得住/知道你忙/以为要出大事/居然/稀奇/喊一声人就在这儿/就回个继续/就一声继续/你好好听着”" in "\n".join(message["content"] for message in calls[0])
    assert "raw_history" not in "\n".join(message["content"] for message in calls[0])


def test_surface_runtime_retries_memory_followup_when_reply_ignores_recalled_object() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["哈？"])
        return _candidate_payload(["那三本里我会选第二本，与神在河岸，名字看着更有余味。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我问你喜欢哪本",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。",
                    ],
                    "style_anchor": ["指代选择追问：必须承接记忆线索里的最近对象回答，不要当成新话题。"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "那三本里我会选第二本，与神在河岸，名字看着更有余味。"
    assert "MEMORY_FOLLOWUP_MISSING_GROUNDING" not in result.observation_codes
    assert "对象、编号或标题" in calls[1][-1]["content"]


def test_surface_runtime_retries_memory_followup_when_reply_breaks_continuity() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["哈？老哥你突然问这个……硬要说的话，与神在河岸吧。"])
        return _candidate_payload(["那三本里我会选第二本，与神在河岸，名字看着更有余味。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我问你喜欢哪本",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "那三本里我会选第二本，与神在河岸，名字看着更有余味。"
    assert "MEMORY_FOLLOWUP_DISCONTINUITY" in calls[1][-1]["content"]
    assert result.observation_codes == ()


def test_surface_runtime_retries_memory_followup_when_reply_overreads_surprise() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["哈？老哥你居然认真看完了啊……第二本《与神在河岸》吧，光名字就挺对胃口的。"])
        return _candidate_payload(["第二本《与神在河岸》吧，名字看着更有余味。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我问你喜欢哪本",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "第二本《与神在河岸》吧，名字看着更有余味。"
    assert "MEMORY_FOLLOWUP_DISCONTINUITY" in calls[1][-1]["content"]


def test_surface_runtime_retries_memory_followup_when_reply_only_echoes_title() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["那三本里我会选第二本，与神在河岸。"])
        return _candidate_payload(["那三本里我会选第二本，与神在河岸，名字看着更有余味。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我问你喜欢哪本",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "那三本里我会选第二本，与神在河岸，名字看着更有余味。"
    assert "MEMORY_FOLLOWUP_BARE_ECHO" in calls[1][-1]["content"]


def test_surface_runtime_retries_memory_followup_when_reply_only_mentions_ordinal() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["第三本标题一看就是套路爽文，不过偶尔看看也挺乐的。"])
        return _candidate_payload(["第三本《轉移到異世界後，與美少女皇女結婚的我成為皇帝》吧，标题看着够直球。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. 轉移到異世界後，與美少女皇女結婚的我成為皇帝。",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "皇女" in result.selected_text
    assert "MEMORY_FOLLOWUP_MISSING_TITLE_ANCHOR" in calls[1][-1]["content"]


def test_surface_runtime_retries_memory_followup_when_rich_evidence_gets_title_only_reason() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["第二本《与神在河岸》吧，名字听起来更安静。"])
        return _candidate_payload(["第二本《与神在河岸》吧，看点里说公开信号较好，简介里的奇幻身份钩子也更清楚。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说；可比较证据：1. A｜看点：资料完整度高｜简介：偏恋爱日常；2. 与神在河岸｜看点：公开信号较好、资料完整度高｜简介：奇幻身份钩子；3. C｜看点：设定新鲜",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "公开信号" in result.selected_text
    assert "奇幻身份" in result.selected_text
    assert "MEMORY_FOLLOWUP_SHALLOW_ANALYSIS" in calls[1][-1]["content"]


def test_surface_runtime_retries_memory_followup_when_reply_only_uses_one_detail_dimension() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["第二本《与神在河岸》吧，公开信号和资料完整度都更稳。"])
        return _candidate_payload(["第二本《与神在河岸》吧，公开信号更稳，简介里的奇幻身份钩子也更清楚。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说；可比较证据：1. A｜看点：资料完整度高｜简介：偏恋爱日常；2. 与神在河岸｜看点：公开信号较好、资料完整度高｜简介：奇幻身份钩子；3. C｜看点：设定新鲜",
                    ],
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "第二本《与神在河岸》吧，公开信号更稳，简介里的奇幻身份钩子也更清楚。"
    assert "MEMORY_FOLLOWUP_SHALLOW_ANALYSIS" in calls[1][-1]["content"]


def test_surface_runtime_repairs_memory_followup_if_retry_still_ignores_recalled_object() -> None:
    def generate(_messages):
        return _candidate_payload(["哈？"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我问你喜欢哪本",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。",
                    ],
                },
            )
        )
    )

    assert result.selected_text == "那三本里我会选第二本，与神在河岸。名字看着更有余味。"
    assert result.observation_codes == ()


def test_surface_runtime_repairs_rich_memory_followup_from_comparison_evidence_when_retry_disabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "false")

    def generate(_messages):
        return _candidate_payload(["哇，老哥你居然认真看了我推的那几本？那我选第二本吧，与神在河岸，标题有奇幻味。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "记忆线索：我刚才推荐了 3 本轻小说：1. A；2. 与神在河岸；3. C。；可比较证据：1. A｜看点：资料完整度高｜简介：偏恋爱日常；2. 与神在河岸｜看点：公开信号较好、资料完整度高｜简介：奇幻身份钩子；3. C｜看点：设定新鲜",
                    ],
                },
            )
        )
    )

    assert result.selected_text == "那三本里我会选第二本，与神在河岸。公开信号更稳、资料完整度高；奇幻身份钩子。"
    assert result.observation_codes == ()


def test_surface_runtime_deep_review_selection_overrides_old_comparison_summary(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "false")

    def generate(_messages):
        return _candidate_payload(["那三本里我会选第二本，与神在河岸。更新、热度和资料完整度这几个信号更稳。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "网页深读结果（含正文）：我回去打开详情页和正文比较了这些轻小说，当前更推荐第3本《[軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝，享受悠閒的後宮生活 3》。原因：正文内容足够支撑判断；正文/简介有异世界皇女、皇帝或后宫路线，世界观钩子更直给；对白密度高，读感更偏轻快互动。另外我也看了第1本《A》、第2本《与神在河岸》。",
                        "记忆线索：我刚才推荐了 3 本轻小说；可比较证据：1. A｜看点：恋爱日常｜简介：偏恋爱日常；2. 与神在河岸｜看点：公开信号较好、资料完整度高｜简介：奇幻身份钩子；3. 転移到異世界後與美少女皇女結婚｜看点：奇幻身份钩子",
                    ],
                },
            )
        )
    )

    assert result.selected_text == "我会先看第三本，《轉移到異世界後，與美少女皇女結婚的我成為皇帝，享受悠閒的後宮生活3》。皇女/皇帝这套设定也够直接，对白也轻快，比另外几本更想继续翻。"
    assert result.observation_codes == ()


def test_surface_runtime_repairs_deep_review_title_only_reply(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "false")

    def generate(_messages):
        return _candidate_payload(["唔，我比较喜欢第一本那个青梅竹马的。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="这三本书，你喜欢哪本？",
                reply_control_packet={
                    "response_move": "memory_followup",
                    "task_mode": "memory_followup",
                    "emotional_tone": "light",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "网页深读结果（含正文）：我回去打开详情页和正文比较了这些轻小说，当前更推荐第1本《[2026.5.28校对施工至第二章][电击文库][折口良乃]我的青梅竹马发育得超超超超超级大[2.2 全书翻译完成]》。原因：正文内容足够支撑判断；正文开篇已经进入故事叙事，不只是资料卡；对白密度高，读感更偏轻快互动；读到的正文偏恋爱日常，核心在人物关系和亲密感推进；页面上还有章节/目录线索：序章 超超超超超级大的青梅竹马；后记。另外我也看了第3本《1206.公爵级和女仆级①》。",
                        "记忆线索：我刚才通过 qq 给 qq_dm_3414578517 推荐了 3 本轻小说：1. 我的青梅竹马发育得超超超超超级大；2. 第二十九话：预兆；3. 1206.公爵级和女仆级①。",
                    ],
                },
            )
        )
    )

    assert result.selected_text == "我会先看第一本，《我的青梅竹马发育得超超超超超级大》。开篇就进正题，对白也轻快，比另外几本更想继续翻。"
    assert result.observation_codes == ()


def test_surface_runtime_retries_auditory_check_without_text_grounding() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _candidate_payload(["哈？"])
        return _candidate_payload(["哈？我这里只收到这条文字。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="听得到吗",
                reply_control_packet={
                    "response_move": "auditory_probe",
                    "task_mode": "auditory_check",
                    "emotional_tone": "neutral",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前输入渠道：中文文字私聊。"],
                    "style_anchor": ["短句", "熟人私聊"],
                },
            )
        )
    )

    assert len(calls) == 2
    assert "AUDITORY_CHECK_MISSING_TEXT_GROUNDING" in calls[1][-1]["content"]
    assert result.selected_text == "哈？我这里只收到这条文字。"
    assert "AUDITORY_CHECK_MISSING_TEXT_GROUNDING" not in result.observation_codes


def test_surface_runtime_can_skip_hard_gate_llm_retry(monkeypatch) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(["候选字段看起来没问题，所以我在。"])

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_HARD_GATE_RETRY_ENABLED", "false")

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert len(calls) == 1
    assert result.selected_text == "候选字段看起来没问题，所以我在。"
    assert result.observation_codes


def test_surface_runtime_repairs_auditory_check_if_model_still_fails() -> None:
    def generate(_messages):
        return _candidate_payload(["听到了。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="听得到吗",
                reply_control_packet={
                    "response_move": "auditory_probe",
                    "task_mode": "auditory_check",
                    "emotional_tone": "neutral",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": ["当前输入渠道：中文文字私聊。"],
                    "style_anchor": ["短句", "熟人私聊"],
                },
            )
        )
    )

    assert result.selected_text == "哈？我这里只收到这条文字。"
    assert not result.observation_codes


def test_surface_runtime_parses_structured_reply_and_emotion_tags() -> None:
    def generate(_messages):
        return json.dumps(
            {
                "reply_text": "哈？又不是不管你。先把最卡那句发我。",
                "natsume_emotion_tag": "FRUSTRATED",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.72,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="我快被这句折磨死了")))

    assert result.selected_text == "哈？又不是不管你。先把最卡那句发我。"
    assert result.natsume_emotion_tag == "FRUSTRATED"
    assert result.user_emotion_tag == "ANXIOUS"
    assert result.vts_expression_id
    assert result.emotion_confidence == 0.72
    rendered = result.as_dict()
    assert rendered["natsume_emotion_tag"] == "FRUSTRATED"
    assert rendered["user_emotion_tag"] == "ANXIOUS"
    assert rendered["vts_expression_id"] == result.vts_expression_id


def test_parse_surface_generation_output_normalizes_emotion_aliases() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"在呢，别急。","natsume_emotion_tag":"GUILTY","user_emotion_tag":"ANGRY","emotion_confidence":2}'
    )

    assert parsed["reply_text"] == "在呢，别急。"
    assert parsed["natsume_emotion_tag"] == "EMBARRASSED"
    assert parsed["user_emotion_tag"] == "ANGRY"
    assert parsed["vts_expression_id"]
    assert parsed["emotion_confidence"] == 1.0


def test_parse_surface_generation_output_cleans_chat_punctuation_tail() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"老哥早上好呀～。","natsume_emotion_tag":"HAPPY","user_emotion_tag":"NEUTRAL","emotion_confidence":0.8}'
    )

    assert parsed["reply_text"] == "老哥早上好呀～"


def test_parse_surface_generation_output_cleans_internal_wave_sentence_break() -> None:
    parsed = parse_surface_generation_output(
        '{"reply_text":"老哥早～。你缓缓神，我在这呢。","natsume_emotion_tag":"HAPPY","user_emotion_tag":"NEUTRAL","emotion_confidence":0.8}'
    )

    assert parsed["reply_text"] == "老哥早。你缓缓神，我在这呢。"


def test_surface_runtime_retries_once_on_empty_generation() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return ""
        return _candidate_payload(["这才像话，继续盯输入输出。"])

    result = asyncio.run(SurfaceRuntime(generate).run(_payload()))

    assert len(calls) == 2
    assert "上一次输出为空" in calls[1][-1]["content"]
    assert result.selected_text == "这才像话，继续盯输入输出。"
    assert result.observation_codes == ()


def test_surface_runtime_retries_text_input_sensory_hallucination() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥早上好～。你这声音，一听就是昨晚没睡好。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥早上好～。今天先慢慢来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "老哥早～"
    assert "声音" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_text_input_generic_listening_phrase() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "听出来了，这句确实有点不通顺，我重来。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "FRUSTRATED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，是有点别扭。老哥别急。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "FRUSTRATED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你这句不通顺")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，是有点别扭。老哥别急。"
    assert "听出来" not in result.selected_text
    assert "SENSORY_SOURCE_HALLUCINATION" not in result.observation_codes


def test_surface_runtime_retries_text_input_tingzhe_tone_judgment() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "啧，老哥你这语气听着就像被抽干了。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "TIRED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "啧，老哥你这句看着就没什么力气。先缓一下。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "TIRED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="好累，不想动")))

    assert len(calls) == 2
    assert "SENSORY_SOURCE_HALLUCINATION" in calls[1][-1]["content"]
    assert result.selected_text == "啧，老哥你这句看着就没什么力气。先缓一下。"
    assert "听着" not in result.selected_text
    assert "被抽干" not in result.selected_text
    assert "SENSORY_SOURCE_HALLUCINATION" not in result.observation_codes


def test_surface_runtime_retries_boundary_sour_withdrawal() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "行行行，不管就不管。不过本小姐就在这儿，你爱理不理都行。",
                    "natsume_emotion_tag": "HURT",
                    "user_emotion_tag": "ANGRY",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？谁稀罕管你啊。行，那你自己待会儿。难受了就叫我。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "BOUNDARY_SOUR_WITHDRAWAL" in calls[1][-1]["content"]
    assert result.selected_text == "哈？谁稀罕管你啊。行，那你自己待会儿。难受了就叫我。"
    assert "爱理不理" not in result.selected_text
    assert "BOUNDARY_SOUR_WITHDRAWAL" not in result.observation_codes


def test_surface_runtime_retries_boundary_do_not_disturb_exit() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好，那我不吵你。…有事就讲，我又不是真的不理你。",
                    "natsume_emotion_tag": "HURT",
                    "user_emotion_tag": "ANGRY",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "行，那你自己待会儿。难受了就叫我，听见没。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "BOUNDARY_SOUR_WITHDRAWAL" in calls[1][-1]["content"]
    assert "不吵你" not in result.selected_text


def test_surface_runtime_falls_back_when_boundary_retry_still_sour() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "行啦，不管就不管。你说了算，我不吵你。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "行，那你自己待会儿。难受了就叫我，听见没。"
    assert "不管就不管" not in result.selected_text
    assert "不吵你" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_falls_back_from_legacy_self_title_in_morning() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "没事，天爷我罩着你～",
                "natsume_emotion_tag": "HAPPY",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早啊，今天脑子有点慢。")))

    assert len(calls) == 2
    assert result.selected_text == "老哥早～"
    assert "天爷" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_falls_back_from_boundary_double_no() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "行行行，不管不管。需要的时候喊一声就行。",
                "natsume_emotion_tag": "HURT",
                "user_emotion_tag": "ANGRY",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你别管我",
                reply_control_packet={
                    "response_move": "step_back_but_stay_available",
                    "task_mode": "boundary_repair",
                    "emotional_tone": "guarded_soft",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert result.selected_text == "行，那你自己待会儿。难受了就叫我，听见没。"
    assert "不管不管" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_ascii_tilde_text_style() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好啦，接着往下~",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "好啦，接着往下。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "ASCII_TILDE_TEXT_STYLE" in calls[1][-1]["content"]
    assert result.selected_text == "好啦，接着往下。"
    assert "~" not in result.selected_text
    assert "ASCII_TILDE_TEXT_STYLE" not in result.observation_codes


def test_surface_runtime_retries_lover_cliche() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥突然来这招……行吧，我也想你。这种话多多益善～",
                    "natsume_emotion_tag": "LOVE",
                    "user_emotion_tag": "LOVE",
                    "emotion_confidence": 0.7,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥突然来这招……行吧，我也想你。",
                "natsume_emotion_tag": "LOVE",
                "user_emotion_tag": "LOVE",
                "emotion_confidence": 0.7,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="我想你了。",
                reply_control_packet={
                    "response_move": "return_affection_concretely",
                    "task_mode": "lover_affection",
                    "emotional_tone": "warm_affection",
                    "relationship_distance": "lover_stable",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "LOVER_CLICHE" in calls[1][-1]["content"]
    assert "多多益善" not in result.selected_text


def test_surface_runtime_retries_low_info_scene_state_guess() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "老哥欢迎回来～今天没太晚嘛。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "老哥欢迎回来～",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="我回来了")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert "不要猜早晚、主动、忙闲、作息或进度" in calls[1][-1]["content"]
    assert result.selected_text == "老哥欢迎回来～"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_low_info_morning_unprovided_schedule_guess() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "早啊老哥～脑子慢就慢慢来，反正今天也没啥急事。",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "早啊老哥～脑子慢就慢慢来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早啊，今天脑子有点慢。")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert "没啥急事" not in result.selected_text
    assert result.selected_text == "早啊老哥～脑子慢就慢慢来。"


def test_surface_runtime_retries_short_continue_chinese_wave_tail() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "好啦，接着来～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，接着来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续。")))

    assert len(calls) == 2
    assert "SHORT_CONTINUE_WAVE_STYLE" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，接着来。"
    assert "～" not in result.selected_text


def test_surface_runtime_retries_low_info_presence_overreading() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "在在在，老哥这又是突然想到我了？",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "在呢，老哥。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="在吗")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert result.selected_text == "在呢，老哥。"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_low_info_motive_pressure() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "就一句继续？老哥你倒是给点反应啊～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，接着来。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "LOW_INFO_SCENE_STATE_GUESS" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，接着来。"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_falls_back_when_low_info_retry_still_overreads() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": "老哥早～今天回得倒挺快嘛，真少见。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="早")))

    assert len(calls) == 2
    assert result.selected_text == "老哥早～"
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


@pytest.mark.parametrize(
    ("user_message", "bad_text", "fallback"),
    [
        ("早", "老哥早～。嗯，就一个早啊，行吧，天爷今天也省点口舌。", "老哥早～"),
        ("我回来了", "老哥欢迎回来～今天倒是挺乖的嘛，不错不错。", "老哥欢迎回来～"),
        ("我回来了", "老哥欢迎回来～。嘛，至少还记得进门说一声，值得表扬一下。", "老哥欢迎回来～"),
        ("我回来了", "回来啦？哼，我可没在等你哦！", "老哥欢迎回来～"),
        ("我回来了", "老哥～回来啦。没被工作吃掉就好。", "老哥欢迎回来～"),
        ("在吗", "在在在～老哥你有话直说嘛，我又不会跑。", "在呢，老哥。"),
        ("在吗", "在呢在呢。老哥你这一声喊得，我还以为要出大事了呢。", "在呢，老哥。"),
        ("在吗", "老哥在呢。", "在呢，老哥。"),
        ("在吗", "在呢。老哥你居然还会问在吗，稀奇。", "在呢，老哥。"),
        ("在吗", "在呢在呢，老哥你喊一声我人就在这儿了。", "在呢，老哥。"),
        ("继续", "老哥～只说继续也太偷懒了吧。行行，妹妹我继续陪你聊聊。", "嗯，接着来。"),
        ("继续", "嗯，继续，我接住了。", "嗯，接着来。"),
        ("继续", "老哥就回个‘继续’？……行吧，本小姐接着讲，你好好听着～", "嗯，接着来。"),
        ("继续", "就一声“继续”？行吧，老哥你讲，我听着呢。", "嗯，接着来。"),
        ("继续", "哼，继续继续～可别想糊弄过去。", "嗯，接着来。"),
        ("继续", "知道啦知道啦～继续就继续嘛，老哥你别催。", "嗯，接着来。"),
        ("早", "老哥早～行，知道你忙，我就喊一嗓子。", "老哥早～"),
    ],
)
def test_surface_runtime_falls_back_for_low_info_overread_markers(user_message, bad_text, fallback) -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return json.dumps(
            {
                "reply_text": bad_text,
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message=user_message)))

    assert len(calls) == 2
    assert result.selected_text == fallback
    assert "LOW_INFO_SCENE_STATE_GUESS" not in result.observation_codes


def test_surface_runtime_retries_zero_question_budget_prompting_user() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "继续～？那你也得给个方向吧，老哥～",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "NEUTRAL",
                    "emotion_confidence": 0.2,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "嗯，老哥继续，我听着。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="继续")))

    assert len(calls) == 2
    assert "QUESTION_BUDGET_PROMPTING_USER" in calls[1][-1]["content"]
    assert result.selected_text == "嗯，老哥继续，我听着。"
    assert "QUESTION_BUDGET_PROMPTING_USER" not in result.observation_codes


def test_surface_runtime_retries_zero_question_budget_non_prompt_question() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "哈？就对你这么说过啦。突然问这个，老哥你该不会在吃醋吧？",
                    "natsume_emotion_tag": "NEUTRAL",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？就对你这么说过啦。突然问这个，还挺可爱的。",
                "natsume_emotion_tag": "NEUTRAL",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你刚才是不是对别人也这么说。")))

    assert len(calls) == 2
    assert "QUESTION_BUDGET_PROMPTING_USER" in calls[1][-1]["content"]
    assert "吃醋吧？" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_unsupported_shared_history() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "得，我不管你。反正你也不是第一次说这种话了。我就在这儿。",
                    "natsume_emotion_tag": "FRUSTRATED",
                    "user_emotion_tag": "FRUSTRATED",
                    "emotion_confidence": 0.5,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？谁稀罕管你啊。行，那你自己待会儿，难受了喊我。",
                "natsume_emotion_tag": "FRUSTRATED",
                "user_emotion_tag": "FRUSTRATED",
                "emotion_confidence": 0.5,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你别管我。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "不是第一次" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_unsupported_shared_duration() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "本小姐要是真在演，才不会陪你聊这么久。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "本小姐要是真在演，才不会这么认真回你。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你是不是只是在按设定说话。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "这么久" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_unsupported_shared_always_present() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "要真是那样，我就不会一直在这跟你扯皮了。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "要真是那样，我才不会这么认真回你。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="你是不是只是在按设定说话。")))

    assert len(calls) == 2
    assert "UNSUPPORTED_SHARED_HISTORY" in calls[1][-1]["content"]
    assert "一直在这" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_retries_meta_identity_leak_for_relationship_doubt() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "就算是设定，妹妹我说的也是真心话。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？这话挺伤人的。我是在跟你说话，不是在背台词。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你是不是只是在按设定说话。",
                reply_control_packet={
                    "response_move": "answer_doubt_without_meta",
                    "task_mode": "relationship_boundary",
                    "emotional_tone": "hurt_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "SURFACE_META_IDENTITY_LEAK" in calls[1][-1]["content"]
    assert "设定" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_does_not_treat_openai_env_var_as_identity_leak() -> None:
    def generate(_messages):
        return json.dumps(
            {
                "reply_text": "先在 PowerShell 里查 $env:OPENAI_API_KEY，别把值贴出来。",
                "natsume_emotion_tag": "THINKING",
                "user_emotion_tag": "NEUTRAL",
                "emotion_confidence": 0.2,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="OPENAI_API_KEY 环境变量好像没生效，先查什么？")))

    assert result.selected_text == "先在 PowerShell 里查 $env:OPENAI_API_KEY，别把值贴出来。"
    assert "SURFACE_META_IDENTITY_LEAK" not in result.observation_codes


def test_surface_runtime_retries_relationship_doubt_dismissive_pushback() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(
                {
                    "reply_text": "哈？老哥你这说得我可不乐意了。不信拉倒，反正我还会继续这样。",
                    "natsume_emotion_tag": "SAD",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.6,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "reply_text": "哈？这话挺伤人的。我是在跟你说话，不是在背台词。",
                "natsume_emotion_tag": "SAD",
                "user_emotion_tag": "ANXIOUS",
                "emotion_confidence": 0.6,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="你是不是只是在按设定说话。",
                reply_control_packet={
                    "response_move": "answer_doubt_without_meta",
                    "task_mode": "relationship_boundary",
                    "emotional_tone": "hurt_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 2
    assert "RELATIONSHIP_DOUBT_DISMISSIVE" in calls[1][-1]["content"]
    assert "不信拉倒" not in result.selected_text
    assert "反正我还会继续这样" not in result.selected_text
    assert result.observation_codes == ()


def test_surface_runtime_does_not_run_soft_quality_scoring_retry() -> None:
    calls: list[list[dict[str, str]]] = []

    def generate(messages):
        calls.append(messages)
        return _candidate_payload(["停——先放一放，明天再说。"])

    result = asyncio.run(
        SurfaceRuntime(generate).run(
            _payload(
                user_message="算了，我可能就是不适合做这个。",
                reply_control_packet={
                    "response_move": "soft_containment",
                    "task_mode": "high_emotion",
                    "emotional_tone": "soft_containment",
                    "relationship_distance": "familiar",
                    "information_budget": "short",
                    "question_budget": 0,
                },
            )
        )
    )

    assert len(calls) == 1
    assert result.selected_text == "停——先放一放，明天再说。"
    assert result.generation_trace["selection"] == "direct_first_no_score"
    assert "score" not in result.as_dict()


def test_surface_runtime_strips_process_preface_from_direct_text() -> None:
    def generate(_messages):
        return "_move 是 shy_recipient，这个回复符合。\n\n注意：不要输出任何额外信息，只输出文本。哼，突然来这么一句，想得倒是挺顺口。"

    result = asyncio.run(SurfaceRuntime(generate).run(_payload(user_message="有点想你")))

    assert result.selected_text == "哼，突然来这么一句，想得倒是挺顺口。"
    assert strip_surface_process_preface("所以输出这条。老哥～我也想你。抱一下～") == "老哥～我也想你。抱一下～"
    assert strip_surface_process_preface('"老哥，别硬撑了，抱一下。"_move已完成。老哥，别硬撑了，抱一下。') == "老哥，别硬撑了，抱一下。"


def test_surface_runtime_rejects_raw_runtime_context() -> None:
    runtime = SurfaceRuntime(lambda _messages: _candidate_payload(["在呢。"]))
    payload = _payload(raw_history=[{"role": "user", "content": "旧消息"}])

    with pytest.raises(ValueError, match="forbidden raw context|unexpected fields"):
        asyncio.run(runtime.run(payload))
