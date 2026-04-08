import React from 'react'
import EmptyStateCard from '../EmptyStateCard.jsx'

export default function InsightChartCard({
  title,
  hasData = true,
  emptyIcon: EmptyIcon,
  emptyText = '暂无数据',
  emptyIconClassName = 'text-gray-300',
  children,
}) {
  return (
    <div className="card p-4">
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
