import { Activity } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import StatusBadge from '../../../../../shared/ui/StatusBadge.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { formatDateTime, normalizeStatus } from '../TraceRequestDetailModel.jsx'

export default function LiveEntriesPanel({ liveEntries, liveConnected, liveLastTs }) {
  return (
    <SectionCard
      icon={Activity}
      title="实时节点变化"
      description="优先展示 SSE 增量补报，帮助你快速确认当前 request 正在推进到哪一层。"
      aside={(
        <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${liveConnected ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
          {liveConnected ? 'SSE 已连接' : '重连中 / 轮询补齐'}
        </span>
      )}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="增量条目" value={liveEntries.length} />
        <MiniStat label="连接状态" value={liveConnected ? '实时流在线' : '实时流重连中'} hint={liveLastTs ? formatDateTime(liveLastTs) : '等待心跳'} />
        <MiniStat label="最新变更" value={liveEntries[0]?.displayName || '-'} hint={liveEntries[0]?.label || '暂无变化'} />
      </div>

      {liveEntries.length > 0 ? (
        <div className="space-y-2">
          {liveEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{entry.displayName}</div>
                <StatusBadge status={normalizeStatus(entry.status)} />
                <span className="text-xs text-gray-400">{entry.label}</span>
                {entry.previousStatus ? <span className="text-xs text-gray-400">{entry.previousStatus} → {entry.status}</span> : null}
                <div className="ml-auto text-xs text-gray-400">{formatDateTime(entry.tsMs)}</div>
              </div>
              <div className="mt-1 break-all font-mono text-xs text-gray-500">{entry.nodeId}</div>
              {entry.preview ? <div className="mt-2 break-all text-sm text-gray-700">{entry.preview}</div> : null}
            </div>
          ))}
        </div>
      ) : <EmptyStateCard title="等待节点变化" hint="实时链路就绪后会自动追加节点事件。" className="p-4" />}
    </SectionCard>
  )
}
