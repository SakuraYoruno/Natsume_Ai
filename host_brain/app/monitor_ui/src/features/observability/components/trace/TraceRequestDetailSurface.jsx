import React from 'react'
import {
  ArrowLeft,
  Database,
  Layers3,
  MessageSquareText,
  RefreshCw,
} from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import NodeDetailPanel from './NodeDetailPanel.jsx'
import {
  TraceMetricCard as MetricCard,
  TraceMiniStat as MiniStat,
  TraceSectionCard as SectionCard,
} from './TraceSurfaces.jsx'
import {
  AuditSummaryPanel,
  RawJsonDetails,
  RecallSummaryPanel,
  RetrievalRuntimeOverviewPanel,
  TextPreviewCard,
} from './TraceRequestSummaryPanels.jsx'
import {
  ImmuneReplayPanel,
  LiveEntriesPanel,
  NodeTimelinePanel,
  StageOverviewPanel,
  StreamEventsPanel,
  SubgraphsPanel,
  TribeEventsPanel,
  UnifiedTimelinePanel,
} from './TraceRequestRuntimePanels.jsx'
import {
  formatDateTime,
  formatIntent,
  normalizeStatus,
} from './TraceRequestDetailModel.jsx'

function TraceRequestHeaderCard({
  requestId,
  traceData,
  nodes,
  issueNodeCount,
  streamEvents,
  liveModeText,
  onNavigateBack,
  onRefresh,
}) {
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-center gap-3 flex-wrap">
        <button className="btn-secondary text-xs" onClick={onNavigateBack}>
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
              href={`#/execution/automation?immune_replay_request_id=${encodeURIComponent(requestId)}`}
              className="btn-secondary text-xs"
            >
              打开 Immune Ops
            </a>
          ) : null}
          <button className="btn-secondary flex items-center gap-1 text-xs" onClick={onRefresh}>
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
  )
}

function TraceRequestMetricGrid({
  traceData,
  nodes,
  summary,
  issueNodeCount,
  runningNodeCount,
  streamEvents,
  latestStreamEvent,
  subgraphs,
  liveConnected,
}) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6">
      <MetricCard label="状态" value={traceData.status} hint={`决策 ${summary.decision || '-'}`} valueClassName={issueNodeCount > 0 ? 'text-orange-600' : ''} />
      <MetricCard label="会话" value={traceData.session_id || summary.session_id || '-'} hint={summary.task_type || '无 task_type'} />
      <MetricCard label="总耗时" value={traceData.total_latency_ms != null ? `${traceData.total_latency_ms}ms` : '-'} hint={formatDateTime(traceData.ts_ms)} />
      <MetricCard label="节点覆盖" value={`${traceData.node_count ?? nodes.length}`} hint={`运行中 ${runningNodeCount} · 异常 ${issueNodeCount}`} />
      <MetricCard label="流式事件" value={streamEvents.length} hint={latestStreamEvent ? `最新 ${formatDateTime(latestStreamEvent.ts_ms)}` : '等待事件'} />
      <MetricCard label="子图数" value={subgraphs.length} hint={liveConnected ? '实时连接中' : '轮询模式'} />
    </div>
  )
}

function TraceRequestSummarySection({
  summary,
  inputPreview,
  outputPreview,
}) {
  return (
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
  )
}

function TraceRetrievalRuntimeSection({
  retrievalMeta,
  retrievalRuntime,
}) {
  return (
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
  )
}

function TraceRequestLoadedContent({
  requestId,
  traceData,
  nodes,
  summary,
  inputPreview,
  outputPreview,
  issueNodeCount,
  runningNodeCount,
  streamEvents,
  latestStreamEvent,
  subgraphs,
  tribeEvents,
  retrievalMeta,
  retrievalRuntime,
  immuneReplay,
  immuneReplayLoading,
  immuneReplayError,
  eventsLoading,
  eventsError,
  tribeEventsLoading,
  tribeEventsError,
  liveConnected,
  liveEntries,
  liveLastTs,
  selectedNode,
  onSelectNode,
  onReloadImmuneReplay,
}) {
  return (
    <>
      <TraceRequestMetricGrid
        traceData={traceData}
        nodes={nodes}
        summary={summary}
        issueNodeCount={issueNodeCount}
        runningNodeCount={runningNodeCount}
        streamEvents={streamEvents}
        latestStreamEvent={latestStreamEvent}
        subgraphs={subgraphs}
        liveConnected={liveConnected}
      />

      <StageOverviewPanel nodes={nodes} selectedNode={selectedNode} onSelectNode={onSelectNode} />

      <ImmuneReplayPanel
        replay={immuneReplay}
        loading={immuneReplayLoading}
        error={immuneReplayError}
        requestId={requestId}
        onReload={onReloadImmuneReplay}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <TraceRequestSummarySection
          summary={summary}
          inputPreview={inputPreview}
          outputPreview={outputPreview}
        />

        <TraceRetrievalRuntimeSection
          retrievalMeta={retrievalMeta}
          retrievalRuntime={retrievalRuntime}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SubgraphsPanel subgraphs={subgraphs} selectedNode={selectedNode} onSelectNode={onSelectNode} />
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
            onSelectNode={onSelectNode}
          />
          <NodeTimelinePanel nodes={nodes} selectedNode={selectedNode} onSelectNode={onSelectNode} />
        </div>

        <div className="xl:sticky xl:top-4">
          {selectedNode ? <NodeDetailPanel node={selectedNode} onClose={() => onSelectNode(null)} /> : <EmptyStateCard title="请选择节点" hint="选中左侧节点后可查看输入、输出与链路元数据。" className="p-6" />}
        </div>
      </div>
    </>
  )
}

export default function TraceRequestDetailSurface({
  requestId,
  traceData,
  nodes,
  summary,
  inputPreview,
  outputPreview,
  issueNodeCount,
  runningNodeCount,
  streamEvents,
  latestStreamEvent,
  subgraphs,
  tribeEvents,
  retrievalMeta,
  retrievalRuntime,
  immuneReplay,
  immuneReplayLoading,
  immuneReplayError,
  eventsLoading,
  eventsError,
  tribeEventsLoading,
  tribeEventsError,
  loading,
  error,
  traceDegraded,
  degradedMessage,
  degradedReason,
  liveModeText,
  tracePulseNodes,
  liveConnected,
  liveEntries,
  liveLastTs,
  selectedNode,
  onSelectNode,
  onNavigateBack,
  onRefresh,
  onReloadImmuneReplay,
}) {
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

      <TraceRequestHeaderCard
        requestId={requestId}
        traceData={traceData}
        nodes={nodes}
        issueNodeCount={issueNodeCount}
        streamEvents={streamEvents}
        liveModeText={liveModeText}
        onNavigateBack={onNavigateBack}
        onRefresh={onRefresh}
      />

      {loading ? <LoadingNotice>正在加载请求链路详情…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="链路详情加载失败">{error}</NoticeBanner> : null}
      {!loading && !error && traceDegraded ? (
        <NoticeBanner variant="warning" title="当前为降级链路视图">
          {degradedMessage || '主链路数据已返回，部分扩展数据稍后补齐。'}
          {degradedReason ? `（${degradedReason}）` : ''}
        </NoticeBanner>
      ) : null}

      {!loading && !error && traceData ? (
        <TraceRequestLoadedContent
          requestId={requestId}
          traceData={traceData}
          nodes={nodes}
          summary={summary}
          inputPreview={inputPreview}
          outputPreview={outputPreview}
          issueNodeCount={issueNodeCount}
          runningNodeCount={runningNodeCount}
          streamEvents={streamEvents}
          latestStreamEvent={latestStreamEvent}
          subgraphs={subgraphs}
          tribeEvents={tribeEvents}
          retrievalMeta={retrievalMeta}
          retrievalRuntime={retrievalRuntime}
          immuneReplay={immuneReplay}
          immuneReplayLoading={immuneReplayLoading}
          immuneReplayError={immuneReplayError}
          eventsLoading={eventsLoading}
          eventsError={eventsError}
          tribeEventsLoading={tribeEventsLoading}
          tribeEventsError={tribeEventsError}
          liveConnected={liveConnected}
          liveEntries={liveEntries}
          liveLastTs={liveLastTs}
          selectedNode={selectedNode}
          onSelectNode={onSelectNode}
          onReloadImmuneReplay={onReloadImmuneReplay}
        />
      ) : null}
    </div>
  )
}
