import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Heart,
  Frown,
  Meh,
  RefreshCw,
  Smile,
  Zap,
} from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { buildLimbicOrganSystemModel } from '../models/organSystemModel.js'
import { useEmotionOverviewQuery } from '../queries/runtimeQueries.js'
import useAppStore from '../store/useAppStore.js'
import { formatEmotionTag, formatNumber, formatStage } from '../utils/digitalLife.js'
import { getSuccessRateTone } from '../utils/successRate.js'

const MOOD_STYLES = {
  calm: { icon: Meh, color: 'text-blue-500', bg: 'bg-blue-50', label: '平静' },
  steady: { icon: Meh, color: 'text-gray-500', bg: 'bg-gray-50', label: '稳定' },
  engaged: { icon: Smile, color: 'text-green-600', bg: 'bg-green-50', label: '活跃' },
  tired: { icon: Frown, color: 'text-orange-500', bg: 'bg-orange-50', label: '疲惫' },
  critical: { icon: Frown, color: 'text-red-600', bg: 'bg-red-50', label: '临界' },
}

const EMOTION_COLORS = {
  HAPPY: '#22c55e',
  EXCITED: '#eab308',
  SAD: '#3b82f6',
  SADNESS: '#3b82f6',
  ANGRY: '#ef4444',
  ANGER: '#ef4444',
  NEUTRAL: '#9ca3af',
  SURPRISED: '#a855f7',
  SURPRISE: '#a855f7',
  CONFUSED: '#f97316',
  CALM: '#06b6d4',
  LOVE: '#ec4899',
  CURIOUS: '#8b5cf6',
  SHY: '#f472b6',
  BORED: '#6b7280',
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const GATE_LABELS = {
  open: '开放',
  cautious: '谨慎',
  protective: '保护',
}

function HormoneBar({ label, value, color }) {
  const numeric = Math.max(0, Math.min(1, Number(value || 0)))
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{label}</span>
        <span className="font-mono font-semibold" style={{ color }}>{numeric.toFixed(2)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
        <div className="h-full rounded-full" style={{ width: `${(numeric * 100).toFixed(0)}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

function Gauge({ label, value, color }) {
  const numeric = Number(value || 0)
  const percent = ((numeric + 1) / 2) * 100
  const colorMap = {
    'text-green-600': '#22c55e',
    'text-blue-600': '#3b82f6',
    'text-purple-600': '#a855f7',
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{label}</span>
        <span className={`font-mono font-bold ${color}`}>{numeric.toFixed(2)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-gray-200">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${percent.toFixed(0)}%`,
            backgroundColor: colorMap[color] || '#a855f7',
          }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-gray-400">
        <span>-1.0</span>
        <span>0</span>
        <span>+1.0</span>
      </div>
    </div>
  )
}

export default function EmotionDashboard() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const {
    vtsData,
    memoryData,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
  } = useEmotionOverviewQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    runtimeSnapshot,
    loading: runtimeLoading,
    error: runtimeError,
    lastUpdatedAt: runtimeUpdatedAt,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const emotionContext = runtimeSnapshot?.emotion_context || {}
  const selfModel = runtimeSnapshot?.self_model_snapshot || {}
  const bondState = runtimeSnapshot?.bond_state || {}
  const topEmotions = vtsData?.top_emotions || []
  const endocrineState = runtimeSnapshot?.endocrine_state || emotionContext?.endocrine_state || {}
  const emotionIntensity = Number(
    emotionContext.emotion_intensity ?? emotionContext?.emotion_timeline_summary?.avg_intensity ?? 0
  )
  const bondEventCount = Number(bondState.event_count || 0)
  const bondTemperature = Math.max(0, Math.min(1, bondEventCount / 500))

  const emotionState = {
    valence: Number(
      emotionContext?.user_emotion_profile?.avg_valence
        ?? emotionContext?.emotion_timeline_summary?.avg_valence
        ?? 0
    ),
    arousal: Number(emotionContext?.user_emotion_profile?.avg_arousal ?? emotionIntensity ?? 0),
    dominance: Number(selfModel?.emotional_stability ?? 0),
    mood: String(emotionContext.mood_state || 'steady').toLowerCase(),
    emotionTag: String(emotionContext.emotion_tag || 'NEUTRAL').toUpperCase(),
    bondStage: String(bondState.current_stage || 'STRANGER').toUpperCase(),
  }

  const moodStyle = MOOD_STYLES[emotionState.mood] || MOOD_STYLES.steady
  const MoodIcon = moodStyle.icon
  const emotionTagLabel = formatEmotionTag(emotionState.emotionTag)
  const bondStageLabel = formatStage(emotionState.bondStage)
  const gateState = String(endocrineState?.gate_state || 'open').toLowerCase()
  const gateLabel = GATE_LABELS[gateState] || gateState
  const gateTone = gateState === 'protective' ? 'text-red-600' : gateState === 'cautious' ? 'text-orange-500' : 'text-green-600'
  const emotionObservabilityData = Boolean(runtimeSnapshot || vtsData || memoryData)
  const emotionSampleCount = Number(vtsData?.total_events ?? topEmotions.length ?? 0)
  const emotionHasSamples = Boolean(runtimeSnapshot?.emotion_context || emotionSampleCount > 0)
  const emotionLoadState = resolveLoadState({
    loading: loading || refreshing || runtimeLoading,
    error: error || runtimeError,
    hasData: emotionObservabilityData,
    hasSamples: emotionHasSamples,
    available: emotionObservabilityData,
  })
  const emotionUpdatedAt = Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
  const limbicOrganModel = useMemo(() => buildLimbicOrganSystemModel({
    loadState: emotionLoadState,
    updatedAt: emotionUpdatedAt,
    moodLabel: moodStyle.label,
    moodKey: emotionState.mood,
    emotionTagLabel,
    bondStageLabel,
    relationshipTemperature: bondTemperature,
    bondEventCount,
    gateLabel,
    gateState,
    degradeRate: vtsData?.degrade_rate,
    applyRate: vtsData?.apply_rate,
    talkGateAllowed: vtsData?.talk_gate_allowed,
    sampleCount: emotionSampleCount,
    cortisolLevel: endocrineState?.cortisol_level,
    emotionalStability: emotionHasSamples ? (100 - Number(vtsData?.degrade_rate || 0)) : null,
  }), [
    bondEventCount,
    bondStageLabel,
    bondTemperature,
    emotionHasSamples,
    emotionLoadState,
    emotionSampleCount,
    emotionState.mood,
    emotionTagLabel,
    emotionUpdatedAt,
    endocrineState?.cortisol_level,
    gateLabel,
    gateState,
    moodStyle.label,
    vtsData?.apply_rate,
    vtsData?.degrade_rate,
    vtsData?.talk_gate_allowed,
  ])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">边缘系统</h1>
          <p className="text-sm text-gray-500">统一查看情绪三维、表情分布与关系温度。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Heart}
        eyebrow="边缘系统快照"
        title={moodStyle.label}
        description={`主情绪 ${emotionTagLabel} · 关系阶段 ${formatStage(emotionState.bondStage)} · 数据来自运行快照与 VTS 聚合。`}
        gradientClass="from-primary-50 via-white to-rose-50"
        metaItems={[
          `主情绪：${emotionTagLabel}`,
          `关系温度：${(bondTemperature * 100).toFixed(0)}%`,
          `表情样本：${topEmotions.length}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={emotionLoadState}
            windowMinutes={windowMinutes}
            sampleSize={emotionSampleCount}
            sampleLabel="VTS 事件"
            updatedAt={emotionUpdatedAt}
            source="runtime snapshot + /obs/vts/stats"
            definition="情绪页优先融合运行快照与 VTS 聚合；无样本时仍可保留运行态上下文，但不会把空窗误报成失败。"
          />
        )}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}
      {!error && runtimeError ? <NoticeBanner variant="warning">{runtimeError}</NoticeBanner> : null}
      {!error && !loading && !refreshing && !runtimeLoading && emotionLoadState === LOAD_STATES.UNAVAILABLE ? (
        <NoticeBanner variant="warning">
          情绪观测数据当前暂不可用，页面会保留中性空态并等待运行快照或 VTS 聚合恢复。
        </NoticeBanner>
      ) : null}
      {!error && !loading && !refreshing && !runtimeLoading && emotionLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">
          当前窗口还没有足够的表情样本，页面主要展示运行快照中的情绪上下文与关系温度。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={limbicOrganModel} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前情绪" value={emotionTagLabel} hint={moodStyle.label} tone="text-pink-600" />
        <MetricCard label="关系阶段" value={bondStageLabel} hint={`事件 ${bondEventCount} 次`} tone="text-purple-600" />
        <MetricCard label="内分泌门控" value={gateLabel} hint={`cortisol ${(Number(endocrineState?.cortisol_level || 0)).toFixed(2)}`} tone={gateTone} />
        <MetricCard label="对话放行" value={vtsData?.talk_gate_allowed ?? 0} hint={`表情降级率 ${vtsData?.degrade_rate ?? 0}%`} tone="text-blue-600" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card space-y-4 p-5">
            <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
              <Heart size={14} className="text-pink-500" />
            当前状态
            </h2>
          <div className="py-4 text-center">
            <div className={`inline-flex items-center gap-2 rounded-full px-4 py-2 ${moodStyle.bg}`}>
              <MoodIcon size={20} className={moodStyle.color} />
              <span className={`font-bold ${moodStyle.color}`}>{moodStyle.label}</span>
            </div>
            <div className="mt-2 text-xs text-gray-500">
              主情绪：<span className="text-gray-700">{emotionTagLabel}</span>
            </div>
            <div className="mt-2">
              <NoticeBanner variant="info" className="text-left">
                这里已经直接读取运行时快照，不再使用前端模拟情绪数据。
              </NoticeBanner>
            </div>
          </div>
          <div className="space-y-3">
            <Gauge label="效价" value={emotionState.valence} color="text-green-600" />
            <Gauge label="唤醒" value={emotionState.arousal} color="text-blue-600" />
            <Gauge label="稳定度" value={emotionState.dominance} color="text-purple-600" />
          </div>
          <div className="space-y-2 border-t pt-3">
            <div className="text-xs font-medium text-gray-600">边缘系统基调（Endocrine）</div>
            <HormoneBar label="Dopamine" value={endocrineState?.dopamine_level} color="#c026d3" />
            <HormoneBar label="Serotonin" value={endocrineState?.serotonin_level} color="#059669" />
            <HormoneBar label="Cortisol" value={endocrineState?.cortisol_level} color="#d97706" />
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-500">Gate</span>
              <span className={`font-semibold ${gateTone}`}>{gateLabel}</span>
            </div>
          </div>
        </div>

        <div className="card space-y-4 p-5">
          <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
            <Activity size={14} className="text-indigo-500" />
            情绪标签分布
          </h2>
          {topEmotions.length > 0 ? (
            <div className="space-y-2">
              {topEmotions.map((emotion) => (
                <div key={emotion.emotion_tag} className="flex items-center gap-2">
                  <span className="w-20 shrink-0 truncate text-xs text-gray-600">{formatEmotionTag(emotion.emotion_tag)}</span>
                  <div className="h-4 flex-1 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${emotion.pct}%`,
                        backgroundColor: EMOTION_COLORS[emotion.emotion_tag] || '#9ca3af',
                      }}
                    />
                  </div>
                  <span className="w-12 text-right text-xs text-gray-500">{emotion.pct}%</span>
                  <span className="w-8 text-right text-xs text-gray-400">{emotion.count}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无情绪样本" hint="等待情绪样本进入后再展示分布。" className="p-6" />
          )}

          {vtsData ? (
            <div className="grid grid-cols-3 gap-2 border-t pt-2 text-center text-xs">
              <div>
                <div className="text-lg font-bold text-green-600">{vtsData.apply_rate ?? 0}%</div>
                <div className="text-gray-500">表情应用率</div>
              </div>
              <div>
                <div className={`text-lg font-bold ${vtsData.degrade_rate > 10 ? 'text-orange-500' : 'text-gray-400'}`}>
                  {vtsData.degrade_rate ?? 0}%
                </div>
                <div className="text-gray-500">降级率</div>
              </div>
              <div>
                <div className="text-lg font-bold text-blue-600">{vtsData.talk_gate_allowed ?? 0}</div>
                <div className="text-gray-500">放行次数</div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="card space-y-4 p-5">
          <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
            <Zap size={14} className="text-pink-500" />
            关系温度
          </h2>
          <div className="py-4 text-center">
            <div className="relative mx-auto h-32 w-32">
              <svg viewBox="0 0 120 120" className="h-full w-full">
                <circle cx="60" cy="60" r="50" fill="none" stroke="#e5e7eb" strokeWidth="8" />
                <circle
                  cx="60"
                  cy="60"
                  r="50"
                  fill="none"
                  stroke="#ec4899"
                  strokeDasharray={`${bondTemperature * 314} 314`}
                  strokeLinecap="round"
                  transform="rotate(-90 60 60)"
                  className="transition-all duration-1000"
                />
                <text x="60" y="55" textAnchor="middle" className="fill-gray-800 text-lg font-bold">
                  {(bondTemperature * 100).toFixed(0)}°
                </text>
                <text x="60" y="72" textAnchor="middle" className="fill-gray-500 text-[10px]">
                  {bondStageLabel}
                </text>
              </svg>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="card p-2 text-center">
              <div className="font-bold text-pink-600">{bondStageLabel}</div>
              <div className="text-gray-500">当前阶段</div>
            </div>
            <div className="card p-2 text-center">
              <div className="font-bold text-gray-700">{formatNumber(bondTemperature, 2)}</div>
              <div className="text-gray-500">温度值</div>
            </div>
          </div>

          {memoryData?.subsystems ? (
            <div className="space-y-1 border-t pt-2">
              <div className="mb-1 text-xs font-medium text-gray-600">记忆子系统成功率</div>
              {memoryData.subsystems.map((subsystem) => (
                <div key={subsystem.subsystem} className="flex items-center justify-between text-xs">
                  <span className="text-gray-600">{subsystem.display_name}</span>
                  <span
                    className={getSuccessRateTone(subsystem.success_rate)}
                  >
                    {subsystem.success_rate ?? '—'}%
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Link to="/response-policy" className="btn-secondary text-sm">查看回复策略</Link>
        <Link to="/self-model" className="btn-secondary text-sm">查看自我模型</Link>
      </div>
    </div>
  )
}
