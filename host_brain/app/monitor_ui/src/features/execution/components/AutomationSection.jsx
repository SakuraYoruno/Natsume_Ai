import React from 'react'

export default function AutomationSection({ icon: Icon, title, description, actions = null, children }) {
  return (
    <section className="card space-y-4 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {Icon ? (
            <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2.5 text-primary">
              <Icon size={16} />
            </div>
          ) : null}
          <div>
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            {description ? <p className="mt-1 text-xs text-gray-500">{description}</p> : null}
          </div>
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
