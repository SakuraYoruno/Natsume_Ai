import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { RefreshCw, ShieldCheck, AlertTriangle, Bot, Database, Tv, GitMerge, Activity, ArrowLeft } from 'lucide-react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import LoadingNotice from '../shared/ui/LoadingNotice.jsx'
import LivingPulseStrip from '../shared/ui/LivingPulseStrip.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  INSIGHTS_WINDOW_OPTIONS,
  buildInsightsDashboardViewModel,
} from '../features/observability/insightsDashboardModel.js'
import { useInsightsSummaryQuery } from '../features/observability/insightsQueries.js'
import useAppStore from '../store/useAppStore.js'

const InsightsGlobalGroup = React.lazy(() => import('../features/observability/components/insights/InsightsGlobalGroup.jsx'))
const InsightsMemoryGroup = React.lazy(() => import('../features/observability/components/insights/InsightsMemoryGroup.jsx'))
const InsightsLlmGroup = React.lazy(() => import('../features/observability/components/insights/InsightsLlmGroup.jsx'))
const InsightsSafetyGroup = React.lazy(() => import('../features/observability/components/insights/InsightsSafetyGroup.jsx'))
const InsightsStateGroup = React.lazy(() => import('../features/observability/components/insights/InsightsStateGroup.jsx'))
const InsightsVtsGroup = React.lazy(() => import('../features/observability/components/insights/InsightsVtsGroup.jsx'))

const INSIGHT_GROUP_ICONS = {
  activity: Activity,
  alert: AlertTriangle,
  bot: Bot,
  database: Database,
  gitMerge: GitMerge,
  shield: ShieldCheck,
  tv: Tv,
}

function getInsightIcon(iconKey) {
  return INSIGHT_GROUP_ICONS[iconKey] || Activity
}

// ===== 主页面 =====
export default function Insights() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const navigate = useNavigate()
  const location = useLocation()

  const [windowMinutes, setWindowMinutes] = useState(60)
  const {
    govData,
    errData,
    subsysData,
    agentData,
    memoryData,
    vtsData,
    qaData,
    llmCallData,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useInsightsSummaryQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    runtimeSnapshot,
    promptStats: promptBuilderData,
    reload: reloadRuntime,
    lastUpdatedAt: runtimeUpdatedAt,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const {
    currentGroup,
    focusTarget,
    isRoot,
    insightGroups,
    headerStats,
    pulseNodes,
    heroView,
    sampleMeta,
  } = buildInsightsDashboardViewModel({
    pathname: location.pathname,
    search: location.search,
    windowMinutes,
    govData,
    errData,
    subsysData,
    agentData,
    memoryData,
    vtsData,
    qaData,
    llmCallData,
    loading,
    refreshing,
    error,
    stale,
    lastUpdatedAt,
    runtimeUpdatedAt,
  })

  useEffect(() => {
    if (currentGroup !== 'global' || focusTarget !== 'forge-risk') {
      return undefined
    }
    const timer = setTimeout(() => {
      document.getElementById('forge-runtime-risk')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 120)
    return () => clearTimeout(timer)
  }, [currentGroup, focusTarget])

  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([refetch(), reloadRuntime()])
  }, [refetch, reloadRuntime])
  const deepQueryProps = {
    pollingEnabled,
    pollingInterval,
    pollingTick,
  }

  return (
    <div className="space-y-8">
      <div className="card space-y-3 p-5 md:p-6">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">皮层洞察</h1>
            <p className="mt-0.5 text-sm text-gray-500">把神经链路、记忆链路、执行推理与生命体运行状态收敛到同一套洞察面。</p>
          </div>
          <div className="flex items-center gap-2">
            <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={INSIGHTS_WINDOW_OPTIONS} />
            <button className="btn-secondary" onClick={handleRefresh} disabled={loading || refreshing}>
              <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
              {loading || refreshing ? '刷新中' : '刷新'}
            </button>
          </div>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 md:grid-cols-4">
          {headerStats.map((item) => (
            <div key={item.label} className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">
              {item.label}：{item.value}
            </div>
          ))}
        </div>
      </div>

      <PageHero
        icon={getInsightIcon(heroView.iconKey)}
        eyebrow={heroView.eyebrow}
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-amber-50 via-white to-sky-50"
        metaItems={heroView.metaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            windowMinutes={sampleMeta.windowMinutes}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {isRoot ? (
        <LivingPulseStrip
          title="器官脉冲"
          subtitle="先看全局生命节律，再下钻到各个器官分区。"
          nodes={pulseNodes}
        />
      ) : null}

      {error && <NoticeBanner variant="error">加载失败：{error.message}</NoticeBanner>}
      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptBuilderData} compact={!isRoot} />
      {isRoot ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {insightGroups.map((group) => {
            const Icon = getInsightIcon(group.iconKey)
            return (
              <button
                type="button"
                key={group.id}
                className={`card-hover group w-full cursor-pointer p-0 text-left ${group.bg}`}
                onClick={() => navigate(`/observability/insights/${group.id}`)}
              >
                <div className="space-y-4 p-6">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className={`rounded-lg p-2.5 shadow-sm ${group.iconBg} ${group.color}`}>
                        <Icon size={24} strokeWidth={2} />
                      </div>
                      <h3 className="text-lg font-bold tracking-tight text-gray-800">{group.title}</h3>
                    </div>
                    <div className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${group.iconBg} ${group.color} bg-opacity-50`}>
                      <span>进入</span>
                      <ArrowLeft size={12} className="rotate-180 transition-transform group-hover:translate-x-0.5 group-hover:rotate-180" />
                    </div>
                  </div>
                  <p className="text-sm leading-relaxed text-gray-600">{group.desc}</p>
                  <div className="lifeline-rail h-1.5">
                    <div className="lifeline-wave h-full w-full" />
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="space-y-8">
          <div className="card p-4">
            <button className="btn-secondary" onClick={() => navigate('/observability/insights')}>
              <ArrowLeft size={16} /> 返回分组
            </button>
          </div>

          {currentGroup === 'global' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载全局异常洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsGlobalGroup
                windowMinutes={windowMinutes}
                govData={govData}
                errData={errData}
                focusTarget={focusTarget}
                {...deepQueryProps}
              />
            </React.Suspense>
          )}

          {currentGroup === 'memory' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载记忆器官洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsMemoryGroup
                windowMinutes={windowMinutes}
                subsysData={subsysData}
                memoryData={memoryData}
                {...deepQueryProps}
              />
            </React.Suspense>
          )}

          {currentGroup === 'llm' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载执行与推理洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsLlmGroup
                windowMinutes={windowMinutes}
                agentData={agentData}
                llmCallData={llmCallData}
                {...deepQueryProps}
              />
            </React.Suspense>
          )}

          {currentGroup === 'safety' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载安全与护栏洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsSafetyGroup
                windowMinutes={windowMinutes}
                qaData={qaData}
                {...deepQueryProps}
              />
            </React.Suspense>
          )}

          {currentGroup === 'state' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载状态与规范洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsStateGroup
                windowMinutes={windowMinutes}
                {...deepQueryProps}
              />
            </React.Suspense>
          )}

          {currentGroup === 'vts' && (
            <React.Suspense
              fallback={(
                <div className="card p-4">
                  <LoadingNotice>正在加载表情通道洞察…</LoadingNotice>
                </div>
              )}
            >
              <InsightsVtsGroup vtsData={vtsData} />
            </React.Suspense>
          )}
        </div>
      )}

    </div>
  )
}
