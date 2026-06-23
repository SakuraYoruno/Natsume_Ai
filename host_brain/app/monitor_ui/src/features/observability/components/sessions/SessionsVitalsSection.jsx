import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import { DASH } from '../../../../utils/digitalLife.js'
import FlowSection from './FlowSection.jsx'

export default function SessionsVitalsSection({ sessions, summary }) {
  return (
    <FlowSection title="体内会话体征" hint="将会话质量、异常密度和耗时放入同一体征层。">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前页样本" value={sessions.length} hint="用于快速浏览" tone="text-gray-900" />
        <MetricCard label="顺流" value={summary.healthyCount} hint="当前页" tone="text-green-600" />
        <MetricCard label="回撤/降级" value={summary.degradedCount} hint="当前页" tone="text-orange-500" />
        <MetricCard
          label="平均耗时"
          value={summary.avgLatency != null ? `${summary.avgLatency} ms` : DASH}
          hint="当前页样本"
          tone="text-blue-600"
        />
      </div>
    </FlowSection>
  )
}
