import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Crosshair,
  GitBranch,
  Radio,
  Route,
  ShieldCheck,
  TimerReset,
} from 'lucide-react'

const STATUS_META = {
  healthy: {
    label: '可继续下钻',
    className: 'border-emerald-200 bg-emerald-50/70',
    badgeClassName: 'border-emerald-200 bg-emerald-100 text-emerald-700',
  },
  attention: {
    label: '优先排障',
    className: 'border-orange-200 bg-orange-50/70',
    badgeClassName: 'border-orange-200 bg-orange-100 text-orange-700',
  },
  degraded: {
    label: '证据降级',
    className: 'border-blue-200 bg-blue-50/70',
    badgeClassName: 'border-blue-200 bg-blue-100 text-blue-700',
  },
  running: {
    label: '运行中',
    className: 'border-violet-200 bg-violet-50/70',
    badgeClassName: 'border-violet-200 bg-violet-100 text-violet-700',
  },
  empty: {
    label: '等待节点',
    className: 'border-slate-200 bg-slate-50/80',
    badgeClassName: 'border-slate-200 bg-slate-100 text-slate-600',
  },
  blocked: {
    label: '缺少快照',
    className: 'border-red-200 bg-red-50/70',
    badgeClassName: 'border-red-200 bg-red-100 text-red-700',
  },
}

const GATE_META = {
  ready: {
    icon: CheckCircle2,
    className: 'border-emerald-200 bg-emerald-50/70',
    textClassName: 'text-emerald-700',
    iconClassName: 'text-emerald-600',
  },
  warning: {
    icon: AlertTriangle,
    className: 'border-orange-200 bg-orange-50/70',
    textClassName: 'text-orange-700',
    iconClassName: 'text-orange-600',
  },
  blocked: {
    icon: AlertTriangle,
    className: 'border-red-200 bg-red-50/70',
    textClassName: 'text-red-700',
    iconClassName: 'text-red-600',
  },
}

const CARD_ICONS = {
  status: ShieldCheck,
  nodes: GitBranch,
  evidence: Radio,
  runtime: TimerReset,
}

const CARD_TONES = {
  ready: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  degraded: 'border-orange-200 bg-orange-50/70 text-orange-700',
  blocked: 'border-red-200 bg-red-50/70 text-red-700',
}

function scrollToSection(targetId) {
  if (!targetId || typeof document === 'undefined') return
  document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function TriageAction({ action, primary = false, onSelectNode }) {
  if (!action) return null
  const Icon = action.kind === 'node' ? Crosshair : action.kind === 'route' ? Route : ArrowRight
  const className = `${primary ? 'btn-primary' : 'btn-secondary'} justify-center`

  if (action.kind === 'route') {
    return (
      <Link to={action.to} className={className}>
        <Icon size={14} />
        {action.label}
      </Link>
    )
  }

  return (
    <button
      type="button"
      className={className}
      onClick={() => {
        if (action.kind === 'node' && action.node) onSelectNode?.(action.node)
        scrollToSection(action.targetId)
      }}
    >
      <Icon size={14} />
      {action.label}
    </button>
  )
}

function EvidenceCard({ item }) {
  const Icon = CARD_ICONS[item.key] || Activity
  const toneClass = CARD_TONES[item.tone] || CARD_TONES.ready

  return (
    <div className="rounded-lg border border-white/80 bg-white/86 px-3 py-3 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
            <Icon size={15} />
          </span>
          <div className="min-w-0">
            <div className="truncate text-xs font-semibold text-gray-500">{item.label}</div>
            <div className="truncate text-sm font-semibold text-gray-900">{item.value}</div>
          </div>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${toneClass}`}>
          {item.tone === 'blocked' ? 'Block' : item.tone === 'degraded' ? 'Watch' : 'OK'}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{item.detail}</div>
    </div>
  )
}

function GateItem({ item }) {
  const meta = GATE_META[item.state] || GATE_META.warning
  const Icon = meta.icon

  return (
    <div className={`rounded-lg border px-3 py-3 ${meta.className}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{item.label}</div>
        <Icon size={14} className={meta.iconClassName} />
      </div>
      <div className={`mt-1 text-xs leading-5 ${meta.textClassName}`}>{item.detail}</div>
    </div>
  )
}

export default function TraceRequestTriageRail({ model, onSelectNode }) {
  if (!model) return null
  const statusMeta = STATUS_META[model.status] || STATUS_META.degraded
  const cards = model.cards || []
  const gates = model.gates || []

  return (
    <section
      aria-label="Trace Request Triage Rail"
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{model.eyebrow || 'Trace Triage'}</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{model.title || '请求排障控制台'}</h2>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className={`self-start rounded-lg border p-4 ${statusMeta.className}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-primary/75">
                  <Crosshair size={13} />
                  First move
                </span>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusMeta.badgeClassName}`}>
                  {statusMeta.label}
                </span>
              </div>
              <h3 className="mt-2 text-lg font-semibold leading-7 text-gray-950">{model.headline}</h3>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-600">{model.description}</p>
              {model.primaryAction?.detail ? (
                <div className="mt-2 text-xs leading-5 text-gray-500">建议动作：{model.primaryAction.detail}</div>
              ) : null}
            </div>
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[190px]">
              <TriageAction action={model.primaryAction} primary onSelectNode={onSelectNode} />
              <TriageAction action={model.secondaryAction} onSelectNode={onSelectNode} />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {cards.map((item) => (
            <EvidenceCard key={item.key} item={item} />
          ))}
        </div>
      </div>

      {gates.length ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {gates.map((item) => (
            <GateItem key={item.label} item={item} />
          ))}
        </div>
      ) : null}
    </section>
  )
}
