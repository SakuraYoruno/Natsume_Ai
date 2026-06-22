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
  buildEmotionVtsStatCards,
} from './emotionDashboardViews.js'

export {
  buildEmotionHeroView,
  buildEmotionLimbicOrganModel,
  buildEmotionMemorySubsystemRows,
  buildEmotionMetricCards,
  buildEmotionNoticeView,
  buildEmotionTopEmotionRows,
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
export function clampEmotionUnitValue(value) {
  return Math.max(0, Math.min(1, Number(value || 0)))
}

export function buildEmotionHormoneBarView({ label, value, color }) {
  const numeric = clampEmotionUnitValue(value)
  return {
    label,
    color,
    valueLabel: numeric.toFixed(2),
    width: `${(numeric * 100).toFixed(0)}%`,
  }
}
export function buildEmotionGaugeView({ label, value, color }) {
  const numeric = Number(value || 0)
  const percent = ((numeric + 1) / 2) * 100

  return {
    label,
    value: numeric,
    color,
    valueLabel: numeric.toFixed(2),
    width: `${percent.toFixed(0)}%`,
    backgroundColor: EMOTION_GAUGE_COLOR_MAP[color] || '#a855f7',
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
  const eventCount = Number(bondState.event_count || 0)
  const temperature = clampEmotionUnitValue(eventCount / 500)
  return {
    bondEventCount: eventCount,
    bondTemperature: temperature,
    bondTemperatureLabel: `${(temperature * 100).toFixed(0)}%`,
    bondTemperatureDegree: `${(temperature * 100).toFixed(0)}°`,
    bondTemperatureFormatted: formatNumber(temperature, 2),
    bondTemperatureArc: `${temperature * 314} 314`,
  }
}

export function buildEmotionState({ emotionContext = {}, selfModel = {}, bondState = {} } = {}) {
  const emotionIntensity = Number(
    emotionContext.emotion_intensity ?? emotionContext?.emotion_timeline_summary?.avg_intensity ?? 0,
  )

  return {
    valence: Number(
      emotionContext?.user_emotion_profile?.avg_valence
        ?? emotionContext?.emotion_timeline_summary?.avg_valence
        ?? 0,
    ),
    arousal: Number(emotionContext?.user_emotion_profile?.avg_arousal ?? emotionIntensity ?? 0),
    dominance: Number(selfModel?.emotional_stability ?? 0),
    mood: String(emotionContext.mood_state || 'steady').toLowerCase(),
    emotionTag: String(emotionContext.emotion_tag || 'NEUTRAL').toUpperCase(),
    bondStage: String(bondState.current_stage || 'STRANGER').toUpperCase(),
  }
}

export function buildEmotionGateView(endocrineState = {}) {
  const gateState = String(endocrineState?.gate_state || 'open').toLowerCase()
  return {
    gateState,
    gateLabel: ENDOCRINE_GATE_LABELS[gateState] || gateState,
    gateTone: gateState === 'protective'
      ? 'text-red-600'
      : gateState === 'cautious'
        ? 'text-orange-500'
        : 'text-green-600',
  }
}

export function getEmotionSampleState({ runtimeSnapshot, vtsData, memoryData, topEmotions = [] } = {}) {
  const sampleCount = Number(vtsData?.total_events ?? topEmotions.length ?? 0)
  return {
    observabilityData: Boolean(runtimeSnapshot || vtsData || memoryData),
    sampleCount,
    hasSamples: Boolean(runtimeSnapshot?.emotion_context || sampleCount > 0),
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
} = {}) {
  const { emotionContext, selfModel, bondState } = getEmotionRuntimeContext(runtimeSnapshot)
  const endocrineState = getEmotionEndocrineState(runtimeSnapshot, emotionContext)
  const topEmotions = getEmotionTopEmotions(vtsData)
  const emotionState = buildEmotionState({ emotionContext, selfModel, bondState })
  const moodStyle = EMOTION_MOOD_STYLES[emotionState.mood] || EMOTION_MOOD_STYLES.steady
  const emotionTagLabel = formatEmotionTag(emotionState.emotionTag)
  const bondStageLabel = formatStage(emotionState.bondStage)
  const gateView = buildEmotionGateView(endocrineState)
  const bondMetrics = getEmotionBondMetrics(bondState)
  const sampleState = getEmotionSampleState({ runtimeSnapshot, vtsData, memoryData, topEmotions })
  const loadState = buildEmotionLoadState({
    loading,
    refreshing,
    runtimeLoading,
    error,
    runtimeError,
    observabilityData: sampleState.observabilityData,
    hasSamples: sampleState.hasSamples,
  })
  const updatedAt = getEmotionUpdatedAt(lastUpdatedAt, runtimeUpdatedAt)

  return {
    endocrineState,
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
      topEmotionCount: topEmotions.length,
    }),
    noticeView: buildEmotionNoticeView({ error, runtimeError, loading, refreshing, runtimeLoading, loadState }),
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
    memorySubsystemRows: buildEmotionMemorySubsystemRows(memoryData),
  }
}
