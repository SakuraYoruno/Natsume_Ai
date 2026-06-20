import React from 'react'
import { Inbox } from 'lucide-react'

export default function EmptyStateCard({
  title = '暂无数据',
  hint = '',
  icon: Icon = Inbox,
  iconClassName = 'text-primary/70',
  className = '',
}) {
  return (
    <div className={`card relative overflow-hidden p-8 text-center ${className}`.trim()}>
      <div className="pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-primary-200 to-transparent" />
      <div className="mx-auto mb-2 inline-flex h-10 w-10 items-center justify-center rounded-full border border-white/80 bg-white/80 shadow-sm">
        <Icon size={20} className={iconClassName} />
      </div>
      <div className="text-sm font-semibold text-gray-600">{title}</div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}
