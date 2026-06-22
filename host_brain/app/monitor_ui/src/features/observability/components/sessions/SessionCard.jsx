import { ExternalLink } from 'lucide-react'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'

export default function SessionCard({ item, view, onOpen }) {
  return (
    <button
      className={`group w-full rounded-2xl border ${view.tone.border} ${view.tone.bg} p-4 text-left shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-md`}
      onClick={() => onOpen(item.request_id)}
    >
      <div className="flex items-start gap-3">
        <span className={`mt-1 h-3.5 w-3.5 shrink-0 rounded-full ${view.tone.ring} ring-4 ring-white/70`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-gray-900">{view.preview}</div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                <span className="font-mono">{view.requestLabel}</span>
                <span>{view.sessionLabel}</span>
                <span>{view.timeLabel}</span>
              </div>
            </div>
            <StatusBadge status={view.normalizedStatus} label={view.statusLabel} />
          </div>

          <div className="mt-3 text-sm leading-6 text-gray-700">{view.preview}</div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className={`inline-flex items-center rounded-full border px-2.5 py-1 font-medium ${view.tone.badge}`}>
              神经链路回放
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              耗时 {view.latencyLabel}
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              {view.reasonLabel}
            </span>
          </div>
        </div>
        <ExternalLink size={14} className="mt-1 text-gray-300 transition-colors group-hover:text-gray-500" />
      </div>
    </button>
  )
}
