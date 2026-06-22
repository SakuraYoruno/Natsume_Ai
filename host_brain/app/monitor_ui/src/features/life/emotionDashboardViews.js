import { LOAD_STATES } from '../../models/loadState.js'
import { buildLimbicOrganSystemModel } from '../../models/organSystemModel.js'
import { EMOTION_TAG_COLORS } from '../../shared/statusMeta.js'
import { formatEmotionTag } from '../../utils/digitalLife.js'
import { getSuccessRateTone } from '../../utils/successRate.js'

export function buildEmotionHeroView({
  moodStyle,
  emotionTagLabel,
  bondStageLabel,
  bondTemperatureLabel,
  topEmotionCount,
} = {}) {
  return {
    title: moodStyle?.label,
    description: `主情绪 ${emotionTagLabel} · 关系阶段 ${bondStageLabel} · 数据来自运行快照与 VTS 聚合。`,
    metaItems: [
      `主情绪：${emotionTagLabel}`,
      `关系温度：${bondTemperatureLabel}`,
      `表情样本：${topEmotionCount}`,
    ],
  }
}

export function buildEmotionNoticeView({ error, runtimeError, loading, refreshing, runtimeLoading, loadState }) {
  const idle = !error && !loading && !refreshing && !runtimeLoading
  return {
    error,
    errorMessage: error?.message || error,
    runtimeWarning: !error ? runtimeError : null,
    unavailable: idle && loadState === LOAD_STATES.UNAVAILABLE,
    noSample: idle && loadState === LOAD_STATES.NO_SAMPLE,
  }
}

export function buildEmotionMetricCards({
  emotionTagLabel,
  moodStyle,
  bondStageLabel,
  bondEventCount,
  gateLabel,
  gateTone,
  endocrineState = {},
  vtsData = {},
} = {}) {
  return [
    { label: '当前情绪', value: emotionTagLabel, hint: moodStyle?.label, tone: 'text-pink-600' },
    { label: '关系阶段', value: bondStageLabel, hint: `事件 ${bondEventCount} 次`, tone: 'text-purple-600' },
    {
      label: '内分泌门控',
      value: gateLabel,
      hint: `cortisol ${Number(endocrineState?.cortisol_level || 0).toFixed(2)}`,
      tone: gateTone,
    },
    {
      label: '对话放行',
      value: vtsData?.talk_gate_allowed ?? 0,
      hint: `表情降级率 ${vtsData?.degrade_rate ?? 0}%`,
      tone: 'text-blue-600',
    },
  ]
}

export function buildEmotionTopEmotionRows(topEmotions = []) {
  return topEmotions.map((emotion) => ({
    key: emotion?.emotion_tag,
    label: formatEmotionTag(emotion?.emotion_tag),
    pct: emotion?.pct,
    count: emotion?.count,
    color: EMOTION_TAG_COLORS[emotion?.emotion_tag] || '#9ca3af',
  }))
}

export function buildEmotionVtsStatCards(vtsData) {
  if (!vtsData) return []
  return [
    { label: '表情应用率', value: `${vtsData.apply_rate ?? 0}%`, className: 'text-green-600' },
    {
      label: '降级率',
      value: `${vtsData.degrade_rate ?? 0}%`,
      className: vtsData.degrade_rate > 10 ? 'text-orange-500' : 'text-gray-400',
    },
    { label: '放行次数', value: vtsData.talk_gate_allowed ?? 0, className: 'text-blue-600' },
  ]
}

export function buildEmotionMemorySubsystemRows(memoryData) {
  const rows = Array.isArray(memoryData?.subsystems) ? memoryData.subsystems : []
  return rows.map((subsystem) => ({
    key: subsystem?.subsystem,
    label: subsystem?.display_name,
    successRate: subsystem?.success_rate ?? '—',
    tone: getSuccessRateTone(subsystem?.success_rate),
  }))
}

export function buildEmotionLimbicOrganModel({
  loadState,
  updatedAt,
  moodStyle,
  emotionState,
  emotionTagLabel,
  bondStageLabel,
  bondMetrics,
  gateView,
  vtsData,
  sampleCount,
  hasSamples,
  endocrineState,
} = {}) {
  return buildLimbicOrganSystemModel({
    loadState,
    updatedAt,
    moodLabel: moodStyle?.label,
    moodKey: emotionState?.mood,
    emotionTagLabel,
    bondStageLabel,
    relationshipTemperature: bondMetrics?.bondTemperature,
    bondEventCount: bondMetrics?.bondEventCount,
    gateLabel: gateView?.gateLabel,
    gateState: gateView?.gateState,
    degradeRate: vtsData?.degrade_rate,
    applyRate: vtsData?.apply_rate,
    talkGateAllowed: vtsData?.talk_gate_allowed,
    sampleCount,
    cortisolLevel: endocrineState?.cortisol_level,
    emotionalStability: hasSamples ? (100 - Number(vtsData?.degrade_rate || 0)) : null,
  })
}
