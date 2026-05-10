from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.chat_agent import ChatAgentInput, chat_agent_sse_generator
from host_brain.app.chat_kernel_v2 import ChatKernelRequest, get_chat_kernel_v2
from host_brain.app.context.interaction_facts_plane import InteractionFactsPlane, assemble_interaction_facts_plane
from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.reply_envelope import coerce_reply_envelope, envelope_from_commit
from host_brain.app.response_form_contract import derive_render_profile_for_response_form
from host_brain.integrations.llm_gateway_schemas import ModelAttempt, PoolCompleteResult
from host_brain.integrations.llm_helpers import acomplete_llm_with_pool, invoke_llm_text


BASELINE_VARIANTS = (
    "soul_only_agent",
    "baseline_soul",
)
SOUL_AGENT_VARIANTS = {
    "soul_only_agent",
}
KERNEL_V2_VARIANTS = (
    "kernel_v2",
    "kernel_v2_plain_soul",
    "kernel_v2_plain_structured",
    "kernel_v2_guided",
    "kernel_v2_soul_full",
    "kernel_v2_style_bank",
    "kernel_v2_no_style_bank",
    "kernel_v2_soul_only",
    "kernel_v2_no_turn_directive",
    "kernel_v2_no_rerank",
)
CURRENT_MAIN_CHAIN_VARIANTS = (
    "current",
    "current_no_contract_prompt",
    "current_legacy_contract_prompt",
)


def load_dotenv(path: Path | None = None) -> None:
    dotenv_path = path or (ROOT / ".env")
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _load_secret_store_provider_key(provider_name: str) -> str:
    provider = str(provider_name or "").strip().lower()
    if not provider:
        return ""
    for path in (
        ROOT / "configs" / "secrets" / "secret_store.runtime.local.json",
        ROOT / "configs" / "secrets" / "secret_store.runtime.json",
    ):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        prefix = f"sec://{provider}/"
        for key, value in payload.items():
            if not str(key).lower().startswith(prefix):
                continue
            secret = str(value or "").strip()
            if secret and "<replace-" not in secret:
                return secret
    return ""


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{lineno} must be a JSON object")
            case_id = str(payload.get("case_id") or "").strip()
            user_message = str(payload.get("user_message") or "").strip()
            if not case_id or not user_message:
                raise ValueError(f"{path}:{lineno} requires case_id and user_message")
            rows.append(payload)
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def parse_variants(raw: str) -> list[str]:
    variants: list[str] = []
    for token in str(raw or "").split(","):
        item = token.strip()
        if not item:
            continue
        if item in CURRENT_MAIN_CHAIN_VARIANTS:
            variants.append(item)
            continue
        if item in BASELINE_VARIANTS:
            variants.append(item)
            continue
        if item in KERNEL_V2_VARIANTS:
            variants.append(item)
            continue
        raise ValueError(f"unsupported chat A/B variant: {item}")
    if not variants:
        raise ValueError("at least one variant is required")
    return variants


def is_retryable_generation_error(row: Mapping[str, Any]) -> bool:
    error = str(row.get("error") or "")
    if not error:
        trace = row.get("trace")
        if isinstance(trace, Mapping):
            error = str(trace.get("error") or "")
    retryable_markers = (
        "LLM_POOL_EXHAUSTED",
        "429",
        "RESOURCE_EXHAUSTED",
        "rate limit",
        "quota",
    )
    error_lower = error.lower()
    return any(marker.lower() in error_lower for marker in retryable_markers)


@contextmanager
def ab_eval_environment(
    *,
    disable_reply_contract_prompt: bool = False,
    legacy_reply_contract_prompt: bool = False,
) -> Iterable[None]:
    overrides = {
        "AB_EVAL_MODE": "true",
        "HOST_STREAM_TTS_ENABLED": "false",
        "HOST_TTS_SENTENCE_STREAM_ENABLED": "false",
        "HOST_CHAT_KERNEL_V2_ENABLED": "false",
    }
    if disable_reply_contract_prompt:
        overrides["HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT"] = "true"
    if legacy_reply_contract_prompt:
        overrides["HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT"] = "true"
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def temporary_environment(overrides: Mapping[str, str]) -> Iterable[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update({str(key): str(value) for key, value in overrides.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _extract_sse_payload(raw_event: str) -> dict[str, Any] | None:
    for raw_line in str(raw_event or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload_text = line.partition("data:")[2].strip()
        if not payload_text:
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


async def resolve_chat_runtime() -> dict[str, Any]:
    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        resolve_chat_agent_llm_runtime,
        resolve_host_llm_runtime_async,
    )

    base_runtime = await resolve_host_llm_runtime_async(scene_type="CHAT_AB")
    return resolve_chat_agent_llm_runtime(base_runtime=base_runtime)


async def generate_current_candidate(
    case: Mapping[str, Any],
    *,
    variant: str = "current",
    llm_runtime: Mapping[str, Any] | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    normalized_variant = str(variant or "current").strip() or "current"
    if normalized_variant not in CURRENT_MAIN_CHAIN_VARIANTS:
        raise ValueError(f"generate_current_candidate does not handle {normalized_variant}")
    case_id = str(case.get("case_id") or "").strip()
    user_message = str(case.get("user_message") or "").strip()
    started = time.perf_counter()
    if mock:
        text = f"current mock reply: {user_message}"
        envelope = envelope_from_commit(
            decision="APPROVE",
            narrative=text,
            source="current_main_chain_mock",
            cacheable=False,
            audit={"ab_eval": True, "mock": True},
        )
        return {
            "case_id": case_id,
            "variant": normalized_variant,
            "text": text,
            "envelope": envelope.as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {"source": "current_main_chain_mock"},
            "ok": True,
            "error": "",
        }

    commit_payload: dict[str, Any] = {}
    trace_events: list[dict[str, Any]] = []
    current_runtime = dict(llm_runtime or {})
    current_response_form = str(case.get("response_form") or current_runtime.get("response_form") or "").strip().lower()
    current_render_profile = derive_render_profile_for_response_form(current_response_form)
    if current_response_form:
        current_runtime["response_form"] = current_response_form
        current_runtime["planned_response_form"] = current_response_form
    current_runtime["render_profile"] = current_render_profile
    current_runtime["render_target_lang"] = (
        "ja" if current_render_profile == "ja_voice" else "media" if current_render_profile == "media_action" else "zh"
    )
    with ab_eval_environment(
        disable_reply_contract_prompt=(normalized_variant == "current_no_contract_prompt"),
        legacy_reply_contract_prompt=(normalized_variant == "current_legacy_contract_prompt"),
    ):
        agent_input = ChatAgentInput(
            request_id=f"ab_{normalized_variant}_{case_id}",
            session_id=f"ab_{normalized_variant}_{case_id}",
            person_id=str(case.get("person_id") or "ab_eval_user"),
            user_message=user_message,
            render_target_lang=str(current_runtime.get("render_target_lang") or "zh"),
            render_profile=current_render_profile,
            reply_channel=None,
            suppress_edge_tts=True,
            suppress_edge_vts=True,
            reply_depth_policy="normal",
            retrieval_summary=dict(case.get("retrieval_summary") or {"status": "SKIPPED"}),
            retrieval_evidence_items=list(case.get("retrieval_evidence_items") or []),
            emotion_context=dict(case.get("emotion_context") or case.get("state_facts") or {}),
            emotion_snapshot=dict(case.get("emotion_snapshot") or {}),
            bond_summary=dict(case.get("bond_summary") or {}),
            attachments=list(case.get("attachments") or []),
            multimodal_observations=dict(case.get("multimodal_facts") or {}),
            attachment_summary=str(case.get("attachment_summary") or ""),
            eval_history_turns=list(case.get("history_turns") or []),
            trace_stage_meta={
                "ab_eval": True,
                "case_id": case_id,
                "variant": normalized_variant,
                "reply_contract_prompt_disabled": normalized_variant == "current_no_contract_prompt",
                "legacy_reply_contract_prompt": normalized_variant == "current_legacy_contract_prompt",
            },
            llm_runtime=current_runtime,
        )
        try:
            async for event in chat_agent_sse_generator(agent_input):
                payload = _extract_sse_payload(event)
                if not payload:
                    continue
                phase = str(payload.get("phase") or "").strip().upper()
                if phase in {"COMMIT", "ERROR"}:
                    trace_events.append(payload)
                if phase == "COMMIT":
                    commit_payload = payload
        except Exception as exc:  # pragma: no cover - exercised by integration failures
            return {
                "case_id": case_id,
                "variant": normalized_variant,
                "text": "",
                "envelope": coerce_reply_envelope(None).as_dict(),
                "latency_ms": int(round((time.perf_counter() - started) * 1000)),
                "prompt_digest": "",
                "trace": {
                    "source": "current_main_chain",
                    "events": trace_events,
                    "reply_contract_prompt_disabled": normalized_variant == "current_no_contract_prompt",
                    "legacy_reply_contract_prompt": normalized_variant == "current_legacy_contract_prompt",
                },
                "ok": False,
                "error": repr(exc),
            }

    narrative = str(commit_payload.get("narrative") or "").strip()
    envelope = coerce_reply_envelope(
        commit_payload.get("reply_envelope")
        or envelope_from_commit(
            decision=str(commit_payload.get("decision") or ""),
            narrative=narrative,
            reason_code=str(commit_payload.get("reason_code") or ""),
            source="current_main_chain",
            cacheable=False,
            audit={"ab_eval": True},
        )
    )
    return {
        "case_id": case_id,
        "variant": normalized_variant,
        "text": str(envelope.text or narrative).strip(),
        "envelope": envelope.as_dict(),
        "latency_ms": int(round((time.perf_counter() - started) * 1000)),
        "prompt_digest": str(commit_payload.get("llm_input_digest") or ""),
        "trace": {
            "source": "current_main_chain",
            "commit": commit_payload,
            "events": trace_events,
            "reply_contract_prompt_disabled": normalized_variant == "current_no_contract_prompt",
            "legacy_reply_contract_prompt": normalized_variant == "current_legacy_contract_prompt",
        },
        "ok": bool(envelope.sendable),
        "error": "",
    }


def _build_kernel_runtime_payload(case: Mapping[str, Any], *, runtime: Mapping[str, Any], response_form: str, render_profile: str) -> dict[str, Any]:
    render_target_lang = "ja" if render_profile == "ja_voice" else "zh"
    return {
        "request": {
            "request_id": f"ab_kernel_{case.get('case_id')}",
            "session_id": f"ab_kernel_{case.get('case_id')}",
            "person_id": str(case.get("person_id") or "ab_eval_user"),
            "user_message": str(case.get("user_message") or "").strip(),
            "render_target_lang": render_target_lang,
            "render_profile": render_profile,
            "response_form": response_form,
            "attachment_summary": str(case.get("attachment_summary") or ""),
            "multimodal_observations": dict(case.get("multimodal_facts") or {}),
        },
        "render_target_lang": render_target_lang,
        "response_form": response_form,
        "render_profile": render_profile,
        "intent_draft": dict(case.get("intent_draft") or {}),
        "emotion_context": dict(case.get("emotion_context") or case.get("state_facts") or {}),
        "emotion": {"state": dict(case.get("emotion_snapshot") or {})},
        "bond_summary": dict(case.get("bond_summary") or {}),
        "prompt_context": {"bond_summary": dict(case.get("bond_summary") or {})},
        "retrieval": {
            **dict(case.get("retrieval_summary") or {"status": "SKIPPED"}),
            "evidence_items": list(case.get("retrieval_evidence_items") or []),
        },
        "multimodal_input": {
            "attachments": list(case.get("attachments") or []),
            "observations": dict(case.get("multimodal_facts") or {}),
        },
        "ab_eval": {"history_turns": list(case.get("history_turns") or [])},
        "llm_runtime": dict(runtime or {}),
    }


async def generate_kernel_v2_candidate(
    case: Mapping[str, Any],
    *,
    variant: str = "kernel_v2",
    llm_runtime: Mapping[str, Any] | None = None,
    mock: bool = False,
) -> dict[str, Any]:
    normalized_variant = str(variant or "kernel_v2").strip() or "kernel_v2"
    if normalized_variant not in KERNEL_V2_VARIANTS:
        raise ValueError(f"generate_kernel_v2_candidate does not handle {normalized_variant}")
    case_id = str(case.get("case_id") or "").strip()
    user_message = str(case.get("user_message") or "").strip()
    started = time.perf_counter()
    runtime = dict(llm_runtime or {})
    response_form = str(case.get("response_form") or runtime.get("response_form") or runtime.get("planned_response_form") or "text").strip().lower()
    render_profile = derive_render_profile_for_response_form(response_form)
    if response_form:
        runtime["response_form"] = response_form
        runtime["planned_response_form"] = response_form
    runtime["render_profile"] = render_profile
    runtime["render_target_lang"] = "ja" if render_profile == "ja_voice" else "zh"
    if mock:
        envelope = envelope_from_commit(
            decision="APPROVE",
            narrative=f"{normalized_variant} mock reply: {user_message}",
            response_form=response_form,
            source=normalized_variant,
            cacheable=False,
            audit={"ab_eval": True, "mock": True},
        )
        return {
            "case_id": case_id,
            "variant": normalized_variant,
            "text": envelope.text,
            "envelope": envelope.as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {"source": "chat_kernel_v2_mock"},
            "ok": True,
            "error": "",
        }
    try:
        env_overrides: dict[str, str] = {}
        if normalized_variant == "kernel_v2_style_bank":
            env_overrides["HOST_STYLE_REFERENCE_BANK_ENABLED"] = "true"
        if normalized_variant == "kernel_v2_no_style_bank":
            env_overrides["HOST_STYLE_REFERENCE_BANK_ENABLED"] = "false"
        if normalized_variant == "kernel_v2_plain_soul":
            env_overrides["HOST_CHAT_KERNEL_V2_PROMPT_MODE"] = "plain_soul"
            env_overrides["HOST_STYLE_REFERENCE_BANK_ENABLED"] = "false"
            env_overrides["HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED"] = "false"
            env_overrides["HOST_CHAT_KERNEL_V2_CANDIDATES"] = "1"
        if normalized_variant == "kernel_v2_plain_structured":
            env_overrides["HOST_CHAT_KERNEL_V2_PROMPT_MODE"] = "plain_structured"
            env_overrides["HOST_STYLE_REFERENCE_BANK_ENABLED"] = "false"
            env_overrides["HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED"] = "true"
            env_overrides["HOST_CHAT_KERNEL_V2_CANDIDATES"] = "4"
        if normalized_variant == "kernel_v2_guided":
            env_overrides["HOST_CHAT_KERNEL_V2_PROMPT_MODE"] = "guided"
        if normalized_variant == "kernel_v2_soul_only":
            env_overrides["HOST_STYLE_REFERENCE_BANK_ENABLED"] = "false"
            env_overrides["HOST_CHAT_KERNEL_V2_TURN_DIRECTIVE_ENABLED"] = "false"
        if normalized_variant == "kernel_v2_no_turn_directive":
            env_overrides["HOST_CHAT_KERNEL_V2_TURN_DIRECTIVE_ENABLED"] = "false"
        if normalized_variant == "kernel_v2_no_rerank":
            env_overrides["HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED"] = "false"
        with temporary_environment(env_overrides):
            if normalized_variant == "kernel_v2_soul_only":
                facts = InteractionFactsPlane(
                    user_message=user_message,
                    history_turns=[
                        dict(item or {})
                        for item in list(case.get("history_turns") or [])
                        if isinstance(item, Mapping)
                    ],
                )
            else:
                runtime_payload = _build_kernel_runtime_payload(
                    case,
                    runtime=runtime,
                    response_form=response_form,
                    render_profile=render_profile,
                )
                facts = assemble_interaction_facts_plane(runtime_payload=runtime_payload)
            result = await get_chat_kernel_v2().generate(
                ChatKernelRequest(
                    request_id=f"ab_{normalized_variant}_{case_id}",
                    session_id=f"ab_{normalized_variant}_{case_id}",
                    person_id=str(case.get("person_id") or "ab_eval_user"),
                    user_message=user_message,
                    history_turns=facts.history_turns,
                    compiled_soul=get_compiled_soul(),
                    interaction_facts=facts,
                    response_form=response_form,
                    render_profile=render_profile,
                    llm_runtime=runtime,
                    eval_mode=True,
                )
            )
        envelope = result.reply_envelope
        return {
            "case_id": case_id,
            "variant": normalized_variant,
            "text": str(envelope.text or "").strip(),
            "envelope": envelope.as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": result.prompt_digest,
            "trace": {
                "source": "chat_kernel_v2",
                "result": result.as_dict(),
            },
            "ok": bool(envelope.sendable),
            "error": "",
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "variant": normalized_variant,
            "text": "",
            "envelope": coerce_reply_envelope(None).as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {"source": "chat_kernel_v2", "error": repr(exc)},
            "ok": False,
            "error": repr(exc),
        }


def _build_baseline_messages(case: Mapping[str, Any]) -> list[dict[str, str]]:
    soul = get_compiled_soul()
    system_parts = [
        str(getattr(soul, "runtime_full_text", "") or "").strip(),
        (
            "只输出新海天会发给尼尼的一条私聊回复。"
            "不要解释、不要 JSON、不要括号动作、不要舞台旁白、不要复述规则。"
            "文字回复使用中文；称呼和关系以 Soul.md 为准。"
        ),
    ]
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "\n\n".join(part for part in system_parts if part).strip()}
    ]
    for turn in list(case.get("history_turns") or []):
        if not isinstance(turn, Mapping):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(case.get("user_message") or "").strip()})
    return messages


def _read_raw_soul_md() -> str:
    path = Path(os.environ.get("SOUL_MD_PATH") or ROOT / "Soul.md")
    return path.read_text(encoding="utf-8-sig").strip()


def _env_float(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _env_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _build_soul_only_agent_messages(case: Mapping[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": _read_raw_soul_md()}]
    for turn in list(case.get("history_turns") or []):
        if not isinstance(turn, Mapping):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(case.get("user_message") or "").strip()})
    return messages


async def _complete_soul_agent_once(
    *,
    normalized: str,
    case: Mapping[str, Any],
    runtime: Mapping[str, Any],
    case_id: str,
    gen_params: Mapping[str, Any],
    candidate_index: int | None = None,
    messages_override: list[dict[str, str]] | None = None,
) -> Any:
    if messages_override is not None:
        messages = messages_override
    elif normalized == "soul_only_agent":
        messages = _build_soul_only_agent_messages(case)
    else:
        messages = _build_baseline_messages(case)
    provider_name = str(runtime.get("provider_name") or runtime.get("chat_provider_name") or "").strip()
    model_id = str(runtime.get("model_id") or runtime.get("chat_model_id") or "").strip()
    if provider_name and model_id and normalized in SOUL_AGENT_VARIANTS and str(os.environ.get("CHAT_AB_SOUL_AGENT_BYPASS_POOL", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        api_key = str(runtime.get("api_key") or runtime.get("chat_api_key") or "").strip()
        if not api_key:
            env_key = {
                "deepseek": "DEEPSEEK_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "minimax": "MINIMAX_API_KEY",
                "openai": "OPENAI_API_KEY",
                "codex": "CODEX_API_KEY",
            }.get(provider_name.lower(), "")
            api_key = str(os.environ.get(env_key or "") or "").strip()
        if not api_key:
            api_key = _load_secret_store_provider_key(provider_name)
        timeout_ms = int(runtime.get("timeout_ms") or runtime.get("chat_timeout_ms") or 45000)
        base_url = str(runtime.get("base_url_override") or runtime.get("base_url") or "").strip() or None
        direct_started = time.perf_counter()
        raw_text = await asyncio.to_thread(
            invoke_llm_text,
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=dict(gen_params),
            base_url_override=base_url,
        )
        latency_ms = int(round((time.perf_counter() - direct_started) * 1000))
        return PoolCompleteResult(
            content=str(raw_text or "").strip(),
            final_model=model_id,
            final_provider=provider_name,
            attempts=[
                ModelAttempt(
                    model=model_id,
                    provider=provider_name,
                    status="OK",
                    latency_ms=latency_ms,
                    reason_code="CHAT_AB_DIRECT",
                    detail="bypassed llm pool cooldown for baseline text-quality probe",
                )
            ],
            pool_exhausted=False,
            total_latency_ms=latency_ms,
            cached=False,
            cognitive_tier="DIRECT",
        )
    return await acomplete_llm_with_pool(
        request_id=f"ab_{normalized}_{case_id}",
        scene_type="CHAT_AB_SOUL_ONLY" if normalized in SOUL_AGENT_VARIANTS else "CHAT_AB_BASELINE",
        cognitive_tier="A",
        provider_name=str(runtime.get("provider_name") or runtime.get("chat_provider_name") or "").strip() or None,
        model_id=str(runtime.get("model_id") or runtime.get("chat_model_id") or "").strip() or None,
        api_key=str(runtime.get("api_key") or runtime.get("chat_api_key") or "").strip() or None,
        timeout_ms=int(runtime.get("timeout_ms") or runtime.get("chat_timeout_ms") or 45000),
        messages=messages,
        gen_params=dict(gen_params),
        base_url_override=str(runtime.get("base_url_override") or runtime.get("base_url") or "").strip() or None,
    )


async def generate_baseline_candidate(
    case: Mapping[str, Any],
    *,
    variant: str,
    llm_runtime: Mapping[str, Any] | None = None,
    baseline_provider: str = "",
    baseline_model_id: str = "",
    mock: bool = False,
) -> dict[str, Any]:
    normalized = parse_variants(variant)[0]
    if normalized == "current":
        raise ValueError("generate_baseline_candidate does not handle current")
    case_id = str(case.get("case_id") or "").strip()
    user_message = str(case.get("user_message") or "").strip()
    started = time.perf_counter()
    runtime = dict(llm_runtime or {})
    response_form = str(
        case.get("response_form") or runtime.get("response_form") or runtime.get("planned_response_form") or "text"
    ).strip().lower()
    render_profile = derive_render_profile_for_response_form(response_form)
    render_target_lang = "ja" if render_profile == "ja_voice" else "media" if render_profile == "media_action" else "zh"
    if response_form:
        runtime["response_form"] = response_form
        runtime["planned_response_form"] = response_form
    runtime["render_profile"] = render_profile
    runtime["render_target_lang"] = render_target_lang
    provider_override = str(baseline_provider or "").strip()
    if provider_override:
        runtime["provider_name"] = provider_override
        runtime["chat_provider_name"] = provider_override
    model_override = str(baseline_model_id or "").strip()
    if model_override:
        runtime["model_id"] = model_override
        runtime["chat_model_id"] = model_override
    if not str(runtime.get("api_key") or runtime.get("chat_api_key") or "").strip():
        resolved_provider = str(runtime.get("provider_name") or runtime.get("chat_provider_name") or "").strip()
        secret_key = _load_secret_store_provider_key(resolved_provider)
        if secret_key:
            runtime["api_key"] = secret_key
            runtime["chat_api_key"] = secret_key
    if mock:
        text = f"{normalized} mock reply: {user_message}"
        envelope = envelope_from_commit(
            decision="APPROVE",
            narrative=text,
            response_form=response_form,
            source=normalized,
            cacheable=False,
            audit={"ab_eval": True, "mock": True},
        )
        return {
            "case_id": case_id,
            "variant": normalized,
            "text": text,
            "envelope": envelope.as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {
                "source": normalized if normalized in SOUL_AGENT_VARIANTS else "simple_soul_baseline",
                "render_target_lang": render_target_lang,
            },
            "ok": True,
            "error": "",
        }
    try:
        if normalized in SOUL_AGENT_VARIANTS:
            gen_params = {
                "temperature": _env_float("CHAT_AB_SOUL_ONLY_TEMPERATURE", default=0.32, minimum=0.0, maximum=1.5),
                "top_p": _env_float("CHAT_AB_SOUL_ONLY_TOP_P", default=0.82, minimum=0.1, maximum=1.0),
                "max_tokens": _env_int("CHAT_AB_SOUL_ONLY_MAX_TOKENS", default=40, minimum=16, maximum=256),
            }
        else:
            gen_params = {"temperature": 0.62, "top_p": 0.9}
        completion = await _complete_soul_agent_once(
            normalized=normalized,
            case=case,
            runtime=runtime,
            case_id=case_id,
            gen_params=gen_params,
        )
        raw_text = str(getattr(completion, "content", "") or "").strip()
        text = raw_text
        candidate_audit = {}
        envelope = envelope_from_commit(
            decision="APPROVE" if text else "NO_REPLY",
            narrative=text,
            response_form=response_form,
            source=normalized,
            cacheable=False,
            audit={
                "ab_eval": True,
                "final_provider": str(getattr(completion, "final_provider", "") or ""),
                "final_model": str(getattr(completion, "final_model", "") or ""),
                "pool_exhausted": bool(getattr(completion, "pool_exhausted", False)),
                **candidate_audit,
            },
        )
        return {
            "case_id": case_id,
            "variant": normalized,
            "text": str(envelope.text or text).strip(),
            "envelope": envelope.as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {
                "source": normalized if normalized in SOUL_AGENT_VARIANTS else "simple_soul_baseline",
                "raw_content": "",
                "render_target_lang": render_target_lang,
                "final_provider": str(getattr(completion, "final_provider", "") or ""),
                "final_model": str(getattr(completion, "final_model", "") or ""),
                "pool_exhausted": bool(getattr(completion, "pool_exhausted", False)),
                **candidate_audit,
            },
            "ok": bool(envelope.sendable),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - exercised by live provider failures
        return {
            "case_id": case_id,
            "variant": normalized,
            "text": "",
            "envelope": coerce_reply_envelope(None).as_dict(),
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "prompt_digest": "",
            "trace": {"source": "simple_soul_baseline", "error": repr(exc)},
            "ok": False,
            "error": repr(exc),
        }


async def generate_candidates(
    *,
    cases: list[Mapping[str, Any]],
    variants: list[str],
    mock: bool = False,
    baseline_provider: str = "",
    baseline_model_id: str = "",
    case_delay_ms: int = 0,
    max_case_retries: int = 0,
    error_backoff_ms: int = 0,
) -> list[dict[str, Any]]:
    llm_runtime: dict[str, Any] = {}
    if not mock:
        llm_runtime = await resolve_chat_runtime()

    rows: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        for variant_index, variant in enumerate(variants):
            attempt = 0
            row: dict[str, Any]
            while True:
                if variant in CURRENT_MAIN_CHAIN_VARIANTS:
                    row = await generate_current_candidate(
                        case,
                        variant=variant,
                        llm_runtime=llm_runtime,
                        mock=mock,
                    )
                elif variant in KERNEL_V2_VARIANTS:
                    row = await generate_kernel_v2_candidate(
                        case,
                        variant=variant,
                        llm_runtime=llm_runtime,
                        mock=mock,
                    )
                else:
                    row = await generate_baseline_candidate(
                        case,
                        variant=variant,
                        llm_runtime=llm_runtime,
                        baseline_provider=baseline_provider,
                        baseline_model_id=baseline_model_id,
                        mock=mock,
                    )
                if not is_retryable_generation_error(row) or attempt >= int(max_case_retries or 0):
                    if attempt:
                        trace = dict(row.get("trace") or {})
                        trace["generation_retry_attempts"] = attempt
                        row["trace"] = trace
                    rows.append(row)
                    break
                attempt += 1
                await asyncio.sleep(max(0, int(error_backoff_ms or case_delay_ms or 0)) / 1000.0)
            if int(case_delay_ms or 0) > 0 and not (case_index == len(cases) - 1 and variant_index == len(variants) - 1):
                await asyncio.sleep(max(0, int(case_delay_ms)) / 1000.0)
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate current/kernel A/B chat candidates without QQ side effects.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--variants", default="current,kernel_v2")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mock", action="store_true", help="Use deterministic mock replies instead of LLM calls.")
    parser.add_argument("--baseline-provider", default=os.environ.get("CHAT_AB_BASELINE_PROVIDER", ""))
    parser.add_argument("--baseline-model", default=os.environ.get("CHAT_AB_BASELINE_MODEL_ID", ""))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-delay-ms", type=int, default=int(os.environ.get("CHAT_AB_CASE_DELAY_MS", "0") or 0))
    parser.add_argument("--max-case-retries", type=int, default=int(os.environ.get("CHAT_AB_MAX_CASE_RETRIES", "0") or 0))
    parser.add_argument("--error-backoff-ms", type=int, default=int(os.environ.get("CHAT_AB_ERROR_BACKOFF_MS", "0") or 0))
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    load_dotenv()
    cases = load_cases(args.cases)
    if int(args.limit or 0) > 0:
        cases = cases[: int(args.limit)]
    variants = parse_variants(str(args.variants or "current,kernel_v2"))
    rows = await generate_candidates(
        cases=cases,
        variants=variants,
        mock=bool(args.mock),
        baseline_provider=str(args.baseline_provider or ""),
        baseline_model_id=str(args.baseline_model or ""),
        case_delay_ms=int(args.case_delay_ms or 0),
        max_case_retries=int(args.max_case_retries or 0),
        error_backoff_ms=int(args.error_backoff_ms or 0),
    )
    out_file = args.out / "raw_candidates.jsonl"
    write_jsonl(out_file, rows)
    print(f"wrote {len(rows)} candidates -> {out_file}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
