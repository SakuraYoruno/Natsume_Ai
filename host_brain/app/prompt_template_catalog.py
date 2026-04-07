from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.app.prompt_template_store import PromptTemplateStore


@dataclass(frozen=True)
class PersonaTemplateCatalogEntryDefinition:
    template_id: str
    title: str
    description: str
    focus_keys: tuple[str, ...] = ()
    operator_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonaTemplateCatalogBundleDefinition:
    bundle_id: str
    title: str
    description: str
    entries: tuple[PersonaTemplateCatalogEntryDefinition, ...]


PERSONA_FIRST_BATCH_BUNDLES: tuple[PersonaTemplateCatalogBundleDefinition, ...] = (
    PersonaTemplateCatalogBundleDefinition(
        bundle_id="self_model",
        title="自我叙事文本池",
        description="影响自我感知、关系变化、时间语境和成长备注的第一人称叙事文本。",
        entries=(
            PersonaTemplateCatalogEntryDefinition(
                template_id="self_model.narrative_text_pools",
                title="Self Model 文本池",
                description="控制情绪趋势、关系趋势、时间上下文、矛盾态和成长备注的自然语言表达。",
                focus_keys=(
                    "mood_text_pool",
                    "bond_text_pool",
                    "time_context_text_pool",
                    "contradiction_text_pool",
                    "mood_awareness_pool",
                    "growth_note_wrappers",
                ),
                operator_notes=(
                    "优先改语气、节奏和亲密度，不要改键名。",
                    "这一组最直接影响数字生命的自我叙事质感。",
                ),
            ),
        ),
    ),
    PersonaTemplateCatalogBundleDefinition(
        bundle_id="proactive_voice",
        title="主动表达文案",
        description="影响主动消息、回访、晨间唤醒、aftercare 和游戏陪伴表达。",
        entries=(
            PersonaTemplateCatalogEntryDefinition(
                template_id="proactive_voice.runtime_texts",
                title="Proactive Voice 运行文案",
                description="控制主动沟通时的默认跟进句、重连句、晚安后照料、晨间轻唤和场景默认回复。",
                focus_keys=(
                    "guarded_soften_followup",
                    "resonance_followup",
                    "warm_followup",
                    "resonance_reconnect",
                    "warm_aftercare",
                    "wakeup_gentle_morning",
                    "gaming_companion_followup",
                ),
                operator_notes=(
                    "适合调节主动说话时的温度、陪伴感和黏性。",
                    "建议保持“轻一点、短一点、别像系统消息”。",
                ),
            ),
        ),
    ),
    PersonaTemplateCatalogBundleDefinition(
        bundle_id="expression_adapter",
        title="表达适配文案",
        description="影响回复时的风格提示、节奏提示和表达策略说明。",
        entries=(
            PersonaTemplateCatalogEntryDefinition(
                template_id="expression_adapter.prompt_section.templates",
                title="表达策略片段",
                description="控制风格、语气、共鸣、追问、节奏这些提示段的标题和说明文案。",
                focus_keys=(
                    "title",
                    "tone_guidance",
                    "echo_high",
                    "follow_up_high",
                    "pace_rapid",
                    "pace_deep",
                ),
                operator_notes=(
                    "适合运营调“怎么看起来更像夏目蓝在说话”。",
                ),
            ),
            PersonaTemplateCatalogEntryDefinition(
                template_id="expression_adapter.style_hint.templates",
                title="表达风格提示",
                description="控制同理、幽默、好奇心、关系距离和对话节奏这些风格提示词。",
                focus_keys=(
                    "core",
                    "traits",
                    "bond",
                    "pace",
                ),
                operator_notes=(
                    "这一组更偏表达调味，不建议改成硬命令口吻。",
                ),
            ),
        ),
    ),
)


def build_persona_first_batch_catalog(store: PromptTemplateStore) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for bundle in PERSONA_FIRST_BATCH_BUNDLES:
        entries: list[dict[str, Any]] = []
        for entry in bundle.entries:
            entries.append(
                {
                    "template_id": entry.template_id,
                    "title": entry.title,
                    "description": entry.description,
                    "focus_keys": list(entry.focus_keys),
                    "operator_notes": list(entry.operator_notes),
                    "item": store.get_template(entry.template_id),
                }
            )
        bundles.append(
            {
                "bundle_id": bundle.bundle_id,
                "title": bundle.title,
                "description": bundle.description,
                "template_count": len(entries),
                "entries": entries,
            }
        )
    return bundles
