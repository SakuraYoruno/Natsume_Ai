import { resolveLoadState } from '../../models/loadState.js'
import { getGrowthSignals } from '../../utils/growthSignals.js'
import {
  asArray,
  formatAllowedStyle,
  formatDigitalLifeTag,
  formatEmotionTag,
  formatRiskLevel,
  formatStage,
  formatTrend,
} from '../../utils/digitalLife.js'
import {
  buildSelfModelHeroView,
  buildSelfModelMetricCards,
  buildSelfModelOrganModel,
  buildSelfModelSampleMeta,
  getSelfModelRiskTone,
} from './selfModelDashboardCards.js'
import {
  buildSelfModelGrowthView,
  buildSelfModelInfoRows,
  buildSelfModelListBlocks,
} from './selfModelDashboardSections.js'

export {
  buildSelfModelHeroView,
  buildSelfModelMetricCards,
  buildSelfModelOrganModel,
  buildSelfModelSampleMeta,
  getSelfModelRiskTone,
} from './selfModelDashboardCards.js'
export {
  buildSelfModelGrowthView,
  buildSelfModelInfoRows,
  buildSelfModelListBlocks,
} from './selfModelDashboardSections.js'

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
