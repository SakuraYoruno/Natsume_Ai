import { ArrowLeft, RefreshCw } from 'lucide-react'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import { normalizeStatus } from './TraceRequestDetailModel.jsx'

export default function TraceRequestDetailHeader({
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
