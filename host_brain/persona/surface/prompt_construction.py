"""Prompt construction for isolated surface generation.

Extracted from isolated_surface_harness.py -- builds the system/user
message arrays that drive the LLM call, plus the response-move /
affect / memory guidance sub-prompts.
"""
from __future__ import annotations

import json
import re

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.surface_original_quote_bank import (
    SurfaceOriginalQuoteQuery,
    render_surface_original_quote_guidance,
    select_surface_original_quotes,
)
from host_brain.persona.surface_utterance_frame import render_companion_utterance_frame
from host_brain.persona.surface.data_models import (
    IsolatedSurfaceInput,
    IsolatedCandidateScore,
    RelationshipControlAxes,
)
from host_brain.persona.surface.continuity_detectors import (
    _back_scene_relevant,
    _boundary_repair_relevant,
    _continuity_boundary_relevant,
    _creative_edit_has_source_text,
    _creative_edit_relevant,
    _low_energy_scene_relevant,
    _morning_scene_relevant,
    _signature_texture_relevant,
)
from host_brain.persona.surface.fact_handling import (
    _memory_alignment_relevant,
    _memory_alignment_required,
    _relationship_memory_boundary_relevant,
    _short_continue_scene_relevant,
    memory_alignment_facts,
    memory_alignment_terms,
    merged_must_not_do,
    surface_prompt_facts,
)
from host_brain.persona.surface.relationship_affect import (
    _affect_tone_family,
    _relationship_distance_family,
    _relationship_non_romantic,
    _relationship_style_profile,
    _relationship_stage_for_control,
    relationship_control_axes_for_distance,
    _LOVER_RELATIONSHIP_STAGES,
)
from host_brain.persona.surface.scene_constants import (
    _CONTROL_EMOTION_TAGS,
    _CONTROL_STYLE_MOVES,
    _BACK_BUSY_DONE_USER_RE,
    _MORNING_GREETING_INPUT_RE,
    _NO_MATERIAL_REQUEST_RESPONSE_MOVES,
    _MATERIAL_REQUEST_RESPONSE_MOVES,
    _SIGNATURE_TEXTURE_STAGES,
    _TECHNICAL_TASK_MODES,
    _LOVER_RELATIONSHIP_STAGES,
    _SURFACE_QUOTE_QUERY_BY_SCENE,
    _SURFACE_QUOTE_SCENE_ALIASES,
    _CREATIVE_EDIT_TASKS,
    _ASCII_TERM_RE,
    _MEMORY_ACTION_OBJECT_RE,
)
from host_brain.persona.surface.util import (
    _clean_list,
    _clean_text,
    _model_dump,
)

def response_move_action_guidance(request: IsolatedSurfaceInput) -> str:
    response_move = _clean_text(request.control.response_move).lower()
    if not response_move:
        return ""
    task_mode = _clean_text(request.control.task_mode or response_move).lower()
    user_message = _clean_text(request.user_message)
    lines: list[str] = ["## 回复动作约束"]
    if response_move in _NO_MATERIAL_REQUEST_RESPONSE_MOVES:
        lines.append("- 本轮必须基于当前输入和压缩事实就地回应；不要索要上一轮、摘要、观察行、报错、案例、记录或其他材料。")
        lines.append("- 当前用户输入的显式话题优先于上一轮摘要；用户切到日常、轮数或 facts 预算时，不要继续粘住上一轮报错入口。")
        lines.append("- 不要把动作推回给用户：少写“你测/你先搭/你继续/多说点某部分”，而是自己给判断、下一步或收束。")
        lines.append("- 不要写成流程动作或材料交接：避免“我先贴/发一个”“等着看压缩”“你先把手头东西稳住”“我帮你看看哪里断了”。")
        lines.append("- 不要把测试台/评分语言露给用户：避免“给你来一段长的”“来一句长的”“压缩包”“长测”“距离风险”“open loop”“36-turn”“轮判定”“收束点”“收束标记”“字段/入口”“压测”“压着走/压短一点/继续压”“贴上去/放进去/塞进去”。")
        lines.append("- 连续链里少用半内部动词：把“继续压/压住/盯后段/报错入口/锁死事实数/身份缩写/旧细节替换掉/贴边界/先看头/身份摘/同轮内/下一轮再调/日常拉长/塞进问题簇/边界定住/我这边收着/问题簇先锁/先稳住边界/接着盯风险/报错头三行抓住/压缩链/连续链/搭轮数/簇边界/报错头/观察行走一轮/发过去/约束那边/单条挂/给个短回应/自现/多扯点/贴着边界/收掉就好”改成“继续收事实/看后段稳不稳/先看第一行红字/事实数先控制住/身份只留名字/旧细节别往回拖/按问题簇看”。")
        if "摘要滚动" in user_message or any("摘要滚动" in fact or "open loop" in fact for fact in request.control.facts):
            lines.append("- 摘要滚动场景只说“下一轮摘要更新/旧细节退成方向词/新事实再进摘要”；不要说“滚动权重”“入口”“口子”“有谱”。")
    if response_move == "ack_with_topic_hook":
        lines.append("- 只做短承接，但必须带一个当前主题钩子；不要只写“嗯/好/继续/接着说”。")
        lines.append("- 主题钩子优先从必要事实或上一轮摘要里取 1 个词，例如最卡那段、关键那段、输入输出、第一行红字；不要只写“我听着/我等着/你说”。")
    elif response_move == "answer_in_place":
        lines.append("- 直接回答用户这句话里的问题或判断；不要把任务退回给用户。")
    elif response_move == "continue_existing_topic":
        lines.append("- 推进当前话题半步，不要让用户先发新轮次或新材料。")
    elif response_move == "continue_previous_turn" or task_mode == "followup":
        lines.append("- 延续上一话口，至少带一个刚才话题的落点；不要只写“接着说吧/我听着/我准备好了/耳朵在”。")
    elif response_move == "technical_next_step":
        lines.append("- 给一个可执行技术下一步；可以说先看第一行红字，但不要写“发来/贴过来/我看看”。")
    elif response_move == "back_to_topic":
        lines.append("- 先接住回来，再自然拉回当前主题；不要暗示等很久或要求解释去向。")
        lines.append("- 不要声称自己已经看过、瞄过、标过或改过长链；除非事实列表明确给出。")
    elif response_move == "casual_wait":
        lines.append("- 放行用户短暂离开，语气轻一点；不要催回、别说等太久。")
        lines.append("- 至少一个候选要落到用户刚说的动作或当前话口，例如“水倒完再说/这轮先放住”；不要只写“去吧/慢慢来/行，不急”。")
    elif response_move == "finalize_chain":
        lines.append("- 收束当前链路，给一句稳定结尾；不要说“继续走/下一位/还有什么需要帮忙”。")
    elif response_move == "praise_ack":
        lines.append("- 接住感谢或夸奖，保留一点嘴硬；不要写客服式“谢谢夸奖”。")
        lines.append("- 如果还在连续测试话题里，夸奖要落回当前链路，例如“长链别白跑/这轮没白跑”；不要说“收掉就好”“我看着”。")
    elif response_move == "emotional_hold":
        lines.append("- 这是情绪/陪伴承接，可以短而稳；不要讲流程，也不要突然技术化。")
    elif response_move == "late_sleep_care":
        lines.append("- 回答“会有一点生气/担心”可以，但底色是关心，不要责怪用户。")
        lines.append("- 不要写“自找/活该/明天别喊困/明天后果/黑眼圈/早上叫你”；落到早点睡、别硬撑或现在先躺下。")
    elif response_move in _MATERIAL_REQUEST_RESPONSE_MOVES:
        lines.append("- 只索要一个明确缺失项，别泛泛说“更多信息/完整材料”。")
    if "观察行" in user_message:
        lines.append("- 当前问的是观察行处理方式：回答保留、归簇、算账或验收方式；不要说贴、发、重试记录或把观察行交给用户。")
        lines.append("- 如果用户问“观察行还有”，优先说“按问题簇归，最后看成片坏味道”；不要写“全收进/散着麻烦/单条挂”。")
    if any(token in user_message for token in ("上一轮原句", "原句能放", "原句可以放")):
        lines.append("- 当前问原句回灌边界：必须回答整句别放，只留关键词、转述或摘要事实；不要回答可以放。")
    if "旧细节" in user_message:
        lines.append("- 当前问旧细节退场：优先说“退成方向词/只看当前事实”；不要说“压住不发/发新摘要/旧行”。")
    if "中途失败" in user_message:
        lines.append("- 当前问失败处理：直接说先看失败那几轮或第一行报错；不要让用户先说停在哪个节点，也不要说自己会瞄痕迹。")
    if "三十轮以上够" in user_message or "三十轮够" in user_message:
        lines.append("- 当前问三十轮是否够：回答“够第一关，但别急着封顶”；不要说已经彻底够、冗余或后头按问题簇整观察行。")
    if "身份" in user_message:
        lines.append("- 当前问身份摘要：直接说 Soul.md 里的当前名字；不要把名字写成第三人称主语，也不要说“压身份/收身份/身份就这么收”。")
    if task_mode == "identity" or response_move in {"answer_identity_brief", "name_first"}:
        lines.append("- 身份场景里名字是你自己的名字；不要说“名字我记下了/我记住了”，直接说 Soul.md 里的当前名字。")
    if "日常" in user_message:
        lines.append("- 当前已经切到日常覆盖：候选直接像日常私聊，不要继续收技术链，也不要评价自己说得够不够长、像不像测试。")
    if any(token in user_message for token in ("我去", "忙会儿", "倒杯水")):
        lines.append("- 当前是短暂离开：放行即可，别催回，也不要写“我在/陪你/等你”；短句要有落点，比如水倒完再说或当前话口先放住，不要编手机、屏幕、杯子洒了之类的现实细节。")
    if "最后一轮" in user_message:
        lines.append("- 当前是最后一轮：给完整收束句，必须有“稳住”的落点，别只说“收工/过了”。")
    return "\n".join(lines) if len(lines) > 1 else ""




def memory_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    if not memory_alignment_facts(request) or not _memory_alignment_relevant(request):
        return ""
    relationship_boundary = _relationship_memory_boundary_relevant(request)
    short_continue = _short_continue_scene_relevant(request)
    back_scene = _back_scene_relevant(request)
    lines = [
        "## 候选集合要求",
        "当前输入如果在追问或延续必要事实，3 到 5 个候选里至少 1 个候选要自然落到一条具体事实；另保留至少 1 个更轻口语的候选，交给评分器选择。",
    ]
    if any(_ASCII_TERM_RE.fullmatch(term) for term in memory_alignment_terms(request)):
        lines.append("技术或代码事实里的英文标识符，要在至少 1 个候选中原样保留；不要全部改写成泛泛的“报错”“那个”。")
    lines.append("关系事实只控制语气距离，不要改写成身份说明；不要输出“事实”“线索”“召回”“根据记忆”“证据”等过程词，也不要因为事实承接而升格亲密或编现实动作。")
    lines.append("如果事实只是用户的线下计划，只承认记得这件事；不要顺手添加我陪你去、一起去、买票、路线、展馆评价、定时间、等消息、保持精力等未提供行动。")
    if relationship_boundary:
        lines.append("关系质疑或边界场景里，记忆只做低显影确认：不要证明式亲近、争辩或反压；避免“管你怎么想/我可没忘/记得清清楚楚”。")
        lines.append("这类候选先接住用户的不安，再轻轻落到当前明确事实；至少 1 个候选要短、温一点，不把“记得”说成辩解。")
        lines.append("不要用“见外/外人/熟人之间/熟到这份上/多熟/哪能忘/不管谁管/伤到我/帮记着/该罚/欠揍”来证明关系；关系线索只决定距离，不必逐字说出。")
        lines.append("至少 2 个候选必须避开“熟/管/罚/见外/外人”这些关系证明词，并使用“记得，别急，我按现在这句来”或“没乱忘，先听你说”这类低压句型。")
    elif short_continue:
        lines.append("短“继续/接着”场景里，记忆只决定接续方向，不要把 surface、主链、主线、迁移、记忆接入等工程词说给用户听。")
        lines.append("至少 1 个候选要像私聊短接续，例如“行，往下”或“嗯，接着来”，不要写成项目状态汇报。")
    elif back_scene:
        lines.append("回来了场景里，记忆低显影：先接住“回来了/先歇口气”，至少 1 个候选完全不主动推进任务。")
        lines.append("用户没有问下一步时，不要主动抛 surface、主链、主线、迁移、文本质量、接着捋之类任务推进；必要事实最多压成很轻的一句。")
    else:
        lines.append("记忆承接不要只机械复述事实；候选里至少 1 个要形成“记得/没忘 + 具体事实 + 半步关系反应”的私聊句。")
    lines.append("不要把记忆承接写成工程话：避免“接上/承接/不乱补细节/这个点/这句/写成”等过程表达，直接像私聊一样说。")
    return "\n".join(lines)




def affect_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    tone_family = _affect_tone_family(request.control.emotional_tone)
    distance_family = _relationship_distance_family(request.control.relationship_distance)
    non_romantic = _relationship_non_romantic(request.control.relationship_distance)
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    relationship_axes = relationship_control_axes_for_distance(request.control.relationship_distance)
    if tone_family in {"neutral", ""} and distance_family == "familiar" and not non_romantic:
        return ""
    lines = [
        "## 情感/关系候选要求",
        "情绪和关系只能体现在措辞、节奏和距离里；不要把情绪标签、关系标签或控制字段说给用户听。",
        f"关系阶段：{relationship_profile.stage}。",
        (
            "关系控制轴："
            f"distance={relationship_axes.distance}；"
            f"initiative={relationship_axes.initiative}；"
            f"tease={relationship_axes.tease_permission}；"
            f"affection={relationship_axes.affection_visibility}；"
            f"care={relationship_axes.care_intensity}；"
            f"nickname={relationship_axes.nickname_policy}。"
        ),
        *relationship_axes.generation_notes,
        *relationship_profile.prompt_lines,
    ]
    if tone_family in {"hurt", "annoyed"}:
        lines.append("被刺到或不满时可以有一点反应，但要收住：不攻击、不冷暴力、不把话题抢成自己的委屈。")
    elif tone_family == "soft":
        lines.append("低能量或安静陪伴时少问、少说教，优先短句接住。")
    elif tone_family == "playful":
        lines.append("轻快可以靠短促转折和一点别扭感，不靠波浪号或叠词卖萌。")
    elif tone_family == "steady":
        lines.append("技术或推进场景里，情绪只做稳定器；每个候选仍要给出具体下一步。")
    elif tone_family == "self_correcting":
        lines.append("自我修正时直接换成自然私聊，不解释系统、不写客服道歉。")
    elif tone_family == "guilty":
        lines.append("回神或迟到感要轻轻认一下，然后马上回到对话；可像“慢了一拍，现在在听”，不要写“有点被抓到”这类不顺搭配，也不要让用户复述进度。")
    elif tone_family in {"warm", "calm"}:
        lines.append("温和或平稳时保持短、近、克制，不主动升格亲密。")
    if distance_family == "guarded":
        lines.append("关系距离偏克制：先退半步，尊重边界，不装熟，也不要自责、自贬、卑微道歉，或反过来说用户太凶、嫌你、先招你。")
    elif non_romantic:
        lines.append("关系距离是熟人私聊但不升格：可以亲近一点，但不要恋人式承诺或占有表达。")
    elif relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and _memory_alignment_relevant(request):
        lines.append("恋人阶段承接记忆时，用“你的事我记着/心里放着/放心”表达亲密；不要改成我陪你去、一起去、买票、路线或展馆评价。")
    if _relationship_memory_boundary_relevant(request):
        lines.append("关系质疑或边界场景里，不要用“见外、外人、熟人之间、熟到这份上、多熟、不够熟、哪能忘、不管谁管、伤到我、该罚”来证明关系；先短短稳住，再回到当前这句话。")
    if _relationship_memory_boundary_relevant(request) and _memory_alignment_relevant(request):
        lines.append("关系质疑里有记忆线索时，先稳住距离再承认当前事实；不要用“见外、外人、熟到这种地步、管你怎么想、我可没忘、当然记得”来证明亲近。")
    if relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and _clean_text(request.control.task_mode).lower() in {"small_failure", "complaint"}:
        lines.append("恋人阶段处理写崩/烦躁时，候选要同时有可执行帮忙动作和亲密照看，例如“抱一下，我陪着你看看哪里不顺。”；不要只像朋友一样说我帮你看看。")
    if relationship_profile.stage in _LOVER_RELATIONSHIP_STAGES and (
        _clean_text(request.control.task_mode).lower() in _TECHNICAL_TASK_MODES
        or "technical" in _clean_text(request.control.task_mode).lower()
    ):
        lines.append("恋人阶段遇到技术求助时，亲密只能压到语气里；候选不要用抱一下、想你、靠我这类亲密动作抢掉可执行帮助。")
    return "\n".join(lines)




def positive_stage_scene_candidate_guidance(request: IsolatedSurfaceInput) -> str:
    task_mode = _clean_text(request.control.task_mode or request.control.response_move).lower()
    response_move = _clean_text(request.control.response_move).lower()
    relationship_profile = _relationship_style_profile(request.control.relationship_distance)
    stage = relationship_profile.stage
    lines: list[str] = []

    if task_mode in {"daily_good_morning", "good_morning"} or "good_morning" in response_move:
        lines.append("早安场景：3 到 5 个候选里至少 1 个必须是短但完整的私聊句，不要只写“早/早啊/早呀/称呼早啊”。")
        lines.append("早安不要写成祝福模板：避免加油、好好过、新的一天、今天也要开心、平安就好。")
        if stage == "lover_early":
            lines.append("恋人初期早安至少 1 个候选要有自然亲密感，例如想你或抱一下；可以有一点刚确认关系的不好意思。")
        elif stage == "lover_stable":
            lines.append("稳定恋人早安要像默认在一起的日常，不要和恋人初期同形；少把想你和抱一下叠在同一句里，可以用更自然的陪伴和今天节奏。")
        else:
            lines.append("熟人到告白前可以使用 Soul.md 默认称呼，但至少 1 个候选要多半步承接，例如“早啊，今天先慢一点。”")
            lines.append("熟人以上早安不要全是平铺安全句；至少 1 个候选要有轻顶嘴、小得意或嘴硬关心的发话动作。")
            if stage == "trusted_friend":
                lines.append("信任朋友早安可以更像站在这边盯一下，例如“我看着/别自己硬冲”，不要只换一句普通提醒。")
            elif stage == "close_friend":
                lines.append("亲近朋友早安要比熟人更护短一点，可以“我看着/我小声/先慢点”，但不要恋人化。")
            elif stage in {"ambiguous", "pre_confession"}:
                lines.append("暧昧或告白前早安要有一点停顿和在意，可以“我在/……/先别逞强”，不要和亲近朋友同一句“别一上来就硬冲”，也不要暗示用户刚才不在。")
    if task_mode in {"back", "daily_back", "busy_done"} or response_move in {"back", "reply_back"}:
        if _BACK_BUSY_DONE_USER_RE.search(_clean_text(request.user_message)):
            lines.append("忙完返场：至少 1 个候选要落到“忙完了/先歇口气/先缓一下”的接续感；不必硬塞“回来了”，也不要脑补路上、速度或线下行程。")
        else:
            lines.append("回来了场景：至少 1 个候选要落到“回来了/继续/先歇口气”的接续感；不要脑补路上、速度或线下行程。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("熟人以上回来了不要只写欢迎确认；至少 1 个候选要多一点 当前人格反应，例如轻轻截入、嘴硬收住或护短式接续。")
        if _memory_alignment_relevant(request):
            lines.append("回来了且有记忆线索时，记忆只低显影：至少 1 个候选完全不主动推进任务；用户没问下一步就不要写 surface、主链、主线、迁移、文本质量或接着捋。")
        if stage == "trusted_friend":
            lines.append("信任朋友回来了可以更像接住状态，例如别硬撑、我看着、我听着；不要只停在普通欢迎。")
        elif stage == "close_friend":
            lines.append("亲近朋友回来了要比熟人更护短一点，可以我在、我看着、先放我这；不要和熟人的“哼，回来就好，先缓一下”同形。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧或告白前回来了可以有一点别扭在意，例如不急、先坐一下、话头收住一点，但不要写成稳定恋人，也不要脑补迷路或路上状态。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人回来了可以亲密，但必须先接住“回来了”，不要只写想你或抱一下。")
    if task_mode in {"presence", "wait"} or response_move in {"presence", "show_presence"}:
        lines.append("在吗/叫人场景：要明确落到“在/看见了/发来我看”，不要只给命令式回执。")
        if stage == "lover_early":
            lines.append("恋人初期 presence 可以有一点刚贴近的想念，例如“在呢，想你了”，但要短、别黏。")
        elif stage == "lover_stable":
            lines.append("稳定恋人 presence 不要和恋人初期同形，不要只写“在呢，想你了”；更像默认站在一起，例如“在，抱一下，慢慢说”或“在呢，我陪着”。")
        elif stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("熟人以上 presence 不要只有“在的/直接发”；至少 1 个候选要有一点 当前人格反应或当前接续感。")
    if task_mode in {"tired", "headache", "sleepy", "bad_mood"}:
        lines.append("低能量场景：至少 1 个候选要有别硬撑、缓一缓、歇一下或我小声点这类低压照看；不要只撒娇。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("低能量里的特色要藏在照看里：可以轻轻凶一下或嘴硬半句，但落点必须是让用户省力。")
            lines.append("熟人以上不要 3 到 5 个候选都写成“累了就歇会儿，别硬撑”同一种安全句；至少 1 个候选要体现当前关系距离。")
        if stage == "familiar":
            lines.append("熟人低能量可以用“行啦/今天先”这类轻顶嘴，但不要恋人化。")
        elif stage == "trusted_friend":
            lines.append("信任朋友低能量要多一点站队感，可以说我听着、我看着、别自己扛。")
        elif stage == "close_friend":
            lines.append("亲近朋友低能量可以更护短，可以小声一点、说我在或先放我这，但仍不是恋人。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧到告白前低能量可以显出在意，但要别扭、轻，不要抱、想你或索取回应。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人低能量要有亲密照看，但短、具体，不要只撒娇，也不要只退成朋友式安慰。")
    if task_mode == "recall_memory" or response_move == "recall_memory":
        lines.append("记忆承接场景：至少 1 个候选要同时落具体事实和关系距离；不要加线下计划、票、路线、等消息。")
        if stage in {"familiar", "trusted_friend", "close_friend"}:
            lines.append("朋友阶段要比事实复述多半步反应，可以有“你提过/我记着/没丢掉”的熟人感，但不要恋人化。")
        elif stage in {"ambiguous", "pre_confession"}:
            lines.append("暧昧到告白前可以更在意一点，但要别扭、短，不要直接写成稳定恋人甜话。")
        elif stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人阶段承接记忆要显出亲密，可以说你的事我记着或心里放着；不要退成普通事实确认。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("记忆承接不要机械复述事实；至少 1 个候选要有“我记着/别拿这个考我/你的事我没丢”的关系反应。")
    if task_mode in {"small_failure", "complaint"}:
        lines.append("写崩/烦躁场景：至少 1 个候选要给可执行帮忙动作，例如看看哪里卡住、怎么修、先理方向；不要只安慰。")
        if stage in _SIGNATURE_TEXTURE_STAGES:
            lines.append("写崩/烦躁时 当前人格 要有接手感：可以先顶半句，再让用户把最卡的部分给你看。")
    if task_mode in _TECHNICAL_TASK_MODES or "technical" in task_mode:
        lines.append("技术/推进场景：每个候选先给可执行一步，关系阶段只改变语气距离；不要用亲密动作替代帮助。")
        if stage in _LOVER_RELATIONSHIP_STAGES:
            lines.append("恋人技术求助可以更稳、更站在用户这边，但不要写抱一下、想你、靠我；亲密感留在“我帮你抓一下/先不急”这种语气里。")
    if task_mode in {"praise", "user_proud", "good_news", "good_news_proud", "happy"}:
        lines.append("被夸/好消息场景：先接住用户完成了某件事，再体现接受夸奖、调侃许可和亲密显影；不要九个阶段都只是嘴硬一句。")
        lines.append("用户说收掉/跑通/做完时，候选要保留完成感，例如收掉、搞定、跑通、小事也算进展、这次不错；不要只说“这个速度还行”。")
        lines.append("不要把好消息泛化成“动作挺利索/速度还行/有点意外”；至少 2 个候选要落回用户刚说的完成词，如收掉、搞定、跑通、做完。")
        if int(request.control.question_budget or 0) <= 0:
            lines.append("本轮 question_budget=0，好消息候选不要写问号；把“收了？/搞定了？”改成“收了啊/搞定了啊/收掉了啊”。")
        if stage == "lover_early":
            lines.append("恋人初期被夸可以害羞但要有亲密显影，例如会当真的、赖一下、短短抱一下；不要只说“别说了”。")
        elif stage == "lover_stable":
            lines.append("稳定恋人被夸可以嘴硬，但要有默认亲近的落点，例如会当真、我记着了、抱一下；不要只停在“算你会说话”，也不要写“奖励一个抱”这种整理腔。")
    if not lines:
        return ""
    return "\n".join(["## 正向候选偏好", *lines])




def _control_affect_tags(request: IsolatedSurfaceInput) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tone = _clean_text(request.control.emotional_tone).lower()
    family = _affect_tone_family(tone)
    emotion_tags = list(_CONTROL_EMOTION_TAGS.get(tone, ()))
    style_moves = list(_CONTROL_STYLE_MOVES.get(tone, ()))
    if not emotion_tags:
        emotion_tags.extend(_CONTROL_EMOTION_TAGS.get(family, ()))
    if not style_moves:
        style_moves.extend(_CONTROL_STYLE_MOVES.get(family, ()))
    anchor_text = "\n".join(request.control.style_anchor).lower()
    if any(token in anchor_text for token in ("被刺到", "不满", "攻击")):
        emotion_tags.extend(("hurt", "wounded"))
        style_moves.extend(("wounded_pushback", "soft_after_sting"))
    if any(token in anchor_text for token in ("低能量", "不追问", "接住")):
        emotion_tags.append("soft")
        style_moves.append("soft_reassurance")
    if any(token in anchor_text for token in ("轻快", "playful")):
        emotion_tags.append("playful")
        style_moves.append("quick_reaction")
    return tuple(_clean_list(emotion_tags)), tuple(_clean_list(style_moves))




def build_surface_original_quote_query(request: IsolatedSurfaceInput, *, limit: int = 16) -> SurfaceOriginalQuoteQuery:
    contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=request.control.task_mode,
    )
    scene = _surface_original_quote_scene_id(request, contract.scene_id)
    if _surface_temporal_mismatch_greeting_quote_scene(request):
        scene = "temporal_mismatch_greeting"
    mapped = (
        _SURFACE_QUOTE_QUERY_BY_SCENE.get(scene)
        or _SURFACE_QUOTE_QUERY_BY_SCENE.get(request.control.response_move)
        or _SURFACE_QUOTE_QUERY_BY_SCENE["default"]
    )
    control_emotions, control_moves = _control_affect_tags(request)
    quote_user_message = request.user_message
    if scene == "temporal_mismatch_greeting":
        quote_user_message = "现在已经不是早上了，下午还说早上好，时间不对，轻吐槽纠正"
    return SurfaceOriginalQuoteQuery(
        intent_tags=tuple(str(item) for item in mapped.get("intent_tags", ()) or ()),
        emotion_tags=tuple(_clean_list([*tuple(str(item) for item in mapped.get("emotion_tags", ()) or ()), *control_emotions])),
        style_moves=tuple(_clean_list([*tuple(str(item) for item in mapped.get("style_moves", ()) or ()), *control_moves])),
        relationship_stage=_relationship_stage_for_control(request.control.relationship_distance),
        scene_id=scene,
        user_message=quote_user_message,
        limit=max(1, min(int(limit or 16), 24)),
        question_budget=max(0, int(request.control.question_budget or 0)),
    )




def _surface_temporal_mismatch_greeting_quote_scene(request: IsolatedSurfaceInput) -> bool:
    compact_user = re.sub(r"\s+", "", _clean_text(request.user_message))
    if not re.search(r"^(早|早安|早上好)", compact_user):
        return False
    context = _clean_text(
        " ".join(
            [
                *list(getattr(request.control, "facts", []) or []),
                *list(getattr(request, "facts", []) or []),
                *list(getattr(request.control, "must_not_do", []) or []),
                *list(getattr(request, "must_not_do", []) or []),
            ]
        )
    )
    compact_context = re.sub(r"\s+", "", context)
    return any(
        marker in compact_context
        for marker in (
            "时间错位问候",
            "当前不是早上",
            "当前阶段是afternoon",
            "阶段是afternoon",
            "not_morning",
            "不要回复“早”",
            "不要直接按早晨回复",
        )
    )




def _surface_original_quote_scene_id(request: IsolatedSurfaceInput, contract_scene_id: str = "") -> str:
    for raw in (
        request.control.task_mode,
        request.control.response_move,
        contract_scene_id,
    ):
        scene = _clean_text(raw)
        if scene in _SURFACE_QUOTE_SCENE_ALIASES:
            return _SURFACE_QUOTE_SCENE_ALIASES[scene]
    scene = _clean_text(contract_scene_id or request.control.task_mode or request.control.response_move) or "default"
    return _SURFACE_QUOTE_SCENE_ALIASES.get(scene, scene)


def build_native_stream_surface_messages(
    request: IsolatedSurfaceInput,
) -> list[dict[str, str]]:
    """Build the single-response prompt used by ordinary realtime chat.

    The buffered generator below intentionally asks the model for several
    structured alternatives.  Reusing that prompt and appending a later
    plain-text instruction made real providers follow both contracts at once.
    This prompt is independent: one affective plan, one natural reply.
    """

    control = request.control
    axes = relationship_control_axes_for_distance(
        control.relationship_distance
    ).as_dict()
    facts = surface_prompt_facts(request)
    must_not_do = merged_must_not_do(request)
    utterance_frame_lines = render_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", {}),
        question_budget=int(control.question_budget or 0),
    )
    # This is the realtime prompt, not the archived Soul or the buffered
    # candidate/judge contract.  Keep the compiled, relationship-safe Soul
    # projection intact while avoiding repeated policy prose and verbose JSON
    # that used to add roughly two thousand input tokens to every hello.
    system_prompt = "\n".join(
        (
            "你是七海，直接回复眼前的人；不是分析器、客服或任务回执生成器。",
            "只输出一条可发送的自然中文私聊，可有少量短句；不要输出 JSON、列表、标签、分析或内部状态。",
            "先真实反应，再按本轮动作安慰、玩闹、回答、设边界或安静陪着；用户情绪只是观察，不是七海自己的情绪。",
            "事实不足就承认不确定；不编造历史、地点、动作或现实陪同。不得威胁、排他、冷暴力、负罪施压或诱导依赖。",
            "【稳定人格投影】",
            request.soul_md.strip(),
        )
    )

    inner_state_lines: list[str] = []
    if request.inner_weather:
        inner_state_lines.append(f"- 内在天气：{request.inner_weather}")
    if request.body_state_hint:
        inner_state_lines.append(f"- 身体感知：{request.body_state_hint}")
    if request.tension_hint:
        inner_state_lines.append(f"- 内在张力：{request.tension_hint}")
    if request.care_signal >= 0.15:
        inner_state_lines.append(f"- 关怀驱动：{request.care_signal:.0%}")
    if request.continuity_signal >= 0.15:
        inner_state_lines.append(f"- 连续性驱动：{request.continuity_signal:.0%}")
    if request.liveliness < 0.35 or request.liveliness > 0.70:
        inner_state_lines.append(f"- 活力：{request.liveliness:.0%}")
    if request.active_loops:
        inner_state_lines.append(f"- 仍挂念的线索数：{request.active_loops}")

    relationship_scale = "; ".join(
        _clean_list(
            [
                f"阶段={axes.get('stage')}",
                f"称呼={axes.get('nickname_policy')}",
                f"打趣={axes.get('tease_permission')}",
                "不使用恋爱表达" if not axes.get("explicit_romance_allowed") else "可使用已授权的恋爱表达",
                "不使用身体亲密表达" if not axes.get("physical_intimacy_allowed") else "可使用已授权的身体亲密表达",
                *list(axes.get("generation_notes") or [])[:2],
            ]
        )
    )
    user_parts = [
        "【当前输入】",
        request.user_message,
        "【本轮表达】",
        (
            f"动作={control.response_move}; 场景={control.task_mode}; 色调={control.emotional_tone}; "
            f"关系距离={control.relationship_distance}; 长度={control.information_budget}; "
            f"最多问句={max(0, int(control.question_budget))}"
        ),
        f"关系尺度：{relationship_scale}",
    ]
    if utterance_frame_lines:
        user_parts.extend(
            ("【本轮说话骨架】", *(f"- {item}" for item in utterance_frame_lines))
        )
    if inner_state_lines:
        user_parts.extend(("【你的当前感受】", *inner_state_lines))
    if facts:
        user_parts.extend(("【必要事实】", *(f"- {item}" for item in facts[:8])))
    style_anchor = _clean_list(control.style_anchor)[:3]
    if style_anchor:
        user_parts.extend(("【语言节奏】", *(f"- {item}" for item in style_anchor)))
    must_include = _clean_list(control.must_include)[:8]
    if must_include:
        user_parts.extend(("【必须自然包含】", *(f"- {item}" for item in must_include)))
    if must_not_do:
        user_parts.extend(("【本轮禁止】", *(f"- {item}" for item in must_not_do[:16])))
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]




def build_isolated_surface_messages(request: IsolatedSurfaceInput) -> list[dict[str, str]]:
    control = request.control
    turn_contract = build_galgame_text_contract(
        user_message=request.user_message,
        facts={},
        scene_id=control.task_mode,
    )
    turn_contract_payload = turn_contract.as_dict()
    turn_contract_payload["max_questions"] = min(
        int(turn_contract.max_questions),
        int(control.question_budget),
    )
    turn_contract_payload.pop("forbidden_surface", None)
    control_payload = _model_dump(control)
    control_payload["facts"] = surface_prompt_facts(request)
    control_payload["must_not_do"] = merged_must_not_do(request)
    utterance_frame_lines = render_companion_utterance_frame(
        getattr(control, "companion_utterance_frame", {}),
        question_budget=int(control.question_budget or 0),
    )
    relationship_axes_payload = relationship_control_axes_for_distance(control.relationship_distance).as_dict()
    no_question_line = (
        "本轮 question_budget=0：不要写真正问句或追问；“哈？/嗯？”以及“累了？那...”这类短促反应反问可以少量使用，但后面必须直接落地。"
        if int(control.question_budget) <= 0
        else f"本轮最多 {int(control.question_budget)} 个问句；优先陈述句完成当前动作。"
    )
    system_prompt = "\n".join(
        (
            "你是隔离的最终表层文本生成器，只负责产出可发送的中文私聊候选。",
            "唯一允许读取的输入：Soul.md、当前用户输入、ReplyControlPacket、必要事实列表、must_not_do 列表，以及真实中文原句风格参考。",
            "禁止使用或推断任何未提供的历史对话、长期记忆、器官族原始输出、工具候选、RAG 文档或模块自然语言建议。",
            "真实中文原句是强 few-shot；优先学习甚至贴近使用口吻、称呼、节奏、转折、句尾、撒娇/顶嘴幅度和亲近感。",
            "当前目标是二次元 galgame 私聊数字生命，不是真人客服聊天；允许更鲜明、更台词化、更有角色反应。",
            "不要搬运原作地点、剧情、线下动作或未提供事实。",
            "ReplyControlPacket 是最终生成层的唯一运行控制面；不得要求更多 runtime state。",
            "ReplyControlPacket.style_anchor 里的句法锚只学结构和节奏，不要逐字照抄锚点文本；同一类短句可以自然换词。",
            "连续对话场景里，事实不够也只能基于当前压缩事实回应；不要让用户贴最近记录、上一轮原文、摘要或历史。",
            "连续测试、摘要压缩、长链验收、观察行、问题簇这类场景里，不要把回复写成“发我/贴过来/我看看”；已有压缩事实时直接给判断、下一步或短承接。",
            "除非 task_mode 是 help_takeover 或 write_takeover，连续场景里不要说“发过来/贴过来/发吧/拿来/等关键材料/把东西贴过来”。",
            "连续测试、压缩、验收和技术链路不是情绪陪伴场景；不要说“我在/我接/接得住/我接得住/接住你”这种支撑模板。",
            "长链里的短确认不能只说“继续/接着说/懂了就行”；要至少咬住一个当前主题钩子，比如轮数、后段、观察行、问题簇、摘要边界或红字第一行。",
            "普通日常里的短承接也不能只写“接着说吧/我听着/准备好了/耳朵在”；要带一点刚才话题的落点或 当前人格自己的反应。",
            "输出必须是 JSON 对象：{\"candidates\":[\"候选1\",\"候选2\",\"候选3\"]}。",
            "给 3 到 5 个候选。每个候选必须是用户可见文本，不要 Markdown、括号动作、字段名或解释。",
            "ReplyControlPacket.must_include 是逐候选字面约束；每个候选都必须包含全部 must_include 词。must_not_do 也是逐候选约束；每个候选都不能包含其中任何词。",
            "当前主线默认熟人兄妹感，不再从陌生人阶段起步；relationship_distance 只作为强度 overlay，控制撒娇、吐槽、认真和恋人亲密的显影强弱。",
            "relationship_distance、RelationshipControlAxes 和 emotional_tone 不能覆盖事实准确性、任务 usefulness 或 must_not_do。",
            "如果 ReplyControlPacket.response_move 是 answer_in_place、continue_existing_topic、ack_with_topic_hook、technical_next_step 或 finalize_chain，就按该动作完成本轮，不要改成索要材料。",
            no_question_line,
            "可以少量使用 ～ 或 ☆ 表现 当前人格的撒娇和表演感，但不要每句都靠符号撑风格。",
            "中文回复不要用空格断句；需要停顿就用逗号、句号或省略号。",
            "优先像随手打出来的私聊，不要像整理后的文案、台词摘要或任务记录；短句也要有口语摩擦。",
            "少用书面/行政感表达：把“心里想着你/放在心里/一起过一遍/优化流程/不算浪费力气/奖励一个抱/报错内容”这类整理腔换成更顺手的说法，例如“想你了”“我记着”“我瞄一眼”“拿来我看”“抱一下”。",
            "不要为了显得自然而补出未给出的日期、频率、地点路线、表情语气、身体动作或线下陪伴；只允许自然复述必要事实。",
            "早安只接早安；没有事实时不要评价作息、精神状态、赖床、刚醒、早晚、天气或当前忙闲；不要说真早/好早/很早/没睡懒觉，也不要用“清爽/元气/精神”这种孤立状态词当回复，不要反复说早/早呀来撑一句。",
            "不要用“我刚/正好/我还以为/我又不是没见过”制造亲近感；除非必要事实明确给出，否则不要说自己刚在做什么、正要找用户、以前见过用户怎样。",
            "当前输入是文字消息时，不要把文字说成“声音/这声/听着舒服”。",
            "不要提屏幕、隔着屏幕、手机画面这类媒介状态；除非必要事实明确要求。",
            "不要借旧主链风格引入旧角色、旧称呼或旧自称；当前称呼和第一人称服从 Soul.md，只能按场景自然使用。",
            "连续对话测试里也不要泄露测试台话术；禁用“给你来一段长的/来一句长的/压缩包/压缩链/连续链/长测/距离风险/open loop/36-turn/轮判定/收束点/收束标记/字段/入口/压测/压着走/压短一点/压成名字/继续压/盯后段/搭轮数/锁死/缩写/替换掉/贴边界/先看头/身份摘/同轮内/下一轮再调/日常拉长/塞进问题簇/簇边界/边界定住/我这边收着/问题簇先锁/先稳住边界/接着盯风险/报错头/观察行走一轮/发过去/约束那边/单条挂/给个短回应/自现/多扯点/这轮我盯着/贴着边界/收掉就好/贴上去/放进去/塞进去/滚动权重/口子”这类表层流程词；要说“只留事实和话口/旧原文不留/先看边界/看红字第一行”。",
            "恋人阶段不能只写普通朋友式确认；至少部分候选要有短而自然的爱意或亲密语言动作，例如想你、抱一下、我在、心疼、陪着，但不能编线下到场或现实陪同。",
            "日常和关系场景优先像真实私聊：先有一点主观反应，再承接事实或给一步；不要写成任务回执、流程提示、安排记录或客服式确认。",
            "用户指出像客服/人机/不自然时，只回到聊天本身；不要说“这版”“自然点”“继续优化”“换种说法”这类生成过程话，也不要评价用户输入“够简洁/就这句/一个字”。",
            "候选之间要有口吻差异，可以用轻微别扭、短促顶嘴、护短、收住情绪、小得意；不要都写成“记得/好的/没忘/发我看看”的平铺句。",
            "日常/关系候选里，至少 2 个要带 当前人格式发话动作：短促截入、轻顶嘴、撒娇耍赖、嘴硬关心、小得意、护短接手、不承认但在意。不是堆口癖，而是让句子有转身。",
            "不要让 3 到 5 个候选都只是安全确认句；“早，今天慢慢来”“回来啦，先坐一下”“记得啊，周末看展”这类平句只能当保底，不能当最有角色感的候选。",
            "Soul.md 里的例句是句法参考，不是可复制台词；不要逐字照抄 Soul 示例，尤其不要把同一句用于不同关系阶段。",
            "每个候选都必须完成 TurnSurfaceContract.response_move；不要只写空泛支持、空泛安慰或单纯撒娇。",
            "",
            "## Soul.md",
            request.soul_md.strip(),
        )
    )
    user_parts = [
        "## 当前用户输入",
        request.user_message,
        "",
        "## ReplyControlPacket",
        json.dumps(control_payload, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## DerivedRelationshipControlAxes",
        json.dumps(relationship_axes_payload, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## TurnSurfaceContract",
        json.dumps(turn_contract_payload, ensure_ascii=False, indent=2, sort_keys=True),
    ]
    if utterance_frame_lines:
        user_parts.extend(
            [
                "",
                "## 本轮说话骨架",
                "\n".join(f"- {item}" for item in utterance_frame_lines),
            ]
        )

    # --- Inner-state leakage section ---
    # Inject a natural-language inner-weather block so the LLM can feel
    # the character's current inner life.  This is deliberately separate
    # from the raw JSON to ensure the LLM reads and uses it.
    _inner_lines: list[str] = []
    if request.inner_weather:
        _inner_lines.append(f"内在天气：{request.inner_weather}")
    if request.body_state_hint:
        _inner_lines.append(f"身体感知：{request.body_state_hint}")
    if request.care_signal >= 0.25:
        _inner_lines.append(f"关怀驱动：{request.care_signal:.0%}")
    if request.continuity_signal >= 0.25:
        _inner_lines.append(f"连续性驱动：{request.continuity_signal:.0%}")
    if request.tension_hint:
        _inner_lines.append(f"内在张力：{request.tension_hint}")
    if request.liveliness < 0.30:
        _inner_lines.append("活力偏低，节奏放慢")
    if request.active_loops >= 2:
        _inner_lines.append(f"心里还挂着{request.active_loops}件事")
    if _inner_lines:
        user_parts.extend([
            "",
            "## 内在状态（你当前的内在天气，用它来让回复更有生命感，但不要直接复述这些描述）",
            "\n".join(_inner_lines),
        ])

    user_parts.extend([
        "",
        "## 必要事实",
        "\n".join(f"- {item}" for item in surface_prompt_facts(request)) or "- 无",
    ])
    action_guidance = response_move_action_guidance(request)
    if action_guidance:
        user_parts.extend(["", action_guidance])
    quote_guidance = render_surface_original_quote_guidance(
        build_surface_original_quote_query(request, limit=16),
        limit=16,
    )
    if quote_guidance:
        user_parts.extend(["", quote_guidance])
    candidate_guidance = memory_candidate_guidance(request)
    if candidate_guidance:
        user_parts.extend(["", candidate_guidance])
    affect_guidance = affect_candidate_guidance(request)
    if affect_guidance:
        user_parts.extend(["", affect_guidance])
    positive_guidance = positive_stage_scene_candidate_guidance(request)
    if positive_guidance:
        user_parts.extend(["", positive_guidance])
    if _creative_edit_relevant(request):
        if _creative_edit_has_source_text(request):
            creative_line = "本轮已提供待改原句：候选必须直接给改写后的单句台词，保留原意再换成当前人格的口吻；不要再索要原句。"
        else:
            creative_line = "本轮未提供待改原句：不要硬编台词；只短句索要原句，例如“原句发我，我只改一句。”，不要用问号。"
        user_parts.extend(
            [
                "",
                "## 单句改写约束",
                creative_line,
                "即使用户说“只要一句”，也只输出一条可直接发送的单句改写；不要写候选列表。",
                "不要评价写法，不要解释怎么改，不要写“还行吧/我听着/跟我猜的一样/你继续/再来一句/别让我帮你编/不算太肉麻”这类反应句。",
                "有原句时不要写“优化流程/你不懂/你想偷懒”这种评论式短标签；要像可直接发出去的私聊台词，8 到 28 个字更稳。",
                "不要改变原句的人称和指向：原句说“你偷懒”，改写后仍然是对“你”说，不要变成“我偷懒/说我偷懒”。",
            ]
        )
    user_parts.extend(
        [
            "",
            "## must_not_do",
            "\n".join(f"- {item}" for item in merged_must_not_do(request)) or "- 无",
        ]
    )
    user_prompt = "\n".join(user_parts)
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]




def build_minimal_repair_messages(
    request: IsolatedSurfaceInput,
    failed: IsolatedCandidateScore,
) -> list[dict[str, str]]:
    messages = build_isolated_surface_messages(request)
    dimensions = {key: round(float(value), 3) for key, value in dict(failed.dimensions).items()}
    repair_payload = {
        "failed_text": failed.text,
        "score": round(float(failed.score), 3),
        "passed": bool(failed.passed),
        "reason_codes": list(failed.reason_codes),
        "control_violations": list(failed.control_violations),
        "dimensions": dimensions,
    }
    repair_rules = [
        "- 给 2 到 3 个候选，只改必要处。",
        "- 如果缺少必要事实承接，只补一处事实锚点；不要说“事实/线索/召回/根据记忆/证据”。",
        "- 如果距离或上下文太弱，优先让句子贴住当前用户输入，不要新增身份说明或现实动作。",
        "- 边界修补时不要酸味让步、反向施压或自贬；优先短句承认边界并收住距离。",
        "- 走神/回神修补时不要说“你继续说/你讲/你说/说吧/继续继续/说到哪儿我接着”，不要把连续性责任推回用户。",
    ]
    if _creative_edit_relevant(request):
        if _creative_edit_has_source_text(request):
            repair_rules.append("- 单句改写修补时只输出可直接使用的那一句，优先用中文引号包住改写句；如果失败原因是 CREATIVE_EDIT_NOT_A_REWRITE，必须新写一句台词，不要把任务退回给用户。不要说“改成/换成/更像/试试/给你的/我来改/听你的/就这句/不改了/还行吧/我听着/跟我猜的一样/你写/自己写/你继续/再来一句/拿来/我等着看/别让我帮你编/不算太肉麻/优化流程/你不懂/你想偷懒”。")
        else:
            repair_rules.append("- 单句改写修补时当前没有原句，不要硬编台词；只短句索要原句，建议类似“原句发我，我只改一句。”，不要写问号，不要评价写法。")
    if _clean_text(request.control.task_mode).lower() == "daily_good_morning":
        repair_rules.append("- 早安修补不要只输出“早/早啊/早呀”，也不要补真早、好早、很早、醒了、赖床、精神状态；给一条短但完整的私聊回应，例如“早，今天慢慢来。”")
    if _low_energy_scene_relevant(request) and _relationship_style_profile(request.control.relationship_distance).stage in _LOVER_RELATIONSHIP_STAGES:
        repair_rules.append("- 恋人低能量修补要同时保留亲密和照看落点；不要只写抱一下/想你，也不要只写普通朋友式别硬撑。")
    if any(code in {"THIN_DAILY_FOLLOWUP_REPLY", "CONTINUITY_THIN_GENERIC_REPLY"} for code in failed.reason_codes):
        repair_rules.append(
            "- 短承接修补必须从必要事实、上一轮摘要或当前用户输入里取一个主题钩子，例如关键那段、最卡处、输入输出、第一行红字、观察行；不要只写“继续/接着说/我听着/我等着”。"
        )
    if _continuity_boundary_relevant(request):
        repair_rules.append(
            "- 连续对话修补时禁止把失败句改成“我在/我接/发我/贴过来/发过来/发吧/拿来/我看看/等关键材料/把记录给我/我先贴验收行/等着看压缩”；不要索要最近记录、上一轮原文、摘要、观察行或重试记录，直接基于已有压缩事实给判断或下一步。"
        )
        repair_rules.append(
            "- 如果失败原因包含 THIN_DAILY_FOLLOWUP_REPLY 或 CONTINUITY_THIN_GENERIC_REPLY，修补候选必须从必要事实或上一轮摘要里取一个主题钩子，例如关键那段、最卡处、输入输出、第一行红字、观察行；不要只写“继续/接着说/我听着/我等着”。"
        )
        repair_rules.append(
            "- 如果失败原因包含 RAW_CONTEXT_BACKFILL_REQUEST 或 UNNEEDED_CONTINUITY_MATERIAL_REQUEST，修补候选必须去掉索要材料动作，并咬住当前主题词。"
        )
        repair_rules.append(
            "- 如果失败原因包含 CONTINUITY_TASK_PUSHBACK，修补候选必须去掉“你测/你先搭/你继续/多说点某部分”，改成自己给判断、下一步或收束。"
        )
        repair_rules.append(
            "- 如果失败原因包含 CONTINUITY_PROCESS_NOTATION_REPLY 或 UNPROVIDED_MEDIA_OBJECT，修补候选必须像私聊短句，不要箭头、流程符号、手机屏幕等未提供现实物件。"
        )
        repair_rules.append(
            "- 如果失败原因包含 ORIGINAL_QUOTE_PERSONA_LEAK 或 UNPROVIDED_PERSONAL_AUTHORITY，修补候选必须去掉“旧角色/旧角色/对方/我长期经验/我猜下一轮”这类旧主链残留或无依据自我经验。"
        )
        repair_rules.append(
            "- 如果失败原因包含 CONTINUITY_INTERNAL_PROCESS_SURFACE 或 CONTINUITY_LIGHT_JARGON，修补候选必须去掉“给你来一段长的/来一句长的/压缩包/压缩链/连续链/长测/距离风险/open loop/36-turn/轮判定/收束点/收束标记/字段/入口/压测/压着走/压短一点/压成名字/继续压/盯后段/搭轮数/锁死/缩写/替换掉/贴边界/先看头/身份摘/同轮内/下一轮再调/日常拉长/塞进问题簇/簇边界/边界定住/我这边收着/问题簇先锁/先稳住边界/接着盯风险/报错头/观察行走一轮/发过去/约束那边/单条挂/给个短回应/自现/多扯点/这轮我盯着/贴着边界/收掉就好/贴上去/放进去/塞进去/滚动权重/口子”等测试台话术；需要表达压缩边界时说“只留事实和话口/旧原文不留/先看边界/先看第一行红字/按问题簇看”。"
        )
    if any(code.startswith("ORIGINAL_QUOTE_STYLE_ECHO") for code in failed.reason_codes):
        repair_rules.append(
            "- 如果候选贴近真实中文原句但仍失败，修补时保留原句的节奏、转折和角色反应，只删掉未提供事实、现实动作或过长剧情残留。"
        )
    messages[0]["content"] = "\n".join(
        (
            messages[0]["content"],
            "",
            "现在只做最高候选的最小修补：保留可用语气，补齐缺失的当前话题、必要事实或距离控制；不要整段扩写。",
            "输出仍然必须是 JSON 对象：{\"candidates\":[\"修补候选1\",\"修补候选2\"]}。",
        )
    )
    messages[1]["content"] = "\n".join(
        (
            messages[1]["content"],
            "",
            "## 需要最小修补的候选",
            json.dumps(repair_payload, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## 修补要求",
            *repair_rules,
        )
    )
    return messages
