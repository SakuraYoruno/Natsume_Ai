import { BellOff } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import AlertCard from './AlertCard.jsx'

export default function AlertsListPanel({ filteredAlerts }) {
  return (
    <div className="space-y-3">
      {filteredAlerts.length > 0 ? (
        filteredAlerts.map((item, index) => (
          <AlertCard key={`${item.reason_code || 'alert'}-${item.ts_ms || index}-${index}`} alert={item} />
        ))
      ) : (
        <EmptyStateCard
          title="当前没有待看的告警"
          hint="系统在当前窗口内保持稳定；如果你刚切换了过滤条件，也可能是该类型暂无命中。"
          icon={BellOff}
        />
      )}
    </div>
  )
}
