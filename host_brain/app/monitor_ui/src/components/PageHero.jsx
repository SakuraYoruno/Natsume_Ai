import React from 'react'

export default function PageHero({
  icon: Icon,
  eyebrow,
  title,
  description,
  gradientClass = 'from-primary-50 via-white to-indigo-50',
  metaItems = [],
  aside = null,
  showPulseRail = true,
}) {
  return (
    <div className={`relative overflow-hidden rounded-3xl border border-white/70 bg-gradient-to-r ${gradientClass} p-5 shadow-card backdrop-blur-sm`}>
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -right-10 -top-8 h-36 w-36 rounded-full bg-white/50 blur-2xl" />
        <div className="absolute bottom-0 left-1/3 h-28 w-28 rounded-full bg-primary-100/40 blur-2xl" />
        <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-primary-200/70 to-transparent" />
      </div>
      <div className="relative flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          {eyebrow ? (
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100/80 bg-white/80 px-3 py-1 text-xs font-semibold text-primary shadow-sm">
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
              <span key={item} className="rounded-full border border-white/80 bg-white/75 px-3 py-1 shadow-sm">
                {item}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {showPulseRail ? (
      <div className="relative mt-4 hidden h-8 items-center overflow-hidden rounded-xl border border-white/75 bg-white/70 px-3 backdrop-blur-sm sm:flex">
        <div className="lifeline-wave absolute inset-0 opacity-65" />
        <div className="relative flex w-full items-center justify-between">
          <span className="lifeline-node" style={{ animationDelay: '0s' }} />
          <span className="lifeline-node" style={{ animationDelay: '0.4s' }} />
          <span className="lifeline-node" style={{ animationDelay: '0.8s' }} />
          <span className="lifeline-node" style={{ animationDelay: '1.2s' }} />
          <span className="lifeline-node" style={{ animationDelay: '1.6s' }} />
        </div>
      </div>
      ) : null}
    </div>
  )
}
