import React, { useState } from 'react'
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
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  EMOTION_WINDOW_OPTIONS,
  buildEmotionDashboardViewModel,
} from '../features/life/emotionDashboardModel.js'
import { useEmotionOverviewQuery } from '../features/life/emotionQueries.js'
import useAppStore from '../store/useAppStore.js'

const MOOD_ICONS = {
  calm: Meh,
  steady: Meh,
  engaged: Smile,
  tired: Frown,
  critical: Frown,
}

function HormoneBar({ view }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{view.label}</span>
        <span className="font-mono font-semibold" style={{ color: view.color }}>{view.valueLabel}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
        <div className="h-full rounded-full" style={{ width: view.width, backgroundColor: view.color }} />
      </div>
    </div>
  )
}

function Gauge({ view }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{view.label}</span>
        <span className={`font-mono font-bold ${view.color}`}>{view.valueLabel}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-gray-200">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: view.width,
            backgroundColor: view.backgroundColor,
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

  const {
    emotionState,
    moodStyle,
    emotionTagLabel,
    bondStageLabel,
    gateView,
    bondMetrics,
    sampleCount,
    loadState,
    updatedAt,
    heroView,
    noticeView,
    limbicOrganModel,
    metricCards,
    gauges,
    hormoneBars,
    topEmotionRows,
    vtsStatCards,
    memorySubsystemRows,
  } = buildEmotionDashboardViewModel({
    runtimeSnapshot,
    vtsData,
    memoryData,
    loading,
    refreshing,
    runtimeLoading,
    error,
    runtimeError,
    lastUpdatedAt,
    runtimeUpdatedAt,
  })
  const MoodIcon = MOOD_ICONS[emotionState.mood] || MOOD_ICONS.steady

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">边缘系统</h1>
          <p className="text-sm text-gray-500">统一查看情绪三维、表情分布与关系温度。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={EMOTION_WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Heart}
        eyebrow="边缘系统快照"
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-primary-50 via-white to-rose-50"
        metaItems={heroView.metaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={loadState}
            windowMinutes={windowMinutes}
            sampleSize={sampleCount}
            sampleLabel="VTS 事件"
            updatedAt={updatedAt}
            source="runtime snapshot + /obs/vts/stats"
            definition="情绪页优先融合运行快照与 VTS 聚合；无样本时仍可保留运行态上下文，但不会把空窗误报成失败。"
          />
        )}
      />

      {noticeView.error ? <NoticeBanner variant="error">{noticeView.errorMessage}</NoticeBanner> : null}
      {noticeView.runtimeWarning ? <NoticeBanner variant="warning">{noticeView.runtimeWarning}</NoticeBanner> : null}
      {noticeView.unavailable ? (
        <NoticeBanner variant="warning">
          情绪观测数据当前暂不可用，页面会保留中性空态并等待运行快照或 VTS 聚合恢复。
        </NoticeBanner>
      ) : null}
      {noticeView.noSample ? (
        <NoticeBanner variant="info">
          当前窗口还没有足够的表情样本，页面主要展示运行快照中的情绪上下文与关系温度。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={limbicOrganModel} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} hint={card.hint} tone={card.tone} />
        ))}
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
            {gauges.map((gauge) => (
              <Gauge key={gauge.label} view={gauge} />
            ))}
          </div>
          <div className="space-y-2 border-t pt-3">
            <div className="text-xs font-medium text-gray-600">边缘系统基调（Endocrine）</div>
            {hormoneBars.map((bar) => (
              <HormoneBar key={bar.label} view={bar} />
            ))}
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-500">Gate</span>
              <span className={`font-semibold ${gateView.gateTone}`}>{gateView.gateLabel}</span>
            </div>
          </div>
        </div>

        <div className="card space-y-4 p-5">
          <h2 className="flex items-center gap-1 text-sm font-semibold text-gray-700">
            <Activity size={14} className="text-indigo-500" />
            情绪标签分布
          </h2>
          {topEmotionRows.length > 0 ? (
            <div className="space-y-2">
              {topEmotionRows.map((emotion) => (
                <div key={emotion.key} className="flex items-center gap-2">
                  <span className="w-20 shrink-0 truncate text-xs text-gray-600">{emotion.label}</span>
                  <div className="h-4 flex-1 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${emotion.pct}%`,
                        backgroundColor: emotion.color,
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

          {vtsStatCards.length > 0 ? (
            <div className="grid grid-cols-3 gap-2 border-t pt-2 text-center text-xs">
              {vtsStatCards.map((card) => (
                <div key={card.label}>
                  <div className={`text-lg font-bold ${card.className}`}>{card.value}</div>
                  <div className="text-gray-500">{card.label}</div>
                </div>
              ))}
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
                  strokeDasharray={bondMetrics.bondTemperatureArc}
                  strokeLinecap="round"
                  transform="rotate(-90 60 60)"
                  className="transition-all duration-1000"
                />
                <text x="60" y="55" textAnchor="middle" className="fill-gray-800 text-lg font-bold">
                  {bondMetrics.bondTemperatureDegree}
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
              <div className="font-bold text-gray-700">{bondMetrics.bondTemperatureFormatted}</div>
              <div className="text-gray-500">温度值</div>
            </div>
          </div>

          {memorySubsystemRows.length > 0 ? (
            <div className="space-y-1 border-t pt-2">
              <div className="mb-1 text-xs font-medium text-gray-600">记忆子系统成功率</div>
              {memorySubsystemRows.map((subsystem) => (
                <div key={subsystem.key} className="flex items-center justify-between text-xs">
                  <span className="text-gray-600">{subsystem.label}</span>
                  <span
                    className={subsystem.tone}
                  >
                    {subsystem.successRate}%
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Link to="/life/response-policy" className="btn-secondary text-sm">查看回复策略</Link>
        <Link to="/life/self-model" className="btn-secondary text-sm">查看自我模型</Link>
      </div>
    </div>
  )
}
