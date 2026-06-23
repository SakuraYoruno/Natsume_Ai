import { TraceMetricCard as MetricCard } from './TraceSurfaces.jsx'
import { formatDateTime } from './TraceRequestDetailModel.jsx'

export default function TraceRequestDetailMetrics({
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
