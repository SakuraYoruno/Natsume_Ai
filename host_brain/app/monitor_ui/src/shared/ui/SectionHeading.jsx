import React from 'react'

export default function SectionHeading({
  icon: Icon,
  title,
  tone = 'text-gray-700',
  className = 'mb-3',
}) {
  return (
    <div className={`flex items-center gap-2 ${className}`.trim()}>
      {Icon ? <Icon size={16} className={tone} /> : null}
      <h2 className={`text-sm font-semibold ${tone}`}>{title}</h2>
    </div>
  )
}
