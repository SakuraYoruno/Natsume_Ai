import React from 'react'
import { Inbox } from 'lucide-react'

export default function EmptyStateCard({
  title = '暂无数据',
  hint = '',
  icon: Icon = Inbox,
  iconClassName = 'text-gray-300',
  className = '',
}) {
  return (
    <div className={`card p-8 text-center ${className}`.trim()}>
      <Icon size={24} className={`mx-auto mb-2 ${iconClassName}`.trim()} />
      <div className="text-sm font-medium text-gray-500">{title}</div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}
