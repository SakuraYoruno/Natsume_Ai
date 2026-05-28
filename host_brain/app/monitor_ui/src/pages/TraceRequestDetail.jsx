import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Database,
  Layers3,
  MessageSquareText,
  RefreshCw,
} from 'lucide-react'
import {
  connectTraceRequestLive,
} from '../api/obs.js'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import LoadingNotice from '../components/LoadingNotice.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import {
  TraceMetricCard as MetricCard,
  TraceMiniStat as MiniStat,
  TraceSectionCard as SectionCard,
} from '../components/trace/TraceSurfaces.jsx'
import {
  AuditSummaryPanel,
  RawJsonDetails,
  RecallSummaryPanel,
  RetrievalRuntimeOverviewPanel,
  TextPreviewCard,
} from '../components/trace/TraceRequestSummaryPanels.jsx'
import {
  ImmuneReplayPanel,
  LiveEntriesPanel,
  NodeTimelinePanel,
  StageOverviewPanel,
  StreamEventsPanel,
  SubgraphsPanel,
  TribeEventsPanel,
  UnifiedTimelinePanel,
  buildDeltaEntries,
  dedupeStreamEvents,
  mergeDeltaEntries,
} from '../components/trace/TraceRequestRuntimePanels.jsx'
import {
  ISSUE_STATUSES,
  buildRetrievalRuntimeMeta,
  formatDateTime,
  formatIntent,
  getSummaryInputText,
  getSummaryOutputText,
  normalizeStatus,
} from '../components/trace/TraceRequestDetailModel.jsx'
import { useImmuneGateReplayQuery } from '../queries/automationQueries.js'
import {
  useTraceRetrievalRuntimeQuery,
  useTraceSnapshotQuery,
  useTraceStreamEventsQuery,
  useTraceTribeEventsQuery,
} from '../queries/traceQueries.js'
import useAppStore from '../store/useAppStore.js'

export default function TraceRequestDetail() {
  const navigate = useNavigate()
  const { requestId } = useParams()
  const [traceData, setTraceData] = useState(null)
  const [fallbackEvents, setFallbackEvents] = useState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [liveEntries, setLiveEntries] = useState([])
  const [liveConnected, setLiveConnected] = useState(false)
  const [liveLastTs, setLiveLastTs] = useState(null)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const detailPollingEnabled = pollingEnabled && pageVisible
  const traceQuery = useTraceSnapshotQuery({
    requestId,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled && !liveConnected,
    pollingTick,
    pollingInterval,
  })
  const streamEventsQuery = useTraceStreamEventsQuery({
    requestId,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled && !liveConnected,
    pollingTick,
    pollingInterval,
  })
  const tribeEventsQuery = useTraceTribeEventsQuery({
    requestId,
    limit: 120,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const retrievalRuntimeQuery = useTraceRetrievalRuntimeQuery({
    requestId,
    limit: 5,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(15, pollingInterval),
  })
  const immuneReplayQuery = useImmuneGateReplayQuery({
    requestId,
    limit: 160,
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const loading = traceQuery.loading
  const error = traceQuery.traceError
  const eventsLoading = streamEventsQuery.loading
  const eventsError = streamEventsQuery.streamEventsError
  const tribeEventsLoading = tribeEventsQuery.loading
  const tribeEventsError = tribeEventsQuery.tribeEventsError
  const immuneReplayLoading = immuneReplayQuery.loading
  const immuneReplayError = (() => {
    const message = String(immuneReplayQuery.replayError || '')
    return message.includes('HTTP 404') ? null : (message || null)
  })()
  const tribeEvents = Array.isArray(tribeEventsQuery.tribeEventsPayload?.items) ? tribeEventsQuery.tribeEventsPayload.items : []
  const retrievalRuntime = retrievalRuntimeQuery.retrievalRuntime
  const immuneReplay = immuneReplayQuery.replayPayload

  useEffect(() => {
    setFallbackEvents(streamEventsQuery.streamEventsPayload?.events ?? [])
  }, [streamEventsQuery.streamEventsPayload])

  useEffect(() => {
    if (!requestId) {
      setTraceData(null)
      return
    }
    setTraceData(traceQuery.traceSnapshot)
  }, [requestId, traceQuery.traceSnapshot])

  useEffect(() => {
    if (!requestId || !pageVisible) {
      setLiveConnected(false)
      return undefined
    }
    let closed = false
    let connection
    try {
      connection = connectTraceRequestLive(requestId, {
        onOpen: () => { if (!closed) setLiveConnected(true) },
        onSnapshot: (payload) => {
          if (closed) return
          const matched = Array.isArray(payload?.items)
            ? payload.items.find((item) => item?.session?.request_id === requestId || item?.trace?.request_id === requestId)
            : null
          if (matched?.trace) {
            setTraceData(matched.trace)
            setFallbackEvents((previous) => dedupeStreamEvents(matched.trace.stream_events, previous))
          }
          setLiveLastTs(payload?.timestamp ?? Date.now())
        },
        onDelta: (payload) => {
          if (closed) return
          const matched = Array.isArray(payload?.items)
            ? payload.items.find((item) => item?.request_id === requestId || item?.session?.request_id === requestId)
            : null
          if (matched) setLiveEntries((previous) => mergeDeltaEntries(previous, buildDeltaEntries(matched)))
          setLiveLastTs(payload?.timestamp ?? Date.now())
        },
        onHeartbeat: (payload) => { if (!closed) setLiveLastTs(payload?.timestamp ?? Date.now()) },
        onConnectionError: () => { if (!closed) setLiveConnected(false) },
      })
    } catch {
      setLiveConnected(false)
      return undefined
    }
    return () => {
      closed = true
      connection?.close?.()
    }
  }, [pageVisible, requestId])

  const nodes = useMemo(() => {
    const items = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
    items.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
    return items
  }, [traceData])

  useEffect(() => {
    if (!nodes.length) return setSelectedNode(null)
    setSelectedNode((previous) => nodes.find((node) => node.node_id === previous?.node_id) || nodes[nodes.length - 1])
  }, [nodes])

  const streamEvents = useMemo(
    () => dedupeStreamEvents(traceData?.stream_events, fallbackEvents),
    [traceData, fallbackEvents]
  )
  const summary = traceData?.summary ?? {}
  const degradedReason = String(summary?.degraded_reason || '').trim()
  const degradedMessage = String(summary?.degraded_message || '').trim()
  const traceDegraded = Boolean(summary?.degraded)
  const subgraphs = Array.isArray(traceData?.subgraphs) ? traceData.subgraphs : []
  const inputPreview = getSummaryInputText(summary)
  const outputPreview = getSummaryOutputText(summary)
  const latestStreamEvent = streamEvents[0] ?? null
  const issueNodeCount = nodes.filter((node) => ISSUE_STATUSES.has(String(node.status || '').toUpperCase())).length
  const runningNodeCount = nodes.filter((node) => RUNNING_STATUSES.has(String(node.status || '').toUpperCase())).length
  const retrievalMeta = buildRetrievalRuntimeMeta(retrievalRuntime)
  const liveModeText = liveConnected ? 'SSE 实时流已连接' : '实时流重连中 / 轮询补齐'
  const tracePulseNodes = [
    {
      label: '请求状态',
      value: traceData?.status || '待加载',
      tone: 'primary',
      active: Boolean(traceData?.status),
    },
    {
      label: '节点覆盖',
      value: `${traceData?.node_count ?? nodes.length}`,
      tone: 'teal',
      active: (traceData?.node_count ?? nodes.length) > 0,
    },
    {
      label: '异常节点',
      value: String(issueNodeCount),
      tone: 'rose',
      active: issueNodeCount > 0,
    },
    {
      label: '流式事件',
      value: String(streamEvents.length),
      tone: 'violet',
      active: streamEvents.length > 0,
    },
    {
      label: 'Tribe 联动',
      value: String(tribeEvents.length),
      tone: 'amber',
      active: tribeEvents.length > 0,
    },
    {
      label: '实时模式',
      value: liveModeText,
      tone: 'primary',
      active: liveConnected,
    },
  ]
  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([
      traceQuery.refetch(),
      streamEventsQuery.refetch(),
      tribeEventsQuery.refetch(),
      retrievalRuntimeQuery.refetch(),
      immuneReplayQuery.refetch(),
    ])
  }, [immuneReplayQuery, retrievalRuntimeQuery, streamEventsQuery, traceQuery, tribeEventsQuery])

  return (
    <div className="space-y-4">
      <PageHero
        icon={Layers3}
        eyebrow="单请求链路详情"
        title="请求级 Trace 深入查看"
        description="把单条 request 的输入、检索、节点时间线、流式事件和子代理内部图收成一屏，便于逐条排查。"
        gradientClass="from-sky-50 via-white to-violet-50"
        metaItems={[
          traceData?.status ? `状态：${traceData.status}` : '状态：待加载',
          `节点：${traceData?.node_count ?? nodes.length}`,
          `异常/降级：${issueNodeCount}`,
          liveModeText,
        ]}
      />

      <LivingPulseStrip
        title="请求脉冲"
        subtitle="这条 request 的生命流已经收束到单页里，先看它现在的节奏，再继续下钻到节点和事件。"
        nodes={tracePulseNodes}
      />

      <div className="card space-y-3 p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <button className="btn-secondary text-xs" onClick={() => navigate('/trace')}>
            <ArrowLeft size={14} />
            返回
          </button>
          <div className="min-w-0">
            <div className="text-xs text-gray-500">请求链路</div>
            <div className="break-all font-mono text-sm text-gray-900">{requestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            {traceData ? <StatusBadge status={normalizeStatus(traceData.status)} label={traceData.status} /> : null}
            {requestId ? (
              <a
                href={`#/automation?immune_replay_request_id=${encodeURIComponent(requestId)}`}
                className="btn-secondary text-xs"
              >
                打开 Immune Ops
              </a>
            ) : null}
            <button className="btn-secondary flex items-center gap-1 text-xs" onClick={() => void handleRefresh()}>
              <RefreshCw size={12} />
              刷新
            </button>
          </div>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 md:grid-cols-4">
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">节点：{traceData?.node_count ?? nodes.length}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">异常/降级：{issueNodeCount}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">流式事件：{streamEvents.length}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">{liveModeText}</div>
        </div>
      </div>

      {loading ? <LoadingNotice>正在加载请求链路详情…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="链路详情加载失败">{error}</NoticeBanner> : null}
      {!loading && !error && traceDegraded ? (
        <NoticeBanner variant="warning" title="当前为降级链路视图">
          {degradedMessage || '主链路数据已返回，部分扩展数据稍后补齐。'}
          {degradedReason ? `（${degradedReason}）` : ''}
        </NoticeBanner>
      ) : null}

      {!loading && !error && traceData ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="状态" value={traceData.status} hint={`决策 ${summary.decision || '-'}`} valueClassName={issueNodeCount > 0 ? 'text-orange-600' : ''} />
            <MetricCard label="会话" value={traceData.session_id || summary.session_id || '-'} hint={summary.task_type || '无 task_type'} />
            <MetricCard label="总耗时" value={traceData.total_latency_ms != null ? `${traceData.total_latency_ms}ms` : '-'} hint={formatDateTime(traceData.ts_ms)} />
            <MetricCard label="节点覆盖" value={`${traceData.node_count ?? nodes.length}`} hint={`运行中 ${runningNodeCount} · 异常 ${issueNodeCount}`} />
            <MetricCard label="流式事件" value={streamEvents.length} hint={latestStreamEvent ? `最新 ${formatDateTime(latestStreamEvent.ts_ms)}` : '等待事件'} />
            <MetricCard label="子图数" value={subgraphs.length} hint={liveConnected ? '实时连接中' : '轮询模式'} />
          </div>

          <StageOverviewPanel nodes={nodes} selectedNode={selectedNode} onSelectNode={setSelectedNode} />

          <ImmuneReplayPanel
            replay={immuneReplay}
            loading={immuneReplayLoading}
            error={immuneReplayError}
            requestId={requestId}
            onReload={() => { void immuneReplayQuery.refetch() }}
          />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <SectionCard
              icon={MessageSquareText}
              title="请求概览"
              description="先看本次输入、输出和决策摘要，确认这条链路在语义层面是否走对。"
              aside={summary.failure_event ? <StatusBadge status="error" label="含失败事件" /> : null}
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <TextPreviewCard label="输入文本" text={inputPreview} emptyLabel="暂无输入文本" />
                <TextPreviewCard
                  label={summary.failure_event ? '输出 / 失败摘要' : '输出文本'}
                  text={outputPreview}
                  emptyLabel="暂无输出文本"
                  tone={summary.failure_event ? 'error' : 'default'}
                />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                <MiniStat label="意图" value={formatIntent(summary.intent)} />
                <MiniStat label="路由选择" value={summary.routing_selected || '-'} hint={summary.reason_code || '无原因码'} />
                <MiniStat label="Bond 阶段" value={summary.bond_stage || '-'} hint={summary.bond_score != null ? `总分 ${summary.bond_score}` : '暂无分值'} />
                <MiniStat label="LLM 调用" value={summary.llm_calls ?? 0} hint={summary.governor_passed == null ? '未记录 governor 结果' : (summary.governor_passed ? 'Governor 通过' : 'Governor 拦截')} />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div className="space-y-4">
                  <div className="text-xs font-medium text-gray-500">召回摘要</div>
                  <RecallSummaryPanel recall={summary.recall} />
                  <RawJsonDetails title="原始召回摘要" data={summary.recall} emptyLabel="暂无召回摘要" />
                </div>
                <div className="space-y-4">
                  <div className="text-xs font-medium text-gray-500">审计与摘要</div>
                  <AuditSummaryPanel summary={summary} />
                  <RawJsonDetails title="原始摘要详情" data={summary} emptyLabel="暂无摘要" />
                </div>
              </div>
            </SectionCard>

            <SectionCard
              icon={Database}
              title="检索与运行时"
              description="这里聚合当前请求实际使用的 embedding 模型、fallback 情况和最近观测值。"
              aside={retrievalMeta.lastObservedAt ? <div className="text-xs text-gray-400">{formatDateTime(retrievalMeta.lastObservedAt)}</div> : null}
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-2">
                <MiniStat label="模型套件" value={retrievalMeta.suiteProfile} hint={retrievalMeta.alignment} />
                <MiniStat label="候选数" value={retrievalMeta.candidateCount != null ? `${retrievalMeta.candidateCount}` : '-'} hint="当前请求召回候选量" />
                <MiniStat label="Embedding 模型" value={retrievalMeta.embeddingModel} hint={retrievalMeta.embeddingProvider} toneClassName={retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-600' : 'text-gray-900'} />
                <MiniStat label="Embedding fallback" value={retrievalMeta.embeddingFallbacks} hint={retrievalRuntime?.latest?.fallback_embedding_reason_code || '最近窗口统计'} toneClassName={retrievalMeta.embeddingFallbacks > 0 ? 'text-orange-600' : 'text-gray-900'} />
              </div>

              <div className="mt-4">
                <RetrievalRuntimeOverviewPanel retrievalMeta={retrievalMeta} retrievalRuntime={retrievalRuntime} />
              </div>
            </SectionCard>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <SubgraphsPanel subgraphs={subgraphs} selectedNode={selectedNode} onSelectNode={setSelectedNode} />
            <StreamEventsPanel events={streamEvents} loading={eventsLoading} error={eventsError} />
          </div>

          <TribeEventsPanel
            requestId={requestId}
            events={tribeEvents}
            loading={tribeEventsLoading}
            error={tribeEventsError}
          />

          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.5fr)_20rem]">
            <div className="space-y-4">
              <LiveEntriesPanel liveEntries={liveEntries} liveConnected={liveConnected} liveLastTs={liveLastTs} />
              <UnifiedTimelinePanel
                nodes={nodes}
                tribeEvents={tribeEvents}
                selectedNode={selectedNode}
                onSelectNode={setSelectedNode}
              />
              <NodeTimelinePanel nodes={nodes} selectedNode={selectedNode} onSelectNode={setSelectedNode} />
            </div>

            <div className="xl:sticky xl:top-4">
              {selectedNode ? <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} /> : <EmptyStateCard title="请选择节点" hint="选中左侧节点后可查看输入、输出与链路元数据。" className="p-6" />}
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
