"""把经过边界校验的运行时状态编译为 Surface 可用的事实与禁令。

事实编译不创建新的世界知识：它筛选已证明来源、压缩长度，并把感知、凭据
和任务结果的安全边界表达为提示约束，供回复主链在用户可见层执行。
"""

from __future__ import annotations

from typing import Any
from host_brain.persona.inner_monologue import build_inner_weather_hint, InnerMonologueDigest
from collections.abc import AsyncGenerator, Mapping, Sequence
import os
import re

_AUDIO_INPUT_SOURCES = {"asr", "audio", "voice", "speech", "mic", "microphone", "voice_chat"}
_ACCOUNT_LOGIN_TASK_RE = re.compile(
    r"(登录|账号|账户|积分|轻币|购买|买|epub|电子书|lightnovel|轻小说)",
    re.IGNORECASE,
)
_ACCOUNT_CREDENTIAL_REQUEST_RE = re.compile(
    r"((账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password)"
    r".{0,16}(丢|发|给|贴|提供|传|告诉|send|paste|provide)"
    r"|(丢|发|给|贴|提供|传|告诉|send|paste|provide).{0,16}"
    r"(账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password))",
    re.IGNORECASE,
)
from host_brain.surface_runtime.life_context import (
    _surface_life_context_task_facts,
    _surface_memory_acceptance_must_not_do,
    _surface_memory_acceptance_task_facts,
    _surface_memory_write_task_facts,
)
from host_brain.surface_runtime.memory_context import (
    _surface_memory_context_orchestrator_facts,
    _surface_memory_context_orchestrator_must_not_do,
)
from host_brain.surface_runtime.quality_guards import (
    _is_technical_task,
    _surface_dream_context_task_facts,
    _surface_recent_conversation_window_facts,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_result_fact_lines,
)
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.contracts.agent_runtime.node_input_attestation import (
    NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA,
)


_ACCEPTED_TASK_RESULT_ITEM_LIMIT = 4
_ACCEPTED_TASK_RESULT_TOTAL_CHARS = 6_000
_ACCEPTED_TASK_RESULT_ITEM_MAX_CHARS = 2_400


def _accepted_task_result_texts(
    agent_input: Any | None,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """只返回绑定到冻结节点证明的任务结果文本。

    普通召回行、调用方提供的 ``store_id`` 与记忆证据绝不能成为 Plan 合成权威。冻结的
    Chat 执行器先校验每个 Task 工件，并在私有 trace 中保存不可变证明；Surface 只有在
    工件、回执和负载摘要三种身份都匹配时才接纳结果行。
    """

    # 验收证明是图执行器私有 trace 中冻结的授权清单；缺失或版本不匹配时
    # 宁可不合成结果，也不能把普通检索证据提升为已完成任务。
    trace = _safe_mapping(trace_stage_meta)
    attestation = _safe_mapping(trace.get("node_input_artifact_attestation"))
    if (
        str(attestation.get("schema_version") or "").strip()
        != NODE_INPUT_ARTIFACT_ATTESTATION_SCHEMA
    ):
        return ()
    attested: dict[str, tuple[str, str]] = {}
    for raw in _coerce_sequence(attestation.get("artifacts")):
        item = _safe_mapping(raw)
        artifact_ref = str(item.get("artifact_ref") or "").strip()
        receipt_ref = str(item.get("receipt_ref") or "").strip()
        payload_sha256 = str(item.get("payload_sha256") or "").strip().lower()
        if artifact_ref and receipt_ref and payload_sha256:
            attested[artifact_ref] = (receipt_ref, payload_sha256)
    if not attested or agent_input is None:
        return ()

    # 每一行同时比对 artifact、receipt 与 payload 摘要。三者绑定可抵御调用方
    # 替换 source_ref、复用旧回执或篡改片段内容的情况。
    candidates: list[str] = []
    seen_refs: set[str] = set()
    for raw in _coerce_sequence(
        getattr(agent_input, "retrieval_evidence_items", None)
    ):
        row = _safe_mapping(raw)
        metadata = _safe_mapping(row.get("metadata"))
        store_id = str(
            row.get("store_id") or metadata.get("store_id") or ""
        ).strip().lower()
        artifact_ref = str(row.get("source_ref") or "").strip()
        expected = attested.get(artifact_ref)
        if (
            store_id != "task_result_artifact"
            or expected is None
            or artifact_ref in seen_refs
            or str(row.get("evidence_ref") or "").strip() != expected[0]
            or str(metadata.get("payload_sha256") or "").strip().lower()
            != expected[1]
        ):
            continue
        text = str(row.get("snippet") or "").replace("\x00", "").strip()
        if not text:
            continue
        seen_refs.add(artifact_ref)
        candidates.append(text)
        if len(candidates) >= _ACCEPTED_TASK_RESULT_ITEM_LIMIT:
            break

    if not candidates:
        return ()
    # 限额让多个验收结果都有机会进入上下文，且防止单一大工件挤掉当前提问、
    # 边界禁令或其它必要证据。
    per_item_limit = min(
        _ACCEPTED_TASK_RESULT_ITEM_MAX_CHARS,
        max(600, _ACCEPTED_TASK_RESULT_TOTAL_CHARS // len(candidates)),
    )
    return tuple(text[:per_item_limit].rstrip() for text in candidates)


def _task_facts(
    *,
    reply_plan: Mapping[str, Any],
    agent_input: Any | None = None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """按稳定来源顺序汇集执行回复计划所需的受限事实。"""

    # 该集合供“按回复计划执行”的路径使用。各来源函数自身负责是否有资格产出，
    # 这里只维护稳定的组合顺序和去重，避免事实编译器偷偷改写来源内容。
    facts = [
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        *_surface_recent_conversation_window_facts(agent_input, ports=ports),
        *_surface_dialogue_repeat_task_facts(reply_plan),
        *_surface_memory_context_orchestrator_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_memory_write_task_facts(agent_input),
        *_surface_memory_acceptance_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_dream_context_task_facts(agent_input, ports=ports),
        *_surface_life_context_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        f"当前任务：{_first_nonempty(reply_plan.get('reply_contract'), reply_plan.get('response_goal'), '')}",
        f"当前意图：{_first_nonempty(reply_plan.get('task_mode'), reply_plan.get('primary_topic'), reply_plan.get('interaction_move'), '')}",
        *_technical_task_facts(reply_plan),
    ]
    return tuple(_clean_list(facts))


def _surface_reply_plan_repeat_count(reply_plan: Mapping[str, Any] | None) -> int:
    # 新字段优先；reason_tags 是旧计划兼容回退。最终返回非负整数，保证重试时
    # 相同计划得到相同的重复判断而不受异常值影响。
    plan = _safe_mapping(reply_plan)
    raw = plan.get("repeat_question_count")
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    for item in list(plan.get("reason_tags") or []):
        text = str(item or "").strip()
        match = re.match(r"repeat_count:(\d+)$", text)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                return 0
    return 0


def _surface_reply_plan_repeat_group(reply_plan: Mapping[str, Any] | None) -> str:
    plan = _safe_mapping(reply_plan)
    value = str(plan.get("repeat_surface_group") or plan.get("repeat_group") or "").strip()
    if value:
        return value
    for item in list(plan.get("reason_tags") or []):
        text = str(item or "").strip()
        if text.startswith("repeat_surface:"):
            return text.split(":", 1)[1].strip()
    return ""


def _surface_dialogue_repeat_task_facts(reply_plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    repeat_count = _surface_reply_plan_repeat_count(reply_plan)
    if repeat_count <= 0:
        return ()
    repeat_group = _surface_reply_plan_repeat_group(reply_plan)
    group_text = f"；同类输入={repeat_group}" if repeat_group else ""
    return (
        f"重复输入：用户这轮是在重复刚才同类输入，之前已有 {repeat_count} 次{group_text}；本轮必须体现已经注意到重复，不要当第一次处理。",
    )


def _surface_dialogue_repeat_must_not_do(reply_plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    if _surface_reply_plan_repeat_count(reply_plan) <= 0:
        return ()
    return (
        "重复输入时不要假装第一次看到；必须避免只换一种说法重复上一轮。",
        "重复时间错位问候时，不要猜用户睡迷糊、故意逗你、状态异常或现实行为。",
    )


def _necessary_facts(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    task_control: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """构建主链的最小事实包，避免将未核验检索内容直接当作回答依据。"""

    # 这是主链需要的最小事实包：包含输入边界与高优先级恢复证据，但不直接把
    # 任意检索文本塞进提示词。检索状态仅作可解释性线索而非回答依据。
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    facts = [
        *_surface_sub_agent_result_fact_lines(agent_input),
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        *_surface_recent_conversation_window_facts(agent_input, ports=ports),
        *_surface_memory_context_orchestrator_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_memory_acceptance_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_dream_context_task_facts(agent_input, ports=ports),
        *_surface_life_context_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_technical_task_facts(task_control or {}),
        *_account_login_handoff_facts(agent_input, task_control=task_control),
    ]
    status = _first_nonempty(retrieval_summary.get("status"), "")
    if status:
        facts.append(f"召回状态：{status}")
    return tuple(_clean_list(facts))


def _surface_input_boundary_facts(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """从可验证输入证据生成感知能力边界，防止文本被拟人化为现实感知。"""

    # 只确认实际收到的模态。没有音频证据时明确为文字通道，防止人格化表达
    # 演变为“听见/看见”用户现实环境的虚假感知声明。
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        return (
            "当前输入渠道：语音/ASR。",
            "音频输入：有；可以确认收到了语音或语音转写。",
            "不要编造未提供的音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。",
        )
    return (
        "当前输入渠道：中文文字私聊。",
        "当前没有音频、摄像头、屏幕画面或现实传感器事实。",
        "只能回应用户当前文字和压缩事实，不能把文字输入写成听见声音或看见画面。",
    )


def _surface_boundary_must_not_do(
    *,
    agent_input: Any | None = None,
    task_control: Mapping[str, Any] | None = None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # 与事实包配对的否定约束：即使存在音频，也只能承认音频/转写本身，不能
    # 推断音色、场景或画面；凭据与记忆边界由各自专用守卫追加。
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        sensory_boundary = "不要编造音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。"
    else:
        sensory_boundary = "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。"
    return tuple(
        _clean_list(
            [
                sensory_boundary,
                "不要编造用户现实动作、地点、屏幕内容、身体状态或睡眠事实，除非 facts 明确给出。",
                *_surface_memory_context_orchestrator_must_not_do(
                    agent_input=agent_input,
                    ports=ports,
                    trace_stage_meta=trace_stage_meta,
                ),
                *_surface_memory_acceptance_must_not_do(agent_input),
                *_technical_task_must_not_do(task_control or {}),
                *_account_login_must_not_do(agent_input, task_control=task_control),
            ]
        )
    )


def _input_has_audio_evidence(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> bool:
    """仅在规范元数据、观察或附件明确证明时授予音频输入判断。"""

    # 证据可来自规范化 trace、请求字段、观察结果或附件元数据。采用“存在明确
    # 证据才为真”的保守策略，缺字段和未知来源一律不授予音频感知权限。
    sources: list[Any] = []
    containers = (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)) if agent_input is not None else {},
        _safe_mapping(getattr(agent_input, "runtime_payload", None)) if agent_input is not None else {},
    )
    for container in containers:
        sources.extend(
            [
                container.get("input_source"),
                container.get("input_modality"),
                container.get("source_modality"),
                container.get("source"),
            ]
        )
    if agent_input is not None:
        sources.extend(
            [
                getattr(agent_input, "input_source", None),
                getattr(agent_input, "input_modality", None),
                getattr(agent_input, "source_modality", None),
                getattr(agent_input, "source", None),
            ]
        )
        observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
        if observations.get("audio") or observations.get("asr") or observations.get("speech_text"):
            return True
        for attachment in _coerce_sequence(getattr(agent_input, "attachments", None)):
            attachment_map = _safe_mapping(attachment)
            mime_type = str(attachment_map.get("mime_type") or attachment_map.get("content_type") or "").lower()
            media_type = str(attachment_map.get("type") or attachment_map.get("kind") or "").lower()
            if mime_type.startswith("audio/") or media_type in {"audio", "voice", "speech"}:
                return True
    for raw in sources:
        normalized = str(raw or "").strip().lower().replace("-", "_")
        if not normalized:
            continue
        if normalized in _AUDIO_INPUT_SOURCES or "asr" in normalized:
            return True
    return False


def _technical_task_facts(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术场景：先给一条能执行的排查步骤，命令和符号要准确。",
        "技术场景：最终回复至少包含查因和下一步处理，不要只给一条裸命令。",
        "技术场景：当前宿主环境按 Windows/PowerShell 优先给命令，除非用户明确指定 Linux 或 macOS。",
        "技术场景：有用性优先，角色口吻只做轻量包裹。",
    )


def _technical_task_must_not_do(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术回复不要用“干掉”“完事”等粗略说法替代清楚步骤。",
        "不要省略关键命令符号，例如端口筛选要保留冒号。",
        "不要默认给 lsof、grep 等 Linux/macOS 命令，除非用户明确指定该环境。",
        "不要只输出单条命令；命令后要说明下一步如何处理结果。",
    )


def _account_login_sensitive(agent_input: Any | None, *, task_control: Mapping[str, Any] | None = None) -> bool:
    """识别需要本地授权与凭据保护的账号/积分消费任务。"""

    # 登录/积分消费链不是一般技术问答：一旦命中明确代理或账户语义，后续事实与
    # 禁令都会切到本地授权/不可接收凭据模式，宁可多触发也不漏掉敏感场景。
    if agent_input is None:
        return False
    sub_agent_ctx = getattr(agent_input, "sub_agent_context", None)
    sub_agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if sub_agent_id == "agent_epub_builder":
        return True
    intent = _safe_mapping(getattr(agent_input, "intent_draft", None))
    target_id = str(intent.get("target_id") or intent.get("action") or "").strip()
    if target_id == "agent_epub_builder":
        return True
    text = " ".join(
        str(part or "")
        for part in (
            getattr(agent_input, "user_message", ""),
            _safe_mapping(task_control).get("reply_contract"),
            _safe_mapping(task_control).get("response_goal"),
            _safe_mapping(task_control).get("primary_topic"),
        )
    )
    return bool(_ACCOUNT_LOGIN_TASK_RE.search(text)) and bool(
        re.search(r"(登录|账号|账户|购买|积分|轻币|epub|lightnovel|轻小说)", text, re.IGNORECASE)
    )


def _account_login_handoff_facts(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # 把安全替代路径写成可执行事实：用户自己在本地完成认证，系统只复用会话；
    # 涉及购买还需明确对象和上限，避免提示词只说“不能”却没有下一步。
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "账号类任务：需要用户在本地浏览器完成登录或授权；系统只复用会话状态，不接收明文账号、密码、验证码或 Cookie。",
        "涉及积分/购买：未明确确认书名、链接和最高消耗前，只能生成购买计划，不执行购买或解锁。",
    )


def _account_login_must_not_do(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "不要要求用户把账号、密码、验证码或 Cookie 发到聊天里。",
        "不要说“账号信息丢过来”“把密码发给我”“贴 Cookie 给我”这类话。",
        "不要暗示可以代填密码、绕过登录、绕过付费限制或未确认就消耗积分。",
    )


def _sanitize_account_login_reply(text: str, *, agent_input: Any | None = None) -> tuple[str, bool]:
    """拦截敏感任务中索取凭据的最终文本，并返回是否发生替换。"""

    # 最终文本兜底仅在敏感任务且命中索要凭据模式时替换，返回 bool 供调用者
    # 记录拦截。普通回答绝不因关键词相似而被改写。
    narrative = str(text or "").strip()
    if not narrative:
        return narrative, False
    if not _account_login_sensitive(agent_input):
        return narrative, False
    if not _ACCOUNT_CREDENTIAL_REQUEST_RE.search(narrative):
        return narrative, False
    return (
        "需要登录没错，但别把账号、密码、验证码或 Cookie 发给我。"
        "你在本地浏览器完成登录/授权，我只复用会话状态；涉及积分购买时，"
        "还要你明确确认书名、链接和最高积分后才会继续。",
        True,
    )


def _has_multimodal_payload(agent_input: Any) -> bool:
    if getattr(agent_input, "attachments", None):
        return True
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    return bool(observations)


def _multimodal_surface_main_chain_supported(agent_input: Any) -> bool:
    """确认附件是否已具备可用观察，避免空载多模态请求假称看过内容。"""

    # 附件存在不代表已有可用观察；只有摘要、核心观察、可识别类型或规范占位符
    # 具备时才走多模态主链，防止空附件触发表面上像“看过内容”的回复。
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    attachment_summary = str(getattr(agent_input, "attachment_summary", "") or "").strip()
    if attachment_summary or str(observations.get("core_observation") or "").strip():
        return True
    if list(observations.get("items") or []):
        return True
    attachments = list(getattr(agent_input, "attachments", None) or [])
    types = {str(_safe_mapping(item).get("type") or "").strip().lower() for item in attachments if item}
    if types & {"image", "emoji", "sticker"}:
        return True
    text = str(getattr(agent_input, "user_message", "") or "").strip()
    return text in {"[图片消息]", "[表情消息]", "[贴纸消息]", "[动画表情]"}


def _surface_user_visible_reply_context(agent_input: Any) -> bool:
    if str(getattr(agent_input, "user_message", "") or "").strip():
        return True
    if getattr(agent_input, "attachments", None):
        return True
    if str(getattr(agent_input, "attachment_summary", "") or "").strip():
        return True
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    return bool(observations)


def _surface_main_chain_default_enabled() -> bool:
    # 环境开关只允许显式关闭；未知或缺失配置保持新主链默认开启，以免部署时
    # 因拼写/注入失败悄然回退到行为不同的 legacy 路径。
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off", "legacy"}:
        return False
    return True


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _set_agent_input_mapping(agent_input: Any, name: str, value: Mapping[str, Any]) -> None:
    try:
        setattr(agent_input, name, dict(value))
    except Exception:
        return


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_digest_from_inner_monologue(inner_monologue: Mapping[str, Any]) -> InnerMonologueDigest:
    """从 ``export_runtime_state`` 的字典形式重建 ``InnerMonologueDigest``。"""
    return InnerMonologueDigest(
        thought_stream_active=bool(inner_monologue.get("thought_stream_active")),
        private_thought_count=int(inner_monologue.get("private_thought_count") or 0),
        dominant_focus=str(inner_monologue.get("dominant_focus") or "self"),
        emotion_tag=str(inner_monologue.get("emotion_tag") or "calm"),
        salience=float(inner_monologue.get("salience") or 0.0),
        care_drive=float(inner_monologue.get("care_drive") or 0.0),
        continuity_drive=float(inner_monologue.get("continuity_drive") or 0.0),
        repair_drive=float(inner_monologue.get("repair_drive") or 0.0),
        open_loop_count=int(inner_monologue.get("open_loop_count") or 0),
        should_distill_to_memory=bool(inner_monologue.get("should_distill_to_memory")),
        dominant_need=str(inner_monologue.get("dominant_need") or "agency"),
        dominant_tendency=str(inner_monologue.get("dominant_tendency") or "observe_more"),
        novelty_score=float(inner_monologue.get("novelty_score") or 0.0),
        dominant_thought_form=str(inner_monologue.get("dominant_thought_form") or "quiet_drift"),
        liveliness_score=float(inner_monologue.get("liveliness_score") or 0.5),
        quality_flags=tuple(str(item) for item in list(inner_monologue.get("quality_flags") or [])),
        memory_anchor_count=int(inner_monologue.get("memory_anchor_count") or 0),
        style_anchor=tuple(str(item) for item in list(inner_monologue.get("style_anchor") or [])),
        must_not_do=tuple(str(item) for item in list(inner_monologue.get("must_not_do") or [])),
    )


__all__ = [
    "_build_digest_from_inner_monologue",
    "build_surface_context_payload_from_adapter",
    "build_surface_main_chain_input_from_surface_reply_input",
    "run_surface_main_chain_for_surface_reply_input",
    "surface_main_chain_enabled",
    "surface_main_chain_required",
    "surface_main_chain_sse_generator",
]
