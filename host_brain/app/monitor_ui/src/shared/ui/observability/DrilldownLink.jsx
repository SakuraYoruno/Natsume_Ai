import React from 'react'
import { Link } from 'react-router-dom'

export default function DrilldownLink({ to, label = '查看详情', hint, className = '' }) {
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 ${className}`.trim()}>
      {hint ? <div className="text-xs text-gray-500">{hint}</div> : <span />}
      <Link
        to={to}
        className="inline-flex min-h-7 items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
      >
        {label}
      </Link>
    </div>
  )
}
