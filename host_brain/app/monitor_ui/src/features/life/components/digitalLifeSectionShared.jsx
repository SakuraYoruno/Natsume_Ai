import React from 'react'
import { Link } from 'react-router-dom'
import { DIGITAL_LIFE_DOMAIN_STATUS_META } from '../../../shared/statusMeta.js'

export function DomainCard({ icon: Icon, title, desc, status, actionTo, actionLabel }) {
  const statusMeta = DIGITAL_LIFE_DOMAIN_STATUS_META[status] || DIGITAL_LIFE_DOMAIN_STATUS_META.pending

  return (
    <div className="card card-hover p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-white/80 bg-white/80 text-primary shadow-sm">
            <Icon size={18} />
          </span>
          <div>
            <div className="text-sm font-semibold text-gray-800">{title}</div>
            <div className="mt-1 text-xs text-gray-500">{desc}</div>
          </div>
        </div>
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusMeta.className}`}>
          {statusMeta.label}
        </span>
      </div>
      {actionTo ? (
        <Link to={actionTo} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-700">
          {actionLabel || '查看'}
        </Link>
      ) : null}
    </div>
  )
}

export function GrowthTextCard({ title, text, compactText }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/75 p-4 shadow-sm backdrop-blur-sm">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-gray-700">
        {compactText(text, 200)}
      </div>
    </div>
  )
}

export function LifeSection({ title, hint, actionTo, actionLabel, children }) {
  return (
    <section
      className="card relative overflow-hidden p-5"
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="mb-3 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {actionTo ? (
            <Link to={actionTo} className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
              {actionLabel || '查看'}
            </Link>
          ) : null}
        </div>

        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}

        <div className="lifeline-rail mb-4 h-5 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-70" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        {children}
      </div>
    </section>
  )
}
