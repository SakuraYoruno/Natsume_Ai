import { resolveLoadState } from '../../models/loadState.js'
import { EMOTION_MOOD_STYLES, ENDOCRINE_GATE_LABELS } from '../../shared/statusMeta.js'
import { formatEmotionTag, formatNumber, formatStage } from '../../utils/digitalLife.js'
import {
  buildEmotionHeroView,
  buildEmotionLimbicOrganModel,
  buildEmotionMemorySubsystemRows,
  buildEmotionMetricCards,
  buildEmotionNoticeView,
  buildEmotionTopEmotionRows,
  buildEmotionVtsObservation,
  buildEmotionVtsStatCards,
} from './emotionDashboardViews.js'

export {
  buildEmotionHeroView,
  buildEmotionLimbicOrganModel,
  buildEmotionMemorySubsystemRows,
  buildEmotionMetricCards,
  buildEmotionNoticeView,
  buildEmotionTopEmotionRows,
  buildEmotionVtsObservation,
  buildEmotionVtsStatCards,
} from './emotionDashboardViews.js'

export const EMOTION_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export const EMOTION_GAUGE_COLOR_MAP = {
  'text-green-600': '#22c55e',
  'text-blue-600': '#3b82f6',
  'text-purple-600': '#a855f7',
}

const EMOTION_UNAVAILABLE_MOOD_STYLE = Object.freeze({
  color: 'text-gray-400',
  bg: 'bg-gray-50',
  label: '待判定',
})

function finiteNumberOrNull(value) {
  if (value == null || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function normalizedTokenOrNull(value, transform = (item) => item) {
  const normalized = String(value ?? '').trim()
  return normalized ? transform(normalized) : null
}

export function clampEmotionUnitValue(value) {
  const numeric = finiteNumberOrNull(value)
  return numeric == null ? null : Math.max(0, Math.min(1, numeric))
}

export function buildEmotionHormoneBarView({ label, value, color }) {
  const numeric = clampEmotionUnitValue(value)
  if (numeric == null) {
    return {
      label,
      color,
      value: null,
      valueLabel: '不可测',
      width: '0%',
      available: false,
    }
  }
  return {
    label,
    color,
    value: numeric,
    valueLabel: numeric.toFixed(2),
    width: `${(numeric * 100).toFixed(0)}%`,
    available: true,
  }
}
export function buildEmotionGaugeView({ label, value, color }) {
  const numeric = finiteNumberOrNull(value)
  if (numeric == null) {
    return {
      label,
      value: null,
      color: 'text-gray-400',
      valueLabel: '不可测',
      width: '0%',
      backgroundColor: '#e5e7eb',
      available: false,
    }
  }
  const percent = ((numeric + 1) / 2) * 100

  return {
    label,
    value: numeric,
    color,
    valueLabel: numeric.toFixed(2),
    width: `${percent.toFixed(0)}%`,
    backgroundColor: EMOTION_GAUGE_COLOR_MAP[color] || '#a855f7',
    available: true,
  }
}

export function getEmotionRuntimeContext(runtimeSnapshot) {
  return {
    emotionContext: runtimeSnapshot?.emotion_context || {},
    selfModel: runtimeSnapshot?.self_model_snapshot || {},
    bondState: runtimeSnapshot?.bond_state || {},
  }
}

export function getEmotionEndocrineState(runtimeSnapshot, emotionContext = {}) {
  return runtimeSnapshot?.endocrine_state || emotionContext?.endocrine_state || {}
}

export function getEmotionTopEmotions(vtsData) {
  return Array.isArray(vtsData?.top_emotions) ? vtsData.top_emotions : []
}

export function getEmotionBondMetrics(bondState = {}) {
  const eventCount = finiteNumberOrNull(bondState?.event_count)
  const temperature = eventCount == null ? null : clampEmotionUnitValue(eventCount / 500)
  return {
    bondEventCount: eventCount,
    bondTemperature: temperature,
    bondTemperatureLabel: temperature == null ? '不可测' : `${(temperature * 100).toFixed(0)}%`,
    bondTemperatureDegree: temperature == null ? '不可测' : `${(temperature * 100).toFixed(0)}°`,
    bondTemperatureFormatted: temperature == null ? '不可测' : formatNumber(temperature, 2),
    bondTemperatureArc: temperature == null ? '0 314' : `${temperature * 314} 314`,
  }
}

export function buildEmotionState({ emotionContext = {}, selfModel = {}, bondState = {} } = {}) {
  const emotionIntensity = finiteNumberOrNull(
    emotionContext?.emotion_intensity ?? emotionContext?.emotion_timeline_summary?.avg_intensity,
  )

  return {
    valence: finiteNumberOrNull(
      emotionContext?.user_emotion_profile?.avg_valence
        ?? emotionContext?.emotion_timeline_summary?.avg_valence,
    ),
    arousal: finiteNumberOrNull(emotionContext?.user_emotion_profile?.avg_arousal ?? emotionIntensity),
    dominance: finiteNumberOrNull(selfModel?.emotional_stability),
    mood: normalizedTokenOrNull(emotionContext?.mood_state, (value) => value.toLowerCase()),
    emotionTag: normalizedTokenOrNull(emotionContext?.emotion_tag, (value) => value.toUpperCase()),
    bondStage: normalizedTokenOrNull(bondState?.current_stage, (value) => value.toUpperCase()),
  }
}

export function buildEmotionGateView(endocrineState = {}) {
  const gateState = normalizedTokenOrNull(endocrineState?.gate_state, (value) => value.toLowerCase())
  return {
    gateState,
    gateLabel: gateState ? ENDOCRINE_GATE_LABELS[gateState] || gateState : '待判定',
    gateTone: gateState === 'protective'
      ? 'text-red-600'
      : gateState === 'cautious'
        ? 'text-orange-500'
        : gateState === 'open'
          ? 'text-green-600'
          : 'text-gray-400',
  }
}

export function getEmotionSampleState({ runtimeSnapshot, vtsData, memoryData, topEmotions = [] } = {}) {
  const sampleCount = vtsData?.total_events == null
    ? null
    : Number(vtsData.total_events)
  const runtimeEmotionContext = runtimeSnapshot?.emotion_context
  const hasRuntimeEmotionSample = Boolean(
    runtimeEmotionContext
    && typeof runtimeEmotionContext === 'object'
    && Object.keys(runtimeEmotionContext).length > 0,
  )
  return {
    observabilityData: Boolean(runtimeSnapshot || vtsData || memoryData),
    sampleCount,
    hasSamples: hasRuntimeEmotionSample || Number(sampleCount || 0) > 0,
  }
}

export function buildEmotionLoadState({
  loading = false,
  refreshing = false,
  runtimeLoading = false,
  error = null,
  runtimeError = null,
  observabilityData = false,
  hasSamples = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing || runtimeLoading,
    error: error || runtimeError,
    hasData: observabilityData,
    hasSamples,
    available: observabilityData,
  })
}

export function getEmotionUpdatedAt(lastUpdatedAt, runtimeUpdatedAt) {
  return Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
}

export function buildEmotionDashboardViewModel({
  runtimeSnapshot,
  vtsData,
  memoryData,
  loading = false,
  refreshing = false,
  runtimeLoading = false,
  error = null,
  runtimeError = null,
  lastUpdatedAt = null,
  runtimeUpdatedAt = null,
  resourceIssues = [],
  resourceStates = {},
} = {}) {
  const { emotionContext, selfModel, bondState } = getEmotionRuntimeContext(runtimeSnapshot)
  const endocrineState = getEmotionEndocrineState(runtimeSnapshot, emotionContext)
  const topEmotions = getEmotionTopEmotions(vtsData)
  const emotionState = buildEmotionState({ emotionContext, selfModel, bondState })
  const runtimeEmotionAvailable = Boolean(runtimeSnapshot && [
    emotionState.valence,
    emotionState.arousal,
    emotionState.dominance,
    emotionState.mood,
    emotionState.emotionTag,
    endocrineState?.dopamine_level,
    endocrineState?.serotonin_level,
    endocrineState?.cortisol_level,
    endocrineState?.gate_state,
  ].some((value) => value != null && value !== ''))
  const moodStyle = emotionState.mood
    ? EMOTION_MOOD_STYLES[emotionState.mood] || EMOTION_UNAVAILABLE_MOOD_STYLE
    : EMOTION_UNAVAILABLE_MOOD_STYLE
  const emotionTagLabel = emotionState.emotionTag ? formatEmotionTag(emotionState.emotionTag) : '待判定'
  const bondStageLabel = emotionState.bondStage ? formatStage(emotionState.bondStage) : '待判定'
  const gateView = buildEmotionGateView(endocrineState)
  const bondMetrics = getEmotionBondMetrics(bondState)
  const sampleState = getEmotionSampleState({ runtimeSnapshot, vtsData, memoryData, topEmotions })
  const resourceLoadError = !sampleState.observabilityData
    ? resourceIssues.find((issue) => issue?.availability === 'error') || null
    : null
  const loadState = buildEmotionLoadState({
    loading,
    refreshing,
    runtimeLoading,
    error: error || resourceLoadError,
    runtimeError,
    observabilityData: sampleState.observabilityData,
    hasSamples: sampleState.hasSamples,
  })
  const updatedAt = getEmotionUpdatedAt(lastUpdatedAt, runtimeUpdatedAt)

  return {
    endocrineState,
    runtimeEmotionAvailable,
    emotionState,
    moodStyle,
    emotionTagLabel,
    bondStageLabel,
    gateView,
    bondMetrics,
    topEmotions,
    sampleCount: sampleState.sampleCount,
    hasSamples: sampleState.hasSamples,
    loadState,
    updatedAt,
    heroView: buildEmotionHeroView({
      moodStyle,
      emotionTagLabel,
      bondStageLabel,
      bondTemperatureLabel: bondMetrics.bondTemperatureLabel,
      vtsEventCount: vtsData?.total_events,
    }),
    noticeView: buildEmotionNoticeView({
      error,
      runtimeError,
      loading,
      refreshing,
      runtimeLoading,
      loadState,
      resourceIssues,
    }),
    limbicOrganModel: buildEmotionLimbicOrganModel({
      loadState,
      updatedAt,
      moodStyle,
      emotionState,
      emotionTagLabel,
      bondStageLabel,
      bondMetrics,
      gateView,
      vtsData,
      sampleCount: sampleState.sampleCount,
      hasSamples: sampleState.hasSamples,
      endocrineState,
    }),
    metricCards: buildEmotionMetricCards({
      emotionTagLabel,
      moodStyle,
      bondStageLabel,
      bondEventCount: bondMetrics.bondEventCount,
      gateLabel: gateView.gateLabel,
      gateTone: gateView.gateTone,
      endocrineState,
      vtsData,
    }),
    gauges: [
      buildEmotionGaugeView({ label: '效价', value: emotionState.valence, color: 'text-green-600' }),
      buildEmotionGaugeView({ label: '唤醒', value: emotionState.arousal, color: 'text-blue-600' }),
      buildEmotionGaugeView({ label: '稳定度', value: emotionState.dominance, color: 'text-purple-600' }),
    ],
    hormoneBars: [
      buildEmotionHormoneBarView({ label: 'Dopamine', value: endocrineState?.dopamine_level, color: '#c026d3' }),
      buildEmotionHormoneBarView({ label: 'Serotonin', value: endocrineState?.serotonin_level, color: '#059669' }),
      buildEmotionHormoneBarView({ label: 'Cortisol', value: endocrineState?.cortisol_level, color: '#d97706' }),
    ],
    topEmotionRows: buildEmotionTopEmotionRows(topEmotions),
    vtsStatCards: buildEmotionVtsStatCards(vtsData),
    vtsObservation: buildEmotionVtsObservation(vtsData, resourceStates.vtsData),
    memorySubsystemRows: buildEmotionMemorySubsystemRows(memoryData),
  }
}
