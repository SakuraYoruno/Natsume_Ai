import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

export default function ImmuneGateReasonCodesPanel({ reasonCodes }) {
  return (
    <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">原因热区</div>
      {reasonCodes.length > 0 ? (
        <div className="mt-3 space-y-2">
          {reasonCodes.slice(0, 6).map((item) => (
            <div key={item.reason_code} className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-orange-600">{item.reason_code}</span>
                <span className="text-gray-500">{item.count} ({item.pct}%)</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-orange-400 to-amber-400"
                  style={{ width: `${Math.min(Number(item.pct ?? 0), 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyStateCard title="暂无原因热区数据" hint="等待免疫事件积累后展示。" className="mt-3 p-4" />
      )}
    </div>
  )
}
