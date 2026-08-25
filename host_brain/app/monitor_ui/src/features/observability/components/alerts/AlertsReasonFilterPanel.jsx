import { Filter } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { getAlertReasonBreakdownBarWidth } from '../../alertsModel.js'
import { classNames, formatAlertTitle } from '../../../../utils/digitalLife.js'

export default function AlertsReasonFilterPanel({
  reasonBreakdown,
  filterCode,
  allAlertsCount,
  onFilterCodeChange,
}) {
  return (
    <div className="card p-5">
      <div className="flex items-center gap-2">
        <Filter size={15} className="text-gray-500" />
        <h2 className="text-sm font-semibold text-gray-800">触发类型</h2>
      </div>

      {reasonBreakdown.length > 0 ? (
        <div className="mt-4 space-y-2">
          {reasonBreakdown.map(([code, count]) => {
            const active = code === filterCode
            return (
              <button
                key={code}
                type="button"
                className={classNames(
                  'w-full rounded-xl border px-3 py-2 text-left transition-colors',
                  active ? 'border-primary bg-primary-50' : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                )}
                onClick={() => onFilterCodeChange(active ? '' : code)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className={classNames('text-sm font-medium', active ? 'text-primary' : 'text-gray-700')}>
                    {formatAlertTitle(code)}
                  </span>
                  <span className="text-xs text-gray-400">{count}</span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: getAlertReasonBreakdownBarWidth(count, allAlertsCount) }}
                  />
                </div>
                <div className="mt-2 truncate font-mono text-[11px] text-gray-400">{code}</div>
              </button>
            )
          })}
        </div>
      ) : (
        <EmptyStateCard title="暂无类型分布" hint="有告警进入后，这里会显示主要触发类型。" className="mt-4 p-5" />
      )}

      {filterCode ? (
        <button className="btn-secondary mt-4 w-full justify-center" onClick={() => onFilterCodeChange('')}>
          清除过滤：{formatAlertTitle(filterCode)}
        </button>
      ) : null}
    </div>
  )
}
