import React from 'react'
import {
  BIO_OPS_STATUS_CLASSES,
  BIO_OPS_STATUS_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../../../shared/statusMeta.js'

export function classNames(...items) {
  return items.filter(Boolean).join(' ')
}

export function StatusPill({ status }) {
  const normalized = String(status || 'UNKNOWN').toUpperCase()
  return (
    <span className={classNames(
      'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold',
      resolveStatusClass(BIO_OPS_STATUS_CLASSES, normalized, BIO_OPS_STATUS_CLASSES.UNKNOWN),
    )}>
      {resolveStatusLabel(BIO_OPS_STATUS_LABELS, normalized, normalized)}
    </span>
  )
}

export function IconTile({ icon: Icon, tone = 'blue' }) {
  const tones = {
    blue: 'border-blue-100 bg-blue-50 text-blue-600',
    green: 'border-green-100 bg-green-50 text-green-600',
    amber: 'border-amber-100 bg-amber-50 text-amber-600',
    red: 'border-red-100 bg-red-50 text-red-600',
    indigo: 'border-indigo-100 bg-indigo-50 text-indigo-600',
    slate: 'border-slate-100 bg-slate-50 text-slate-600',
  }
  return (
    <span className={classNames('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border', tones[tone] || tones.blue)}>
      <Icon size={18} />
    </span>
  )
}

export function MetricTile({ icon, tone, label, value, hint, status }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <IconTile icon={icon} tone={tone} />
        {status ? <StatusPill status={status} /> : null}
      </div>
      <div className="mt-3 text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-bold text-gray-900">{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div> : null}
    </div>
  )
}

export function Section({ title, subtitle, children, aside = null }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-sm text-gray-500">{subtitle}</p> : null}
        </div>
        {aside}
      </div>
      {children}
    </section>
  )
}

export function EvidenceChips({ items }) {
  const values = Array.isArray(items) ? items.filter(Boolean) : []
  if (!values.length) return null
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {values.map((item) => (
        <span key={item} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
          {item}
        </span>
      ))}
    </div>
  )
}

export function BioOpsSkeletonGrid({ count = 4 }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="h-36 animate-pulse rounded-xl border border-white/80 bg-white/70 shadow-sm" />
      ))}
    </div>
  )
}
