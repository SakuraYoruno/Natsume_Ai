import { compactText } from '../../utils/digitalLife.js'

export function buildSelfModelGrowthView(growthSignals) {
  return {
    hasAnyGrowth: Boolean(growthSignals?.hasAnyGrowth),
    primarySummary: growthSignals?.primarySummary || '-',
    lastCycleLabel: growthSignals?.lastCycleLabel,
    cycleCount: growthSignals?.cycleCount || 0,
    activeThemes: growthSignals?.activeThemes || [],
    awarenessItems: growthSignals?.awarenessItems || [],
    insightSections: (growthSignals?.insightSections || []).map((item) => ({
      ...item,
      value: compactText(item.value, 240),
    })),
  }
}

export function buildSelfModelInfoRows({
  selfModel = {},
  selfReflection = {},
  guardrails = {},
  bondSummary = {},
  labels = {},
} = {}) {
  return {
    selfRows: [
      { label: '最近情绪趋势', value: labels.recentMoodTrendLabel },
      { label: '主导近期情绪', value: labels.dominantEmotionLabel },
      { label: '关系成长感知', value: selfModel.bond_growth_perception || '-' },
      { label: '共享记忆数', value: selfModel.shared_memory_count ?? 0 },
      {
        label: '最近深聊',
        value: selfModel.last_deep_talk_days_ago != null ? `${selfModel.last_deep_talk_days_ago} 天前` : '-',
      },
      { label: '人格变化数', value: labels.personalityChangeCount ?? 0 },
    ],
    reflectionRows: [
      { label: '自我感知', value: selfReflection.self_awareness || '-' },
      { label: '情绪感知', value: selfReflection.mood_awareness || '-' },
      { label: '成长笔记', value: selfReflection.growth_note || '-' },
      { label: '触发原因', value: labels.triggerReasonLabel },
      { label: '允许风格', value: labels.allowedStyleLabel },
      { label: '可扩展自叙事', value: guardrails.allow_self_narrative_expansion ? '是' : '否' },
    ],
    guardrailRows: [
      { label: '风险等级', value: labels.riskLevelLabel },
      { label: '最大自叙事句数', value: guardrails.max_self_narrative_sentences ?? '-' },
      { label: '护栏指令', value: guardrails.instruction || '-' },
    ],
    relationshipRows: [
      { label: '阶段', value: labels.relationshipStageLabel },
      { label: '趋势', value: labels.bondTrendLabel },
      { label: '能力提示', value: bondSummary.capability_hint || '-' },
      { label: '共享记忆', value: bondSummary.shared_memory_count ?? '—' },
      { label: '里程碑提示', value: bondSummary.milestone_hint || '-' },
    ],
  }
}

export function buildSelfModelListBlocks({ milestones = [], activeThemes = [], personalityChanges = [], promptDirectives = [] } = {}) {
  return [
    { title: '成长里程碑', items: milestones, emptyText: '当前还没有明确的成长里程碑。' },
    { title: '活跃成长主题', items: activeThemes, emptyText: '当前还没有形成稳定的成长主题。' },
    { title: '人格变化', items: personalityChanges, emptyText: '目前没有检测到可记录的人格变化。' },
    { title: '提示指令', items: promptDirectives, emptyText: '当前没有额外的提示指令。' },
  ].map((block) => ({
    ...block,
    items: block.items.map((item) => compactText(item, 220)),
  }))
}
