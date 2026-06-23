import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

export default function ImmuneGateRecentEventsPanel({ recent, onReplayLoad }) {
  return (
    <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">近期事件</div>
      {recent.length > 0 ? (
        <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
          {recent.map((item, index) => (
            <button
              key={`${item.request_id}-${item.ts_ms}-${index}`}
              onClick={() => onReplayLoad(item.request_id)}
              className="w-full rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2 text-left text-xs transition hover:border-violet-200 hover:bg-violet-50/60"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="font-mono text-blue-600">{item.request_id || '-'}</div>
                <div className="text-gray-500">{item.decision} / {item.risk_level || 'none'}</div>
              </div>
              <div className="mt-1 text-gray-500">
                {item.event_type} · score={item.score ?? '-'} · {item.reason_codes?.join(', ') || '-'}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <EmptyStateCard title="暂无免疫事件" hint="当前时间窗内没有免疫门禁事件。" className="mt-3 p-4" />
      )}
    </div>
  )
}
