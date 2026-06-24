import React from 'react'
import { Layers3 } from 'lucide-react'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import TraceRequestDetailHeader from './TraceRequestDetailHeader.jsx'
import TraceRequestDetailLoadedContent from './TraceRequestDetailLoadedContent.jsx'

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
  triageRail,
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

      <TraceRequestDetailHeader
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
        <TraceRequestDetailLoadedContent
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
          triageRail={triageRail}
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
