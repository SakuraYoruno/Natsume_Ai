import { resolveLoadState } from '../../models/loadState.js'
import { buildSelfOrganSystemModel } from '../../models/organSystemModel.js'
import { getGrowthSignals } from '../../utils/growthSignals.js'
import {
  asArray,
  compactText,
  formatAllowedStyle,
  formatDigitalLifeTag,
  formatEmotionTag,
  formatNumber,
  formatRiskLevel,
  formatStage,
  formatTrend,
} from '../../utils/digitalLife.js'

export const SELF_MODEL_RUNTIME_WINDOW_MINUTES = 60

export function getSelfModelRuntimePieces(runtimeSnapshot) {
  return {
    selfModel: runtimeSnapshot?.self_model_snapshot || {},
    selfReflection: runtimeSnapshot?.self_reflection_pulse || {},
    bondSummary: runtimeSnapshot?.bond_summary_human || {},
    bondState: runtimeSnapshot?.bond_state || {},
  }
}

export function buildSelfModelCollections({ selfModel = {}, guardrails = {} } = {}) {
  return {
    milestones: asArray(selfModel.growth_milestones),
    personalityChanges: asArray(selfModel.personality_changes),
    riskTags: asArray(guardrails.risk_tags),
    promptDirectives: asArray(guardrails.prompt_directives),
  }
}

export function buildSelfModelLabels({
  selfModel = {},
  selfReflection = {},
  guardrails = {},
  bondSummary = {},
  bondState = {},
} = {}) {
  const riskLevelRaw = String(guardrails.risk_level || '').toLowerCase()
  return {
    riskLevelRaw,
    riskLevelLabel: formatRiskLevel(riskLevelRaw),
    allowedStyleLabel: formatAllowedStyle(guardrails.allowed_style),
    recentMoodTrendLabel: selfModel.recent_mood_trend ? formatTrend(selfModel.recent_mood_trend) : '平稳',
    dominantEmotionLabel: selfModel.dominant_recent_emotion
      ? formatEmotionTag(selfModel.dominant_recent_emotion)
      : '-',
    triggerReasonLabel: selfReflection.trigger_reason ? formatDigitalLifeTag(selfReflection.trigger_reason) : '-',
    bondTrendLabel: (bondSummary.trend || bondState?.trend)
      ? formatTrend(bondSummary.trend || bondState?.trend)
      : '-',
    bondStageLabel: formatStage(bondState?.current_stage),
    relationshipStageLabel: formatStage(bondSummary.stage || bondState?.current_stage),
  }
}

export function formatSelfModelTags(items = []) {
  return items.map(formatDigitalLifeTag).filter(Boolean)
}

export function buildSelfModelGrowthHeadline({ growthSignals, selfReflection = {} } = {}) {
  return growthSignals?.primarySummary
    || selfReflection.growth_note
    || selfReflection.self_awareness
    || '当前暂无明显成长摘要。'
}

export function buildSelfModelLoadState({
  loading = false,
  error = null,
  runtimeSnapshot = null,
  growthSignals,
  milestones = [],
  personalityChanges = [],
  riskTags = [],
} = {}) {
  return resolveLoadState({
    loading,
    error: error ? new Error(String(error)) : null,
    hasData: Boolean(runtimeSnapshot),
    hasSamples: Boolean(
      growthSignals?.hasAnyGrowth
      || milestones.length > 0
      || personalityChanges.length > 0
      || riskTags.length > 0,
    ),
    available: true,
  })
}

export function getSelfModelUpdatedAt(runtimeSnapshot) {
  return Number(runtimeSnapshot?.timestamp || 0) || null
}

export function getSelfModelRiskTone(riskLevelRaw) {
  if (riskLevelRaw === 'high' || riskLevelRaw === 'critical') return 'text-red-500'
  if (riskLevelRaw === 'medium') return 'text-orange-500'
  return 'text-green-600'
}

export function buildSelfModelHeroView({
  recentMoodTrendLabel,
  growthHeadline,
  bondStageLabel,
  sharedMemoryCount = 0,
  growthCycleCount = 0,
  riskLevelLabel,
} = {}) {
  return {
    title: recentMoodTrendLabel,
    description: compactText(growthHeadline, 220),
    metaItems: [
      `阶段：${bondStageLabel}`,
      `共享记忆：${sharedMemoryCount ?? 0}`,
      `成长周期：${growthCycleCount || 0}`,
      `风险：${riskLevelLabel}`,
    ],
  }
}

export function buildSelfModelMetricCards({
  selfModel = {},
  labels = {},
  growthSignals,
  milestones = [],
  personalityChanges = [],
  riskTags = [],
} = {}) {
  return [
    {
      label: '情绪稳定性',
      value: formatNumber(selfModel.emotional_stability, 2),
      hint: labels.recentMoodTrendLabel,
      tone: 'text-blue-600',
    },
    {
      label: '共享记忆',
      value: selfModel.shared_memory_count ?? 0,
      hint: selfModel.last_deep_talk_days_ago != null
        ? `最近深聊：${selfModel.last_deep_talk_days_ago} 天前`
        : '最近暂无深聊记录',
      tone: 'text-pink-600',
    },
    { label: '成长里程碑', value: milestones.length, hint: '已记录节点', tone: 'text-purple-600' },
    {
      label: '成长周期',
      value: growthSignals?.cycleCount || 0,
      hint: growthSignals?.lastCycleLabel !== '—' ? `最近周期 ${growthSignals?.lastCycleLabel}` : '尚未形成稳定周期',
      tone: 'text-fuchsia-600',
    },
    {
      label: '活跃主题',
      value: growthSignals?.activeThemes?.length || 0,
      hint: growthSignals?.activeThemes?.length > 0 ? compactText(growthSignals.activeThemes.join('、'), 36) : '暂无主题聚合',
      tone: 'text-indigo-600',
    },
    {
      label: '护栏风险',
      value: labels.riskLevelLabel,
      hint: `${riskTags.length} 个风险标签`,
      tone: getSelfModelRiskTone(labels.riskLevelRaw),
    },
  ]
}

export function buildSelfModelSampleMeta({ loadState, updatedAt, milestones = [], personalityChanges = [], growthSignals } = {}) {
  return {
    loadState,
    sampleSize: milestones.length + personalityChanges.length + (growthSignals?.activeThemes?.length || 0),
    sampleLabel: '成长样本',
    updatedAt,
    source: 'runtime snapshot / self model',
    definition: '自我系统页聚合自我模型、成长脉冲与叙事护栏；无样本与暂不可用会按统一口径展示。',
  }
}

export function buildSelfModelOrganModel({
  loadState,
  updatedAt,
  selfModel = {},
  labels = {},
  growthSignals,
  growthHeadline,
  milestones = [],
  personalityChanges = [],
  promptDirectives = [],
  allowedStyleLabel,
} = {}) {
  return buildSelfOrganSystemModel({
    loadState,
    updatedAt,
    recentMoodTrendLabel: labels.recentMoodTrendLabel,
    riskLevelLabel: labels.riskLevelLabel,
    riskLevelRaw: labels.riskLevelRaw,
    growthHeadline,
    sharedMemoryCount: selfModel.shared_memory_count,
    milestoneCount: milestones.length,
    growthCycleCount: growthSignals?.cycleCount,
    activeThemeCount: growthSignals?.activeThemes?.length || 0,
    personalityChangeCount: personalityChanges.length,
    promptDirectiveCount: promptDirectives.length,
    bondStageLabel: labels.bondStageLabel,
    allowedStyleLabel,
  })
}

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

export function buildSelfModelDashboardViewModel({ runtimeSnapshot, loading = false, error = null } = {}) {
  const { selfModel, selfReflection, bondSummary, bondState } = getSelfModelRuntimePieces(runtimeSnapshot)
  const guardrails = selfReflection?.narrative_guardrails || {}
  const growthSignals = getGrowthSignals(runtimeSnapshot)
  const collections = buildSelfModelCollections({ selfModel, guardrails })
  const formattedRiskTags = formatSelfModelTags(collections.riskTags)
  const labels = buildSelfModelLabels({ selfModel, selfReflection, guardrails, bondSummary, bondState })
  const enrichedLabels = {
    ...labels,
    allowedStyleLabel: labels.allowedStyleLabel,
    personalityChangeCount: collections.personalityChanges.length,
  }
  const growthHeadline = buildSelfModelGrowthHeadline({ growthSignals, selfReflection })
  const loadState = buildSelfModelLoadState({
    loading,
    error,
    runtimeSnapshot,
    growthSignals,
    milestones: collections.milestones,
    personalityChanges: collections.personalityChanges,
    riskTags: collections.riskTags,
  })
  const updatedAt = getSelfModelUpdatedAt(runtimeSnapshot)

  return {
    selfModel,
    selfReflection,
    guardrails,
    bondSummary,
    growthSignals,
    ...collections,
    formattedRiskTags,
    labels: enrichedLabels,
    growthHeadline,
    loadState,
    updatedAt,
    heroView: buildSelfModelHeroView({
      recentMoodTrendLabel: labels.recentMoodTrendLabel,
      growthHeadline,
      bondStageLabel: labels.bondStageLabel,
      sharedMemoryCount: selfModel.shared_memory_count,
      growthCycleCount: growthSignals.cycleCount,
      riskLevelLabel: labels.riskLevelLabel,
    }),
    sampleMeta: buildSelfModelSampleMeta({
      loadState,
      updatedAt,
      milestones: collections.milestones,
      personalityChanges: collections.personalityChanges,
      growthSignals,
    }),
    organModel: buildSelfModelOrganModel({
      loadState,
      updatedAt,
      selfModel,
      labels,
      growthSignals,
      growthHeadline,
      milestones: collections.milestones,
      personalityChanges: collections.personalityChanges,
      promptDirectives: collections.promptDirectives,
      allowedStyleLabel: labels.allowedStyleLabel,
    }),
    metricCards: buildSelfModelMetricCards({
      selfModel,
      labels,
      growthSignals,
      milestones: collections.milestones,
      personalityChanges: collections.personalityChanges,
      riskTags: collections.riskTags,
    }),
    growthView: buildSelfModelGrowthView(growthSignals),
    infoRows: buildSelfModelInfoRows({
      selfModel,
      selfReflection,
      guardrails,
      bondSummary,
      labels: enrichedLabels,
    }),
    listBlocks: buildSelfModelListBlocks({
      milestones: collections.milestones,
      activeThemes: growthSignals.activeThemes,
      personalityChanges: collections.personalityChanges,
      promptDirectives: collections.promptDirectives,
    }),
  }
}
