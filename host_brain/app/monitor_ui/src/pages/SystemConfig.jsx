import React, { useMemo, useState, useCallback } from 'react'
import {
  Cpu,
  Database,
  GitBranch,
  RefreshCw,
  SearchCheck,
  Settings,
  Shield,
  Zap,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useSystemConfigQuery } from '../features/operations/systemConfigQueries.js'
import {
  SYSTEM_CONFIG_WINDOW_OPTIONS,
  buildSystemConfigViewModel,
} from '../features/operations/systemConfigModel.js'
import useAppStore from '../store/useAppStore.js'
import { useGovernanceSnapshotQuery } from '../features/governance/governanceQueries.js'
import { buildGovernanceViewModel } from '../features/governance/governanceModel.js'
import GovernanceHealthSection from '../features/governance/components/GovernanceHealthSection.jsx'

const SYSTEM_CONFIG_METRIC_ICONS = {
  cpu: Cpu,
  database: Database,
  git_branch: GitBranch,
  search: SearchCheck,
  shield: Shield,
  zap: Zap,
}

export default function SystemConfig() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [windowMinutes, setWindowMinutes] = useState(60)
  const {
    routeData,
    callData,
    rateData,
    healthData,
    retrievalRuntime,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
    stale,
  } = useSystemConfigQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const governanceQuery = useGovernanceSnapshotQuery({ pollingEnabled, pollingTick, pollingInterval })
  const governance = useMemo(() => buildGovernanceViewModel(governanceQuery.data), [governanceQuery.data])

  const {
    runtimeSnapshot,
    promptStats,
    reload: reloadRuntime,
    lastUpdatedAt: runtimeUpdatedAt,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const {
    sysMode,
    loadState: systemConfigLoadState,
    updatedAt: systemConfigUpdatedAt,
    heroDescription,
    heroMetaItems,
    sampleSize,
    metricCards,
    routeRows,
    callRows,
    rateKeyRows,
    retrievalRuntimeView,
  } = useMemo(() => buildSystemConfigViewModel({
    routeData,
    callData,
    rateData,
    healthData,
    retrievalRuntime,
    runtimeSnapshot,
    loading,
    refreshing,
    error,
    stale,
    lastUpdatedAt,
    runtimeUpdatedAt,
  }), [
    callData,
    error,
    healthData,
    lastUpdatedAt,
    loading,
    rateData,
    refreshing,
    retrievalRuntime,
    routeData,
    runtimeSnapshot,
    runtimeUpdatedAt,
    stale,
  ])

  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([refetch(), reloadRuntime(), governanceQuery.refetch()])
  }, [refetch, reloadRuntime, governanceQuery])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">神经系统底座</h1>
          <p className="text-sm text-gray-500">查看模型路由、检索运行时、限流后端与基础器官状态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={SYSTEM_CONFIG_WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={handleRefresh} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <PageHero
        icon={Settings}
        eyebrow="神经系统运行底座"
        title={sysMode}
        description={heroDescription}
        gradientClass="from-primary-50 via-white to-sky-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={systemConfigLoadState}
            windowMinutes={windowMinutes}
            sampleSize={sampleSize}
            sampleLabel="底座对象"
            updatedAt={systemConfigUpdatedAt}
            source="system-config queries + runtime"
            definition="SystemConfig 聚焦模型路由、检索运行时、限流后端和器官健康摘要，主数据已统一交给 query 层。"
          />
        )}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard
            key={card.key}
            label={card.label}
            value={card.value}
            icon={SYSTEM_CONFIG_METRIC_ICONS[card.iconKey]}
            variant={card.variant}
            tone={card.tone}
            hint={card.hint}
          />
        ))}
      </div>

      <GovernanceHealthSection model={governance} queryError={governanceQuery.error} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            <GitBranch size={14} className="mr-1 inline" />
            模型路由决策
          </h2>
          {routeRows.length > 0 ? (
            <div className="space-y-2">
              {routeRows.map((route) => (
                <div key={route.key} className="rounded bg-gray-50 px-3 py-2 text-xs">
                  <div className="font-mono text-gray-700">{route.id}</div>
                  <div className="mt-1 flex items-center justify-between text-gray-500">
                    <span>选择 {route.selectCount} 次</span>
                    <span>降级 {route.degradeCount} 次</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无路由数据" hint="当前窗口内没有新的模型路由决策。" className="p-6" />
          )}
        </div>

        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            <Cpu size={14} className="mr-1 inline" />
            模型调用统计
          </h2>
          {callRows.length > 0 ? (
            <div className="space-y-2">
              {callRows.map((model) => (
                <div key={model.key} className="rounded bg-gray-50 px-3 py-2 text-xs">
                  <div className="font-mono text-gray-700">{model.modelId}</div>
                  <div className="mt-1 flex items-center justify-between text-gray-500">
                    <span>{model.callCount} 次调用</span>
                    <span>{model.totalTokens} tok</span>
                    <span>{model.latencyLabel}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无调用数据" hint="当前窗口内没有新的大模型调用记录。" className="p-6" />
          )}
        </div>

        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            <Shield size={14} className="mr-1 inline" />
            限流活跃键
          </h2>
          {rateKeyRows.length > 0 ? (
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {rateKeyRows.map((keyItem) => (
                <div key={keyItem.key} className="flex items-center justify-between rounded bg-gray-50 px-3 py-1.5 text-xs">
                  <span className="truncate font-mono text-gray-600">{keyItem.label}</span>
                  <span className={keyItem.exhausted ? 'font-bold text-red-500' : 'text-gray-500'}>
                    {keyItem.current}/{keyItem.limit}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无活跃键" hint="限流后端当前没有活跃调用键。" className="p-6" />
          )}
        </div>

        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            <Database size={14} className="mr-1 inline" />
            检索运行时
          </h2>
          {retrievalRuntimeView ? (
            <div className="space-y-2 text-sm">
              <div className="rounded bg-gray-50 px-3 py-2">
                <div className="text-xs text-gray-400">当前套件</div>
                <div className="mt-1 font-medium text-gray-800">{retrievalRuntimeView.suiteProfile}</div>
              </div>
              <div className="rounded bg-gray-50 px-3 py-2">
                <div className="text-xs text-gray-400">读取嵌入 Provider</div>
                <div className="mt-1 font-medium text-gray-800">{retrievalRuntimeView.readEmbeddingProvider}</div>
              </div>
            </div>
          ) : (
            <EmptyStateCard title="暂无检索运行时" hint="等待 retrieval runtime 统计返回后再显示。" className="p-6" />
          )}
        </div>
      </div>
    </div>
  )
}
