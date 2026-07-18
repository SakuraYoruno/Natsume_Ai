import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Heart,
  Frown,
  Meh,
  RefreshCw,
  Smile,
} from 'lucide-react'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  BondTemperaturePanel,
  CurrentEmotionPanel,
  EmotionDistributionPanel,
} from '../features/life/components/emotion/EmotionDashboardPanels.jsx'
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
    resourceIssues,
    resourceStates,
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
    runtimeEmotionAvailable,
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
    vtsObservation,
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
    resourceIssues,
    resourceStates,
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
      {noticeView.resourceIssues.map((issue) => (
        <NoticeBanner key={issue.key} variant={issue.availability === 'error' ? 'error' : 'warning'}>
          {issue.key === 'vtsData' ? 'VTS 观测' : '记忆观测'}：{issue.message}
          {issue.source ? ` · 来源 ${issue.source}` : ''}
        </NoticeBanner>
      ))}
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
        <CurrentEmotionPanel
          moodStyle={moodStyle}
          MoodIcon={MoodIcon}
          emotionTagLabel={emotionTagLabel}
          gauges={gauges}
          hormoneBars={hormoneBars}
          gateView={gateView}
          runtimeEmotionAvailable={runtimeEmotionAvailable}
        />
        <EmotionDistributionPanel
          topEmotionRows={topEmotionRows}
          vtsStatCards={vtsStatCards}
          vtsObservation={vtsObservation}
        />
        <BondTemperaturePanel
          bondMetrics={bondMetrics}
          bondStageLabel={bondStageLabel}
          memorySubsystemRows={memorySubsystemRows}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <Link to="/life/response-policy" className="btn-secondary text-sm">查看回复策略</Link>
        <Link to="/life/self-model" className="btn-secondary text-sm">查看自我模型</Link>
      </div>
    </div>
  )
}
