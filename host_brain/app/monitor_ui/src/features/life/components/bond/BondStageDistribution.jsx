import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { buildBondStageDistribution } from '../../bondModel.js'

export default function BondStageDistribution({ items }) {
  const distribution = buildBondStageDistribution(items)

  if (!distribution.items.length || distribution.total <= 0) {
    return (
      <EmptyStateCard
        title="暂无阶段分布"
        hint="等待关系样本累积后再展示。"
        className="p-6"
      />
    )
  }

  return (
    <div className="space-y-2">
      {distribution.items.map((item) => (
        <div key={item.stage} className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-700">{item.label}</span>
            <span className="text-gray-400">{item.count} · {item.percentLabel}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full"
              style={{
                width: item.width,
                backgroundColor: item.color,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
