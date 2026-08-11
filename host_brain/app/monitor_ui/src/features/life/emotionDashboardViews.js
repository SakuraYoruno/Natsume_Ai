import { LOAD_STATES } from '../../models/loadState.js'
import { buildLimbicOrganSystemModel } from '../../models/organSystemModel.js'
import { EMOTION_TAG_COLORS } from '../../shared/statusMeta.js'
import { formatEmotionTag } from '../../utils/digitalLife.js'
import { getSuccessRateTone } from '../../utils/successRate.js'
import {
  formatObservableCount,
  formatObservablePercent,
} from './observabilityResourceState.js'

function formatVtsRate(value, totalEvents) {
  if (value == null && Number(totalEvents || 0) === 0) return '无样本'
  return formatObservablePercent(value)
}

export function buildEmotionHeroView({
  moodStyle,
  emotionTagLabel,
  bondStageLabel,
  bondTemperatureLabel,
  vtsEventCount,
} = {}) {
  return {
    title: moodStyle?.label,
    description: `主情绪 ${emotionTagLabel} · 关系阶段 ${bondStageLabel} · 数据来自运行快照与 VTS 聚合。`,
    metaItems: [
      `主情绪：${emotionTagLabel}`,
      `关系温度：${bondTemperatureLabel}`,
      `表情事件：${formatObservableCount(vtsEventCount)}`,
    ],
  }
}

export function buildEmotionNoticeView({
  error,
  runtimeError,
  loading,
  refreshing,
  runtimeLoading,
  loadState,
  resourceIssues = [],
}) {
  const idle = !error && !loading && !refreshing && !runtimeLoading
  return {
    error,
    errorMessage: error?.message || error,
    runtimeWarning: !error && runtimeError
      ? runtimeError?.message || String(runtimeError)
      : null,
    unavailable: idle && loadState === LOAD_STATES.UNAVAILABLE,
    noSample: idle && loadState === LOAD_STATES.NO_SAMPLE,
    resourceIssues,
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
  vtsData = null,
} = {}) {
  return [
    { label: '当前情绪', value: emotionTagLabel, hint: moodStyle?.label, tone: 'text-pink-600' },
    { label: '关系阶段', value: bondStageLabel, hint: `事件 ${formatObservableCount(bondEventCount)} 次`, tone: 'text-purple-600' },
    {
      label: '内分泌门控',
      value: gateLabel,
      hint: endocrineState?.cortisol_level == null
        ? 'cortisol 不可测'
        : `cortisol ${Number(endocrineState.cortisol_level).toFixed(2)}`,
      tone: gateTone,
    },
    {
      label: '对话放行',
      value: vtsData
        ? `${formatObservableCount(vtsData.talk_gate_allowed)}/${formatObservableCount(vtsData.talk_gate_total)}`
        : '不可用',
      hint: vtsData
        ? `表情降级率 ${formatVtsRate(vtsData.degrade_rate, vtsData.total_events)}`
        : 'VTS 数据源不可用',
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
    {
      label: '表情应用率',
      value: formatVtsRate(vtsData.apply_rate, vtsData.total_events),
      className: 'text-green-600',
    },
    {
      label: '降级率',
      value: formatVtsRate(vtsData.degrade_rate, vtsData.total_events),
      className: vtsData.degrade_rate != null && vtsData.degrade_rate > 10
        ? 'text-orange-500'
        : 'text-gray-500',
    },
    {
      label: 'Talk Gate 放行',
      value: formatObservableCount(vtsData.talk_gate_allowed),
      className: 'text-blue-600',
    },
    {
      label: 'Talk Gate 阻断',
      value: formatObservableCount(vtsData.talk_gate_blocked),
      className: 'text-orange-500',
    },
  ]
}

export function buildEmotionVtsObservation(vtsData, resourceState) {
  if (!vtsData && !resourceState) {
    return {
      source: 'avatar_action_log',
      availability: '未加载',
      reasonCode: null,
    }
  }
  const available = resourceState?.available ?? Boolean(vtsData)
  return {
    source: resourceState?.source || 'avatar_action_log',
    availability: available
      ? Number(vtsData?.total_events || 0) > 0 || Number(vtsData?.talk_gate_total || 0) > 0
        ? '可观测'
        : '可用，当前窗口无样本'
      : resourceState?.availability === 'error'
        ? '请求失败'
        : '数据源暂不可用',
    reasonCode: resourceState?.reasonCode || null,
  }
}

export function buildEmotionMemorySubsystemRows(memoryData) {
  const rows = Array.isArray(memoryData?.subsystems) ? memoryData.subsystems : []
  return rows.map((subsystem) => ({
    key: subsystem?.subsystem,
    label: subsystem?.display_name,
    successRate: subsystem?.success_rate == null ? '不可测' : `${subsystem.success_rate}%`,
    source: subsystem?.extra?.source_table || '来源未标注',
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
    emotionalStability: hasSamples && vtsData?.degrade_rate != null
      ? (100 - Number(vtsData.degrade_rate))
      : null,
  })
}
