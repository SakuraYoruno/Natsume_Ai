import React from 'react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

export default function InsightChartCard({
  title,
  hasData = true,
  emptyIcon: EmptyIcon,
  emptyText = '暂无数据',
  emptyIconClassName = 'text-gray-300',
  children,
}) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/75 p-4 shadow-sm">
      {hasData ? (
        children
      ) : (
        <EmptyStateCard
          title={title || '暂无数据'}
          hint={emptyText}
          icon={EmptyIcon}
          iconClassName={emptyIconClassName}
          className="h-48 flex flex-col items-center justify-center"
        />
      )}
    </div>
  )
}
