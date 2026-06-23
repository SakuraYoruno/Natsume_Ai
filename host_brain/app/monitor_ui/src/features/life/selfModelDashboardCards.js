import { buildSelfOrganSystemModel } from '../../models/organSystemModel.js'
import { compactText, formatNumber } from '../../utils/digitalLife.js'

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
