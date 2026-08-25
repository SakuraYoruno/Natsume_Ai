import { Link } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  Bell,
  CheckCircle2,
  GitBranch,
  MessageSquare,
  ShieldAlert,
} from 'lucide-react'
import { OBSERVABILITY_COPY } from '../../../../shared/content/terminology.js'

const ROUTE_ICONS = {
  alerts: Bell,
  trace: GitBranch,
  metrics: ShieldAlert,
  sessions: MessageSquare,
}

function toneClass(tone) {
  return {
    green: 'border-emerald-200 bg-emerald-50/75 text-emerald-700',
    blue: 'border-blue-200 bg-blue-50/75 text-blue-700',
    orange: 'border-orange-200 bg-orange-50/75 text-orange-700',
    red: 'border-red-200 bg-red-50/75 text-red-700',
    slate: 'border-slate-200 bg-slate-50/75 text-slate-600',
  }[tone] || 'border-slate-200 bg-slate-50/75 text-slate-600'
}

function HandoffEvidenceCard({ item }) {
  return (
    <div className={`rounded-lg border px-3 py-3 ${toneClass(item.tone)}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide">{item.label}</span>
        <span className="shrink-0 rounded bg-white/70 px-1.5 py-0.5 text-[11px] font-semibold">{item.value}</span>
      </div>
      <div className="mt-2 line-clamp-3 text-xs leading-5">{item.detail}</div>
    </div>
  )
}

function RouteCard({ item }) {
  const Icon = ROUTE_ICONS[item.key] || Activity
  const primary = item.state === 'primary'
  return (
    <Link
      to={item.to}
      className={`group rounded-lg border px-3 py-3 transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50 ${
        primary ? toneClass(item.tone) : 'border-gray-200 bg-white text-gray-700'
      }`}
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-semibold text-gray-900">{item.label}</div>
            {primary ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-white/80 bg-white/80 px-2 py-0.5 text-[11px] font-semibold text-primary">
                <CheckCircle2 size={11} />
                primary
              </span>
            ) : null}
          </div>
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">{item.detail}</div>
          <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
            打开
            <ArrowRight size={12} />
          </div>
        </div>
      </div>
    </Link>
  )
}

export default function ObservabilityEvidenceHandoff({ handoff }) {
  if (!handoff) return null

  return (
    <section
      id="observability-evidence-handoff"
      aria-label="Observability Evidence Handoff"
      className="rounded-xl border border-white/80 bg-white/88 p-4 shadow-sm"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-primary/75">
            <GitBranch size={14} />
            {OBSERVABILITY_COPY.evidenceHandoffKicker}
          </div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{handoff.title}</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-gray-500">{handoff.detail}</p>
        </div>
        <div className="grid min-w-[240px] grid-cols-2 gap-2 text-center text-xs sm:grid-cols-3">
          <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
            <div className="text-base font-bold text-gray-900">{handoff.issueCount}</div>
            <div className="text-gray-500">issues</div>
          </div>
          <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
            <div className="text-base font-bold text-gray-900">{handoff.sampleSize}</div>
            <div className="text-gray-500">samples</div>
          </div>
          <Link
            to={handoff.actionTo}
            className={`inline-flex items-center justify-center gap-1 rounded-lg border px-3 py-2 font-semibold ${toneClass(handoff.tone)}`}
          >
            {handoff.actionLabel}
            <ArrowRight size={12} />
          </Link>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {handoff.evidenceCards.map((item) => (
          <HandoffEvidenceCard key={item.key} item={item} />
        ))}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-4">
        {handoff.routeCards.map((item) => (
          <RouteCard key={item.key} item={item} />
        ))}
      </div>
    </section>
  )
}
