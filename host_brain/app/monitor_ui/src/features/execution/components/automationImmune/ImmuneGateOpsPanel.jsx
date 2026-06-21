import { useCallback, useEffect, useState } from 'react'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../../../../models/loadState.js'
import { formatPercent } from '../../../../shared/formatters.js'
import { AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES, resolveStatusClass } from '../../../../shared/statusMeta.js'
import {
  useImmuneGateReplayQuery,
  useImmuneGateStatsQuery,
} from '../../automationQueries.js'
import ImmuneGateReasonCodesPanel from './ImmuneGateReasonCodesPanel.jsx'
import ImmuneGateRecentEventsPanel from './ImmuneGateRecentEventsPanel.jsx'
import ImmuneGateReplayPanel from './ImmuneGateReplayPanel.jsx'
import ImmuneGateRuntimePanel from './ImmuneGateRuntimePanel.jsx'
import ImmuneGateStatsGrid from './ImmuneGateStatsGrid.jsx'
import ImmuneGateToolbar from './ImmuneGateToolbar.jsx'

export default function ImmuneGateOpsPanel({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  initialReplayRequestId = '',
}) {
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [replayRequestId, setReplayRequestId] = useState('')
  const [activeReplayRequestId, setActiveReplayRequestId] = useState('')
  const {
    stats,
    statsError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useImmuneGateStatsQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    replayPayload,
    replayError,
    loading: replayLoading,
    refreshing: replayRefreshing,
    lastUpdatedAt: replayUpdatedAt,
    refetch: refetchReplay,
  } = useImmuneGateReplayQuery({
    requestId: activeReplayRequestId,
    limit: 120,
  })

  useEffect(() => {
    const rid = String(initialReplayRequestId || '').trim()
    if (!rid) return
    setReplayRequestId(rid)
    setActiveReplayRequestId(rid)
  }, [initialReplayRequestId])

  const handleReplayLoad = useCallback((requestId) => {
    const rid = String(requestId || replayRequestId || '').trim()
    if (!rid) return
    setReplayRequestId(rid)
    if (rid === activeReplayRequestId) {
      void refetchReplay()
      return
    }
    setActiveReplayRequestId(rid)
  }, [activeReplayRequestId, refetchReplay, replayRequestId])

  const handleRefresh = useCallback(() => {
    void Promise.allSettled([
      refetch(),
      activeReplayRequestId ? refetchReplay() : Promise.resolve(),
    ])
  }, [activeReplayRequestId, refetch, refetchReplay])

  const runtimeState = stats?.runtime_state || {}
  const counters = runtimeState?.counters || {}
  const reasonCodes = Array.isArray(stats?.reason_codes) ? stats.reason_codes : []
  const recent = Array.isArray(stats?.recent) ? stats.recent : []
  const runtimeStatus = String(runtimeState?.status || 'unknown').toLowerCase()
  const statusClass = resolveStatusClass(
    AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES,
    runtimeStatus,
    'border-gray-200 bg-gray-50 text-gray-600',
  )
  const immunePulseNodes = [
    {
      label: '运行态',
      value: runtimeStatus.toUpperCase() || 'UNKNOWN',
      tone: 'primary',
      active: runtimeStatus === 'live',
    },
    {
      label: '总事件',
      value: String(stats?.total ?? 0),
      tone: 'teal',
      active: (stats?.total ?? 0) > 0,
    },
    {
      label: '允许',
      value: String(stats?.allow ?? 0),
      tone: 'teal',
      active: (stats?.allow ?? 0) > 0,
    },
    {
      label: '报告',
      value: String(stats?.report ?? 0),
      tone: 'amber',
      active: (stats?.report ?? 0) > 0,
    },
    {
      label: '拦截',
      value: String(stats?.block ?? 0),
      tone: 'rose',
      active: (stats?.block ?? 0) > 0,
    },
    {
      label: '拦截率',
      value: formatPercent(stats?.block_rate, 0),
      tone: 'violet',
      active: Number(stats?.block_rate ?? 0) > 0,
    },
  ]
  const immuneLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: statsError && !stats ? new Error(statsError) : null,
    hasData: Boolean(stats),
    hasSamples: (stats?.total ?? 0) > 0 || recent.length > 0 || reasonCodes.length > 0,
    available: Boolean(stats),
    stale,
  })

  return (
    <div className="space-y-4">
      <ImmuneGateToolbar
        windowMinutes={windowMinutes}
        onWindowMinutesChange={setWindowMinutes}
        replayRequestId={replayRequestId}
        onReplayRequestIdChange={setReplayRequestId}
        loading={loading}
        refreshing={refreshing}
        replayLoading={replayLoading}
        replayRefreshing={replayRefreshing}
        onRefresh={handleRefresh}
        onReplayLoad={handleReplayLoad}
      />

      <LivingPulseStrip
        title="免疫脉冲"
        subtitle="把免疫门禁的运行态、事件总量和拦截/报告分布压成一条可读节律。"
        nodes={immunePulseNodes}
      />

      <SampleMeta
        loadState={immuneLoadState}
        windowMinutes={windowMinutes}
        sampleSize={stats?.total ?? 0}
        sampleLabel="免疫事件"
        updatedAt={lastUpdatedAt}
        source="immune gate"
        definition="这一段聚合 immune gate 运行态、原因热区、近期事件和指定 request 的免疫回放。"
      />

      {statsError ? (
        <NoticeBanner variant={stats ? 'warning' : 'error'}>
          {statsError}
        </NoticeBanner>
      ) : null}

      {!statsError && !loading && !refreshing && immuneLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前窗口还没有 immune gate 事件样本。</NoticeBanner>
      ) : null}

      {replayError ? <NoticeBanner variant="error">{replayError}</NoticeBanner> : null}

      <ImmuneGateStatsGrid runtimeStatus={runtimeStatus} statusClass={statusClass} stats={stats} />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <ImmuneGateRuntimePanel runtimeState={runtimeState} counters={counters} />
        <ImmuneGateReasonCodesPanel reasonCodes={reasonCodes} />
        <ImmuneGateRecentEventsPanel recent={recent} onReplayLoad={handleReplayLoad} />
      </div>

      <ImmuneGateReplayPanel
        activeReplayRequestId={activeReplayRequestId}
        replayLoading={replayLoading}
        replayRefreshing={replayRefreshing}
        replayPayload={replayPayload}
        replayError={replayError}
        replayUpdatedAt={replayUpdatedAt}
      />
    </div>
  )
}
