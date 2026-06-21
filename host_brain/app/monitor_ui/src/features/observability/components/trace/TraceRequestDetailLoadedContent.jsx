import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import NodeDetailPanel from './NodeDetailPanel.jsx'
import TraceRequestDetailMetrics from './TraceRequestDetailMetrics.jsx'
import {
  TraceRequestSummarySection,
  TraceRetrievalRuntimeSection,
} from './TraceRequestDetailOverviewSections.jsx'
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

export default function TraceRequestDetailLoadedContent({
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
      <TraceRequestDetailMetrics
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
