import React from 'react'

export default function KeyValueRow({
  label,
  value,
  valueClassName = 'text-gray-800',
  maxValueWidthClassName = 'max-w-[60%]',
  breakClassName = 'break-words',
  className = '',
}) {
  return (
    <div className={`flex items-start justify-between gap-3 rounded-xl border border-white/75 bg-white/70 px-3 py-2 text-sm shadow-sm ${className}`.trim()}>
      <span className="text-gray-500">{label}</span>
      <span className={`${maxValueWidthClassName} ${breakClassName} text-right font-medium ${valueClassName}`.trim()}>
        {value ?? '--'}
      </span>
    </div>
  )
}
