import React from 'react'
import SectionHeading from '../../../../shared/ui/SectionHeading.jsx'

export default function InsightSection({
  icon,
  title,
  description = '',
  actions = null,
  color = 'text-gray-700',
  children,
  className = '',
}) {
  return (
    <section className={`card space-y-4 p-5 md:p-6 ${className}`.trim()}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <SectionHeading icon={icon} title={title} tone={color} className="mb-0" />
          {description ? <p className="text-xs text-gray-500">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
      {children}
    </section>
  )
}
