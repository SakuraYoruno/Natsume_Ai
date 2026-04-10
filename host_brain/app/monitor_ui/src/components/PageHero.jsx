import React from 'react'

export default function PageHero({
  icon: Icon,
  eyebrow,
  title,
  description,
  gradientClass = 'from-primary-50 via-white to-indigo-50',
  metaItems = [],
  aside = null,
}) {
  return (
    <div className={`relative overflow-hidden rounded-2xl border border-primary-100 bg-gradient-to-r ${gradientClass} p-5 shadow-sm`}>
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full bg-white/40 blur-2xl" />
        <div className="absolute bottom-0 left-1/3 h-24 w-24 rounded-full bg-primary-100/40 blur-2xl" />
        <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-primary-200/70 to-transparent" />
      </div>
      <div className="relative flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          {eyebrow ? (
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              {Icon ? <Icon size={12} /> : null}
              {eyebrow}
            </div>
          ) : null}
          <div className="mt-3 text-2xl font-bold text-gray-900">{title ?? '—'}</div>
          <div className="mt-1 max-w-3xl text-sm leading-6 text-gray-600">{description ?? '—'}</div>
        </div>

        {aside ? (
          aside
        ) : metaItems.length > 0 ? (
          <div className="flex flex-wrap gap-2 text-xs text-gray-500">
            {metaItems.map((item) => (
              <span key={item} className="rounded-full border border-gray-200 bg-white px-3 py-1">
                {item}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
