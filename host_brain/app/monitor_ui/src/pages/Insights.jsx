import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { RefreshCw, ShieldCheck, AlertTriangle, Bot, Database, Tv, GitMerge, Activity, ArrowLeft } from 'lucide-react'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import LoadingNotice from '../components/LoadingNotice.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import { useInsightsSummaryQuery } from '../queries/insightsQueries.js'
import useAppStore from '../store/useAppStore.js'

const InsightsGlobalGroup = React.lazy(() => import('../components/insights/InsightsGlobalGroup.jsx'))
const InsightsMemoryGroup = React.lazy(() => import('../components/insights/InsightsMemoryGroup.jsx'))
const InsightsLlmGroup = React.lazy(() => import('../components/insights/InsightsLlmGroup.jsx'))
const InsightsSafetyGroup = React.lazy(() => import('../components/insights/InsightsSafetyGroup.jsx'))
const InsightsStateGroup = React.lazy(() => import('../components/insights/InsightsStateGroup.jsx'))
const InsightsVtsGroup = React.lazy(() => import('../components/insights/InsightsVtsGroup.jsx'))

const WINDOW_OPTIONS = [
  { label: '1h',  value: 60    },
  { label: '6h',  value: 360   },
  { label: '24h', value: 1440  },
  { label: '7d',  value: 10080 },
]

const INSIGHT_GROUPS = [
  { id: 'global', title: '全局与异常', icon: AlertTriangle, desc: '入口守护决策、全局异常追踪、热力图、请求链路统计、失败事件', color: 'text-red-600', bg: 'bg-gradient-to-br from-red-50 to-red-100 border border-red-100', iconBg: 'bg-red-100' },
  { id: 'memory', title: '器官与记忆', icon: Database, desc: '器官健康、记忆统计、情景写入、上下文压缩', color: 'text-teal-700', bg: 'bg-gradient-to-br from-teal-50 to-teal-100 border border-teal-100', iconBg: 'bg-teal-100' },
  { id: 'llm', title: '执行与推理', icon: Bot, desc: '执行循环、模型成本、路由决策、提示构建、五路召回', color: 'text-indigo-700', bg: 'bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-100', iconBg: 'bg-indigo-100' },
  { id: 'safety', title: '安全与护栏', icon: ShieldCheck, desc: '质检违规、权限拒绝、规则校验、界面断言安全', color: 'text-emerald-700', bg: 'bg-gradient-to-br from-emerald-50 to-emerald-100 border border-emerald-100', iconBg: 'bg-emerald-100' },
  { id: 'state', title: '状态与规范', icon: GitMerge, desc: '关系查询统计、主设定提案', color: 'text-cyan-700', bg: 'bg-gradient-to-br from-cyan-50 to-cyan-100 border border-cyan-100', iconBg: 'bg-cyan-100' },
  { id: 'vts', title: '表情通道', icon: Tv, desc: '表情通道统计', color: 'text-pink-700', bg: 'bg-gradient-to-br from-pink-50 to-pink-100 border border-pink-100', iconBg: 'bg-pink-100' },
]

// ===== 主页面 =====
export default function Insights() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const navigate = useNavigate()
  const location = useLocation()
  const currentGroup = location.pathname.split('/').pop()
  const focusTarget = new URLSearchParams(location.search).get('focus')
  const isRoot = currentGroup === 'insights' || currentGroup === ''
  const currentGroupMeta = INSIGHT_GROUPS.find((group) => group.id === currentGroup) || null

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
  const insightPulseNodes = [
    {
      label: '窗口',
      value: `${windowMinutes}m`,
      tone: 'primary',
      active: true,
    },
    {
      label: '全局异常',
      value: String(errData?.total_errors ?? 0),
      tone: 'rose',
      active: (errData?.total_errors ?? 0) > 0,
    },
    {
      label: '记忆器官',
      value: String(memoryData?.subsystems?.length ?? 0),
      tone: 'teal',
      active: Boolean(memoryData?.subsystems?.length),
    },
    {
      label: '执行循环',
      value: String(agentData?.total_loops ?? 0),
      tone: 'violet',
      active: (agentData?.total_loops ?? 0) > 0,
    },
    {
      label: '表情通道',
      value: String(vtsData?.total_events ?? 0),
      tone: 'pink',
      active: (vtsData?.total_events ?? 0) > 0,
    },
    {
      label: '模型呼吸',
      value: String(llmCallData?.total_calls ?? 0),
      tone: 'amber',
      active: (llmCallData?.total_calls ?? 0) > 0,
    },
  ]

  useEffect(() => {
    if (currentGroup !== 'global' || focusTarget !== 'forge-risk') {
      return undefined
    }
    const timer = setTimeout(() => {
      document.getElementById('forge-runtime-risk')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 120)
    return () => clearTimeout(timer)
  }, [currentGroup, focusTarget])

  const insightsUpdatedAt = Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
  const insightsSourceCount = [
    govData,
    errData,
    subsysData,
    agentData,
    memoryData,
    vtsData,
    qaData,
    llmCallData,
  ].filter(Boolean).length
  const insightsLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: insightsSourceCount > 0,
    hasSamples: insightsSourceCount > 0,
    available: true,
    stale,
  })

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
            <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
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
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">窗口：{windowMinutes}m</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">全局异常：{errData?.total_errors ?? 0}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">子系统：{subsysData?.subsystems?.length ?? 0}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">执行循环：{agentData?.total_loops ?? 0}</div>
        </div>
      </div>

      <PageHero
        icon={currentGroupMeta?.icon || Activity}
        eyebrow={isRoot ? '数字生命体综合洞察' : `${currentGroupMeta?.title || '器官分组'} 深度洞察`}
        title={isRoot ? '数字生命体器官级洞察面板' : currentGroupMeta?.title || '器官分组'}
        description={
          isRoot
            ? '从全局异常、记忆器官、安全守卫到 VTS 通道，先看总览，再按分组继续下钻。'
            : currentGroupMeta?.desc || '查看该分组下的详细指标、图表与最近事件。'
        }
        gradientClass="from-amber-50 via-white to-sky-50"
        metaItems={[
          `时间窗口：${windowMinutes} 分钟`,
          `全局错误：${errData?.total_errors ?? 0}`,
          `子系统：${subsysData?.subsystems?.length ?? 0}`,
          `Agent Loop：${agentData?.total_loops ?? 0}`,
          `VTS 事件：${vtsData?.total_events ?? 0}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={insightsLoadState}
            windowMinutes={windowMinutes}
            sampleSize={insightsSourceCount}
            sampleLabel="摘要分区"
            updatedAt={insightsUpdatedAt}
            source="insights summary + runtime"
            definition="Insights 的页首摘要已经切到统一 query 层，后续各分组 section 会继续逐步迁移。"
          />
        )}
      />

      {isRoot ? (
        <LivingPulseStrip
          title="器官脉冲"
          subtitle="先看全局生命节律，再下钻到各个器官分区。"
          nodes={insightPulseNodes}
        />
      ) : null}

      {error && <NoticeBanner variant="error">加载失败：{error.message}</NoticeBanner>}
      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptBuilderData} compact={!isRoot} />
      {isRoot ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {INSIGHT_GROUPS.map(g => (
            <button
              type="button"
              key={g.id}
              className={`card-hover group w-full cursor-pointer p-0 text-left ${g.bg}`}
              onClick={() => navigate(`/insights/${g.id}`)}
            >
              <div className="space-y-4 p-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`rounded-lg p-2.5 shadow-sm ${g.iconBg} ${g.color}`}>
                      <g.icon size={24} strokeWidth={2} />
                    </div>
                    <h3 className="text-lg font-bold tracking-tight text-gray-800">{g.title}</h3>
                  </div>
                  <div className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${g.iconBg} ${g.color} bg-opacity-50`}>
                    <span>进入</span>
                    <ArrowLeft size={12} className="rotate-180 transition-transform group-hover:translate-x-0.5 group-hover:rotate-180" />
                  </div>
                </div>
                <p className="text-sm leading-relaxed text-gray-600">{g.desc}</p>
                <div className="lifeline-rail h-1.5">
                  <div className="lifeline-wave h-full w-full" />
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="space-y-8">
          <div className="card p-4">
            <button className="btn-secondary" onClick={() => navigate('/insights')}>
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
