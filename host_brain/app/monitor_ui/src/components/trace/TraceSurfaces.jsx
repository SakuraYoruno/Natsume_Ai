import React from 'react'
import LoadingNotice from '../LoadingNotice.jsx'
import RenderValue from '../../shared/ui/RenderValue.jsx'

export function HomeSummaryCard({ icon: Icon, title, value, caption, toneClassName = 'text-gray-900' }) {
  return (
    <div className="card p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-gray-500">{title}</div>
          <div className={`mt-1 break-all text-sm font-semibold leading-6 ${toneClassName}`}>
            <RenderValue value={value} />
          </div>
          {caption ? <div className="mt-1 break-all text-xs leading-5 text-gray-500">{caption}</div> : null}
        </div>
      </div>
    </div>
  )
}

export function TraceSection({ title, hint, aside = null, children }) {
  return (
    <section className="card relative overflow-hidden p-4 md:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {aside ? <div className="ml-auto">{aside}</div> : null}
        </div>
        {hint ? <p className="mt-1 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </section>
  )
}

export function TraceSectionCard({ icon: Icon, title, description, aside = null, children, className = '' }) {
  return (
    <section className={`card overflow-hidden rounded-2xl ${className}`.trim()}>
      <div className="flex items-start gap-3 border-b border-primary-100/70 px-4 py-4">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white/80 text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900">{title}</div>
          {description ? <div className="mt-1 text-xs leading-5 text-gray-500">{description}</div> : null}
        </div>
        {aside}
      </div>
      <div className="lifeline-rail mx-4 mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

export function TraceMetricCard({ label, value, hint = null, valueClassName = '' }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/82 p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-lg font-semibold text-gray-900 ${valueClassName}`}>
        <RenderValue value={value} empty="-" />
      </div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}

export function TraceMiniStat({ label, value, hint = null, toneClassName = 'text-gray-900' }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/72 p-3">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-sm font-semibold ${toneClassName}`}>
        <RenderValue value={value} empty="-" />
      </div>
      {hint ? <div className="mt-1 text-xs text-gray-500">{hint}</div> : null}
    </div>
  )
}

export function TracePageFallback({ children }) {
  return (
    <div className="card p-4">
      <LoadingNotice>{children}</LoadingNotice>
    </div>
  )
}
