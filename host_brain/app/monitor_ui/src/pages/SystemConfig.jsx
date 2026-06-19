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
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import { useSystemConfigQuery } from '../features/operations/queries.js'
import useAppStore from '../store/useAppStore.js'
import { formatMode, formatReplyPosture } from '../utils/digitalLife.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const FALLBACK_TEXT = '—'

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

  const routeStats = routeData?.model_routes || []
  const callStats = callData?.models || []
  const rateKeys = rateData?.top_keys || []
  const notLiveSlots = useMemo(
    () => Object.values(runtimeSnapshot?.slot_status || {}).filter((status) => status !== 'live').length,
    [runtimeSnapshot]
  )

  const replyPosture = formatReplyPosture(runtimeSnapshot?.runtime_summary?.reply_posture?.style || FALLBACK_TEXT)
  const sysMode = formatMode(runtimeSnapshot?.sys_mode || FALLBACK_TEXT)
  const systemConfigUpdatedAt = Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
  const systemConfigLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(routeData || callData || rateData || healthData || retrievalRuntime),
    hasSamples: Boolean(routeStats.length || callStats.length || healthData?.total || retrievalRuntime?.latest),
    available: true,
    stale,
  })

  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([refetch(), reloadRuntime()])
  }, [refetch, reloadRuntime])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">神经系统底座</h1>
          <p className="text-sm text-gray-500">查看模型路由、检索运行时、限流后端与基础器官状态。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
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
        description={`当前硬状态为 ${sysMode}，回复姿态为 ${replyPosture}，未在线槽位数量为 ${notLiveSlots}。`}
        gradientClass="from-primary-50 via-white to-sky-50"
        metaItems={[
          `器官：${healthData ? `${healthData.up}/${healthData.total}` : FALLBACK_TEXT}`,
          `未知器官：${healthData?.unknown ?? FALLBACK_TEXT}`,
          `限流后端：${rateData?.backend_type || FALLBACK_TEXT}`,
          `检索套件：${retrievalRuntime?.suite_profile || '自定义'}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={systemConfigLoadState}
            windowMinutes={windowMinutes}
            sampleSize={routeStats.length + callStats.length + (healthData?.total ?? 0)}
            sampleLabel="底座对象"
            updatedAt={systemConfigUpdatedAt}
            source="system-config queries + runtime"
            definition="SystemConfig 聚焦模型路由、检索运行时、限流后端和器官健康摘要，主数据已统一交给 query 层。"
          />
        )}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard
          label="写入嵌入模型"
          value={retrievalRuntime?.episodic_write_embedding_model_id || FALLBACK_TEXT}
          icon={Database}
          variant="stacked"
          tone={retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500'}
          hint={retrievalRuntime?.episodic_write_embedding_provider || FALLBACK_TEXT}
        />
        <MetricCard
          label="读取嵌入模型"
          value={retrievalRuntime?.latest?.embedding_model_id || FALLBACK_TEXT}
          icon={SearchCheck}
          variant="stacked"
          tone={retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-500' : 'text-blue-600'}
          hint={retrievalRuntime?.latest?.effective_embedding_provider || FALLBACK_TEXT}
        />
        <MetricCard
          label="读写一致性"
          value={retrievalRuntime?.read_write_model_aligned ? '对齐' : '漂移'}
          icon={Zap}
          variant="stacked"
          tone={retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500'}
          hint={retrievalRuntime?.suite_profile || '自定义'}
        />
        <MetricCard
          label="限流后端"
          value={rateData?.backend_type || FALLBACK_TEXT}
          icon={Shield}
          variant="stacked"
          tone={rateData?.is_degraded ? 'text-orange-500' : 'text-green-600'}
          hint={rateData?.is_degraded ? `降级：${rateData.degrade_reason}` : '正常'}
        />
        <MetricCard
          label="模型路由数"
          value={routeStats.length || FALLBACK_TEXT}
          icon={GitBranch}
          variant="stacked"
          tone="text-blue-600"
        />
        <MetricCard
          label="调用模型数"
          value={callStats.length || FALLBACK_TEXT}
          icon={Cpu}
          variant="stacked"
          tone="text-purple-600"
        />
        <MetricCard
          label="器官健康"
          value={healthData?.up ?? FALLBACK_TEXT}
          icon={Zap}
          variant="stacked"
          tone="text-green-600"
          hint={healthData ? `${healthData.total} 总计 / ${healthData.degraded} 降级 / ${healthData.down} 离线 / ${healthData.unknown} 未知` : ''}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            <GitBranch size={14} className="mr-1 inline" />
            模型路由决策
          </h2>
          {routeStats.length > 0 ? (
            <div className="space-y-2">
              {routeStats.map((route) => (
                <div key={route.model_id || route.route_id} className="rounded bg-gray-50 px-3 py-2 text-xs">
                  <div className="font-mono text-gray-700">{route.model_id || route.route_id}</div>
                  <div className="mt-1 flex items-center justify-between text-gray-500">
                    <span>选择 {route.select_count ?? 0} 次</span>
                    <span>降级 {route.degrade_count ?? 0} 次</span>
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
          {callStats.length > 0 ? (
            <div className="space-y-2">
              {callStats.map((model) => (
                <div key={model.model_id} className="rounded bg-gray-50 px-3 py-2 text-xs">
                  <div className="font-mono text-gray-700">{model.model_id}</div>
                  <div className="mt-1 flex items-center justify-between text-gray-500">
                    <span>{model.call_count ?? 0} 次调用</span>
                    <span>{model.total_tokens ?? 0} tok</span>
                    <span>{model.avg_latency_ms != null ? `${Number(model.avg_latency_ms).toFixed(0)} ms` : FALLBACK_TEXT}</span>
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
          {rateKeys.length > 0 ? (
            <div className="max-h-48 space-y-1 overflow-y-auto">
              {rateKeys.map((keyItem, index) => (
                <div key={index} className="flex items-center justify-between rounded bg-gray-50 px-3 py-1.5 text-xs">
                  <span className="truncate font-mono text-gray-600">{keyItem.key}</span>
                  <span className={keyItem.remaining <= 0 ? 'font-bold text-red-500' : 'text-gray-500'}>
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
          {retrievalRuntime ? (
            <div className="space-y-2 text-sm">
              <div className="rounded bg-gray-50 px-3 py-2">
                <div className="text-xs text-gray-400">当前套件</div>
                <div className="mt-1 font-medium text-gray-800">{retrievalRuntime.suite_profile || '自定义'}</div>
              </div>
              <div className="rounded bg-gray-50 px-3 py-2">
                <div className="text-xs text-gray-400">读取嵌入 Provider</div>
                <div className="mt-1 font-medium text-gray-800">{retrievalRuntime.latest?.effective_embedding_provider || FALLBACK_TEXT}</div>
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
