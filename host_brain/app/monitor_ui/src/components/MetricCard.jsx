import React from 'react'

export default function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'text-gray-900',
  variant = 'inline',
  className = '',
}) {
  if (variant === 'stacked') {
    return (
      <div className={`card p-4 text-center ${className}`.trim()}>
        {Icon ? <Icon size={16} className="mx-auto mb-1 text-gray-400" /> : null}
        <div className={`text-2xl font-bold ${tone}`}>{value ?? '—'}</div>
        <div className="mt-1 text-xs text-gray-500">{label}</div>
        <div className="mt-0.5 text-xs text-gray-400">{hint ?? '—'}</div>
      </div>
    )
  }

  return (
    <div className={`card p-4 ${className}`.trim()}>
      <div className="flex items-center gap-2 text-xs text-gray-500">
        {Icon ? <Icon size={14} className="text-gray-500" /> : null}
        <span>{label}</span>
      </div>
      <div className={`mt-2 text-2xl font-bold ${tone}`}>{value ?? '—'}</div>
      <div className="mt-1 text-xs text-gray-400">{hint ?? '—'}</div>
    </div>
  )
}
