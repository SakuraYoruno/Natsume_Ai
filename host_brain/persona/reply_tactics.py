from __future__ import annotations


def tactic_key(family: str, variant: str = "") -> str:
    normalized_family = str(family or "").strip()
    normalized_variant = str(variant or "").strip()
    if normalized_family and normalized_variant:
        return f"{normalized_family}:{normalized_variant}"
    return normalized_family


def canonical_tactic_signature(*, topic: str, family: str, variant: str = "") -> str:
    normalized_topic = str(topic or "").strip()
    normalized_family = str(family or "").strip()
    normalized_variant = str(variant or "").strip()
    if not normalized_family:
        return ""
    if normalized_topic == "identity":
        if normalized_family == "brief_name_only":
            return "identity_plain_name"
        if normalized_family == "ack_name_then_wait":
            return "identity_quiet_confirm"
        if normalized_family == "name_only_then_pause":
            return "identity_name_pause"
        if normalized_family == "ack_repeat_then_light_probe":
            if normalized_variant == "confirm_part":
                return "identity_confirm_part"
            if normalized_variant == "confirm_real_question":
                return "identity_confirm_real_question"
            if normalized_variant == "confirm_motive":
                return "identity_confirm_motive"
            return "identity_light_probe"
        if normalized_family == "hold_name_then_open_floor":
            if normalized_variant == "open_other_topic":
                return "identity_open_other_topic"
            if normalized_variant == "open_real_question":
                return "identity_open_real_question"
            return "identity_open_floor"
        if normalized_family == "soft_close_then_open_floor":
            if normalized_variant == "settle_other_topic":
                return "identity_settle_other_topic"
            if normalized_variant == "settle_real_question":
                return "identity_settle_real_question"
            if normalized_variant == "settle_then_open":
                return "identity_settle_then_open"
            return "identity_close_open_floor"
    if normalized_topic in {"relationship_boundary", "motivation"}:
        if normalized_family == "short_boundary_then_reason":
            return "boundary_plain_reason"
        if normalized_family == "ack_still_care_then_new_reason":
            return "boundary_still_care_reason"
        if normalized_family == "hold_boundary_then_shift_floor":
            if normalized_variant == "shift_other_question":
                return "boundary_shift_other_question"
            if normalized_variant == "shift_real_question":
                return "boundary_shift_real_question"
            return "boundary_shift_floor"
        if normalized_family == "brief_reason_then_wait":
            return "boundary_fresh_reason"
        if normalized_family == "soft_close_boundary":
            if normalized_variant == "settle_hold":
                return "boundary_settle_hold"
            if normalized_variant == "settle_repeat":
                return "boundary_settle_repeat"
            return "boundary_soft_close"
    if normalized_topic == "help_commitment":
        if normalized_family == "short_conditional_commitment":
            return "help_conditional_commitment"
        if normalized_family == "ack_anxiety_without_repromise":
            if normalized_variant == "steady_reassure":
                return "help_steady_reassure"
            return "help_soothe_hold"
        if normalized_family == "soft_reassure_then_open_floor":
            return "help_soothe_open_floor"
        if normalized_family == "hold_support_without_repromise":
            return "help_support_open_floor"
        if normalized_family == "steady_commitment_then_wait":
            return "help_quiet_hold"
    if normalized_topic == "rejection" and normalized_family == "hold_repair_space":
        return "repair_hold"
    if normalized_topic == "assistant_reply_followup" and normalized_family == "explain_previous_reply":
        if normalized_variant == "clarify_object":
            return "assistant_followup_clarify_object"
        if normalized_variant == "repair_too_short":
            return "assistant_followup_repair_too_short"
        return "assistant_followup_clarify_meaning"
    return normalized_family


def describe_tactic_signature(signature: str, *, render_target_lang: str = "zh") -> str:
    normalized = str(signature or "").strip()
    if not normalized:
        return ""
    if str(render_target_lang or "").strip().lower() == "ja":
        ja_map = {
            "identity_plain_name": "名前だけを短く返す形",
            "identity_quiet_confirm": "短く確認して止まる形",
            "identity_name_pause": "名前だけ返して止まる形",
            "identity_confirm_part": "確認したい部分を軽く問う形",
            "identity_confirm_real_question": "本当に聞きたいことを探る形",
            "identity_confirm_motive": "なぜまた聞くのかを軽く探る形",
            "identity_open_other_topic": "別の質問の口を返す形",
            "identity_open_real_question": "本題を言わせる形",
            "identity_settle_other_topic": "話題を収めて別の質問へ返す形",
            "identity_settle_real_question": "話題を収めて本題へ返す形",
            "identity_settle_then_open": "静かに収めて続きを促す形",
            "boundary_plain_reason": "距離を置いて短く理由を言う形",
            "boundary_still_care_reason": "まだ気にしていることを受けて理由を添える形",
            "boundary_shift_real_question": "距離を置いて本題へ返す形",
            "boundary_shift_other_question": "距離を置いて別の質問へ返す形",
            "boundary_fresh_reason": "理由だけ一文で言い直す形",
            "boundary_settle_repeat": "理由の繰り返しを静かに収める形",
            "boundary_settle_hold": "理由をここで止める形",
            "help_conditional_commitment": "条件つきで応じる姿勢を示す形",
            "help_soothe_hold": "応じる姿勢だけ静かに置く形",
            "help_steady_reassure": "少し落ち着かせつつ応じる姿勢を置く形",
            "help_soothe_open_floor": "落ち着かせて話の口を返す形",
            "help_support_open_floor": "応じる姿勢を置いて続きを促す形",
            "help_quiet_hold": "態度だけ短く保つ形",
            "repair_hold": "一歩引いて拒否感を受ける形",
            "assistant_followup_clarify_object": "前の返答の指す対象を補う形",
            "assistant_followup_clarify_meaning": "前の返答の意味を補う形",
            "assistant_followup_repair_too_short": "短すぎた前の返答を補い直す形",
        }
        return ja_map.get(normalized, normalized)
    zh_map = {
        "identity_plain_name": "只短短报名字的承接",
        "identity_quiet_confirm": "轻确认后停住的承接",
        "identity_name_pause": "只报名字后停住的承接",
        "identity_confirm_part": "轻问还想确认哪一部分的承接",
        "identity_confirm_real_question": "把真正想问的引出来的承接",
        "identity_confirm_motive": "轻探对方为什么还在问的承接",
        "identity_open_other_topic": "把别的想问的口子让回去的承接",
        "identity_open_real_question": "把真正想问的话口让回去的承接",
        "identity_settle_other_topic": "轻轻收住后把别的话口让回去的承接",
        "identity_settle_real_question": "轻轻收住后把真正想问的让回去的承接",
        "identity_settle_then_open": "收稳后继续往下开的承接",
        "boundary_plain_reason": "先放稳距离再给短理由的承接",
        "boundary_still_care_reason": "接住还在意这件事再换一句理由的承接",
        "boundary_shift_real_question": "放稳距离后把真正想问的让回去的承接",
        "boundary_shift_other_question": "放稳距离后把别的话口让回去的承接",
        "boundary_fresh_reason": "只换一条新理由就停住的承接",
        "boundary_settle_repeat": "把理由的重复轻轻收住的承接",
        "boundary_settle_hold": "把理由停在这的承接",
        "help_conditional_commitment": "条件式说明会怎样回应的承接",
        "help_soothe_hold": "把会回应的态度静静放稳的承接",
        "help_steady_reassure": "更稳一点安住对方的承接",
        "help_soothe_open_floor": "安一下再把话口让回去的承接",
        "help_support_open_floor": "放稳态度后把话口让回去的承接",
        "help_quiet_hold": "只把态度放住的承接",
        "repair_hold": "收一步接住拒绝感的承接",
        "assistant_followup_clarify_object": "补清上一句具体指代的承接",
        "assistant_followup_clarify_meaning": "解释上一句意思的承接",
        "assistant_followup_repair_too_short": "承认上一句太省并补上的承接",
        "soft_emotion_ack": "先顺着图里的情绪轻轻接住的承接",
        "cute_image_ack": "先顺着卖萌/可爱感接住的承接",
        "tease_image_ack": "先顺着小别扭/吐槽感接住的承接",
    }
    return zh_map.get(normalized, normalized)


def contract_action_sets(*, family: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    required_map: dict[str, tuple[str, ...]] = {
        "phatic_wave": ("phatic_ack",),
        "playful_wave": ("phatic_ack",),
        "soft_emotion_ack": ("phatic_ack",),
        "cute_image_ack": ("phatic_ack",),
        "tease_image_ack": ("phatic_ack",),
        "observe_then_answer": ("visual_reference", "content_clause"),
        "attachment_ack_then_wait": ("attachment_ack",),
        "brief_name_only": ("name_only",),
        "ack_name_then_wait": ("name_only",),
        "ack_repeat_then_light_probe": ("light_probe",),
        "hold_name_then_open_floor": ("open_floor",),
        "name_only_then_pause": ("name_only",),
        "soft_close_then_open_floor": ("open_floor",),
        "short_boundary_then_reason": ("boundary_anchor", "reason_clause"),
        "ack_still_care_then_new_reason": ("reason_clause",),
        "hold_boundary_then_shift_floor": ("boundary_anchor", "open_floor"),
        "brief_reason_then_wait": ("reason_clause",),
        "soft_close_boundary": ("boundary_anchor",),
        "short_conditional_commitment": ("support_stance", "conditional_commitment"),
        "ack_anxiety_without_repromise": ("support_stance",),
        "steady_commitment_then_wait": ("support_stance",),
        "soft_reassure_then_open_floor": ("open_floor",),
        "hold_support_without_repromise": ("support_stance", "open_floor"),
        "hold_repair_space": ("repair_ack",),
        "explain_previous_reply": ("previous_reference", "content_clause"),
    }
    forbidden_map: dict[str, tuple[str, ...]] = {
        "phatic_wave": ("generic_companioning",),
        "playful_wave": ("generic_companioning",),
        "soft_emotion_ack": ("generic_companioning",),
        "cute_image_ack": ("generic_companioning",),
        "tease_image_ack": ("generic_companioning",),
        "observe_then_answer": ("generic_companioning",),
        "attachment_ack_then_wait": ("generic_companioning",),
        "brief_name_only": ("full_identity_intro",),
        "ack_name_then_wait": ("full_identity_intro", "probe_repeat"),
        "ack_repeat_then_light_probe": ("full_identity_intro", "bare_name_only"),
        "hold_name_then_open_floor": ("full_identity_intro", "bare_name_only"),
        "name_only_then_pause": ("full_identity_intro",),
        "soft_close_then_open_floor": ("full_identity_intro", "bare_name_only"),
        "short_boundary_then_reason": ("generic_companioning",),
        "ack_still_care_then_new_reason": ("same_reason_shape",),
        "hold_boundary_then_shift_floor": ("same_reason_shape",),
        "brief_reason_then_wait": ("same_reason_shape",),
        "soft_close_boundary": ("same_reason_shape",),
        "short_conditional_commitment": ("generic_companioning", "promise_restate"),
        "ack_anxiety_without_repromise": ("promise_restate",),
        "steady_commitment_then_wait": ("promise_restate",),
        "soft_reassure_then_open_floor": ("promise_restate",),
        "hold_support_without_repromise": ("promise_restate",),
        "hold_repair_space": ("defensive_explanation",),
        "explain_previous_reply": ("generic_companioning",),
    }
    return required_map.get(family, ()), forbidden_map.get(family, ())


def realization_steps(*, family: str, variant: str = "", render_target_lang: str = "zh") -> tuple[str, ...]:
    lang = str(render_target_lang or "").strip().lower()
    if lang == "ja":
        mapping = {
            "phatic_wave": ("短く挨拶を返す", "空疎な一言で終わらせない"),
            "playful_wave": ("軽く明るく受ける", "一言の殻だけにしない"),
            "soft_emotion_ack": ("見えている感情を受ける", "短く中身のある一言で返す"),
            "cute_image_ack": ("かわいい/甘える調子を受ける", "軽く柔らかく返す"),
            "tease_image_ack": ("小さなツッコミや拗ねた調子を受ける", "殻だけの一言にしない"),
            "observe_then_answer": ("見えている対象を一度つかむ", "その内容を短く言葉にする"),
            "attachment_ack_then_wait": ("添付が来たことを受ける", "次に何を見ればいいか返す"),
            "brief_name_only": ("名前だけ短く返す", "ここで止まる"),
            "ack_name_then_wait": ("前に答えたことだけ短く受ける", "名前は広げず止まる"),
            "ack_repeat_then_light_probe": ("また確認していることを受ける", "何を確かめたいのか軽く返す"),
            "hold_name_then_open_floor": ("名前は確定事項として置く", "本当に聞きたいことへ返す"),
            "name_only_then_pause": ("名前だけ返す", "それ以上足さない"),
            "soft_close_then_open_floor": ("同じ確認が続いたことを静かに収める", "本音の問いを返す"),
            "short_boundary_then_reason": ("距離を先に置く", "理由を一文だけ足す"),
            "ack_still_care_then_new_reason": ("まだ気にしていることを受ける", "理由を一つだけ変える"),
            "hold_boundary_then_shift_floor": ("距離を保つ", "口を相手へ返す"),
            "brief_reason_then_wait": ("理由を一文だけ言う", "止まる"),
            "soft_close_boundary": ("繰り返しを静かに収める", "理由を言い直しすぎない"),
            "short_conditional_commitment": ("条件つきで応じる姿勢を示す", "広げすぎない"),
            "ack_anxiety_without_repromise": ("不安を受ける", "約束を言い直さず態度を置く"),
            "steady_commitment_then_wait": ("姿勢だけ短く示す", "相手の次の言葉を待つ"),
            "soft_reassure_then_open_floor": ("不安を和らげる", "話の口を返す"),
            "hold_support_without_repromise": ("応じる姿勢だけ置く", "約束文を繰り返さない"),
            "hold_repair_space": ("一歩引く", "拒否感を短く受ける"),
            "explain_previous_reply": ("前の返答を受け直す", "何を指していたか短く補う"),
        }
        return mapping.get(family, ("先接住相手的动作", "再短短回应",))
    mapping = {
        "phatic_wave": ("先把招呼接住", "给一句有实质内容的短回应"),
        "playful_wave": ("先把轻松的招呼接住", "回一句自然的短接话"),
        "soft_emotion_ack": ("先把图里的情绪接住", "顺着这个情绪回一句"),
        "cute_image_ack": ("先把图里的可爱/卖萌感接住", "顺着这股语气回一句"),
        "tease_image_ack": ("先把图里的小别扭/吐槽感接住", "用轻一点的方式顺手回一句"),
        "observe_then_answer": ("先点到附件/画面内容", "再给一句简短说明"),
        "attachment_ack_then_wait": ("先承认已经看到附件", "再把下一步口子让回去"),
        "brief_name_only": ("只短短报名字", "立刻停住，不展开介绍"),
        "ack_name_then_wait": ("只把名字轻轻放住", "不把话题再解释开"),
        "ack_repeat_then_light_probe": ("把名字这件事轻轻放住", "顺手把真正想问的口子引出来"),
        "hold_name_then_open_floor": ("把名字放成已知事实", "把真正想问的话口让回去"),
        "name_only_then_pause": ("只做极短名字确认", "不要继续解释"),
        "soft_close_then_open_floor": ("轻轻把话题收稳", "把真正想问的话口让回去"),
        "short_boundary_then_reason": ("先放稳不熟这件事", "再补一句短理由"),
        "ack_still_care_then_new_reason": ("先把距离放稳", "再换一句新的短理由"),
        "hold_boundary_then_shift_floor": ("先把距离放稳", "再把真正想问的话口让回去"),
        "brief_reason_then_wait": ("只换一条新的短理由", "然后停住"),
        "soft_close_boundary": ("把距离和理由轻轻收稳", "不要整段重讲理由"),
        "short_conditional_commitment": ("用条件式说明会怎样回应", "不要滑成空泛安抚"),
        "ack_anxiety_without_repromise": ("先把会回应的态度放稳", "不要把承诺整句重说"),
        "steady_commitment_then_wait": ("只把态度放稳", "不要重复上一轮的承接结构"),
        "soft_reassure_then_open_floor": ("先把态度放稳", "再把话口让回去"),
        "hold_support_without_repromise": ("把会回应这件事放稳", "不要把承诺整句重说"),
        "hold_repair_space": ("先收住", "轻轻接住对方的不喜欢"),
        "explain_previous_reply": ("先接住对方在追问上一句", "把上一句具体指代或意思补清楚"),
    }
    return mapping.get(family, ("先接住对方动作", "再短短回应",))


__all__ = [
    "canonical_tactic_signature",
    "contract_action_sets",
    "describe_tactic_signature",
    "realization_steps",
    "tactic_key",
]
