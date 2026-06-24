import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  GitBranch,
  MessageSquare,
  RadioTower,
  RefreshCw,
  ScrollText,
  Send,
  Users,
  Wifi,
} from 'lucide-react'
import { GATEWAY_COPY } from '../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  trace: GitBranch,
  edgeCommand: Send,
  tribe: Users,
  logs: ScrollText,
}

const FOCUS_ICONS = {
  alert: AlertTriangle,
  edge: Send,
  message: MessageSquare,
  radio: RadioTower,
  refresh: RefreshCw,
  trace: GitBranch,
  wifi: Wifi,
}

const HANDOFF_META = {
  ready: {
    label: 'ready',
    className: 'border-emerald-100 bg-emerald-50/70 text-emerald-800',
    dotClassName: 'bg-emerald-500',
  },
  warning: {
    label: 'review',
    className: 'border-orange-100 bg-orange-50/75 text-orange-800',
    dotClassName: 'bg-orange-500',
  },
  blocked: {
    label: 'blocked',
    className: 'border-red-100 bg-red-50/75 text-red-800',
    dotClassName: 'bg-red-500',
  },
}

function classForState(state) {
  if (state === 'ready') return 'border-emerald-200 bg-emerald-50/70 text-emerald-700'
  if (state === 'blocked') return 'border-red-200 bg-red-50/70 text-red-700'
  return 'border-orange-200 bg-orange-50/70 text-orange-700'
}

function iconForState(state) {
  if (state === 'ready') return CheckCircle2
  return AlertTriangle
}

function GateCard({ label, state, detail }) {
  const Icon = iconForState(state)
  const iconClassName = state === 'ready' ? 'text-emerald-600' : state === 'blocked' ? 'text-red-600' : 'text-orange-600'

  return (
    <div className={`rounded-lg border px-3 py-3 ${classForState(state)}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{label}</div>
        <Icon size={14} className={iconClassName} />
      </div>
      <div className="mt-1 text-xs leading-5">{detail}</div>
    </div>
  )
}

function HandoffStep({ step, index }) {
  const meta = HANDOFF_META[step.state] || HANDOFF_META.warning

  return (
    <div className={`h-full rounded-lg border px-3 py-3 ${meta.className}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <span className={`h-2 w-2 rounded-full ${meta.dotClassName}`} />
            <span>{index + 1}. {step.label}</span>
          </div>
          <div className="mt-1 break-all text-sm font-bold text-gray-950">{step.value}</div>
        </div>
        <span className="shrink-0 rounded-full border border-current/20 bg-white/70 px-2 py-0.5 text-[11px] font-semibold">
          {meta.label}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 opacity-85">{step.detail}</div>
    </div>
  )
}

function GatewayRoutingHandoff({ steps = [] }) {
  if (!steps.length) return null
  const readyCount = steps.filter((step) => step.state === 'ready').length

  return (
    <section aria-label="Gateway Routing Handoff" className="mt-4 rounded-xl border border-gray-100 bg-white/88 p-3 shadow-sm">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 text-sm font-bold text-gray-900">
            <GitBranch size={15} className="text-primary" />
            {GATEWAY_COPY.handoffTitle}
          </div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">{GATEWAY_COPY.handoffDescription}</div>
        </div>
        <span className="w-fit rounded border border-cyan-100 bg-cyan-50 px-2 py-1 text-xs font-semibold text-cyan-700">
          {readyCount}/{steps.length} ready
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {steps.map((step, index) => (
          <HandoffStep key={step.key} step={step} index={index} />
        ))}
      </div>
    </section>
  )
}

function FocusAction({ action, onFocusPlatforms, onFocusWeChat, onRefresh }) {
  const Icon = FOCUS_ICONS[action.icon] || RadioTower
  const body = (
    <div className="flex items-start gap-3">
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
        <Icon size={16} />
      </span>
      <div className="min-w-0 flex-1 text-left">
        <div className="text-sm font-semibold text-gray-900">{action.title}</div>
        <div className="mt-1 text-xs leading-5 text-gray-500">{action.detail}</div>
        <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
          {action.label}
          <ArrowRight size={12} />
        </div>
      </div>
    </div>
  )
  const className = 'group rounded-lg border border-white/80 bg-white/82 px-3 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50'

  if (action.to) {
    return <Link to={action.to} className={className}>{body}</Link>
  }

  const handlers = {
    platforms: onFocusPlatforms,
    refresh: onRefresh,
    wechat: onFocusWeChat,
  }

  return (
    <button type="button" className={className} onClick={handlers[action.action]}>
      {body}
    </button>
  )
}

function QuickLink({ item }) {
  const Icon = QUICK_LINK_ICONS[item.key] || RadioTower
  return (
    <Link
      to={item.to}
      className="rounded-lg border border-gray-200 bg-white px-3 py-3 transition hover:border-primary-200 hover:bg-primary-50/40"
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
        <Icon size={15} className="text-primary" />
        {item.label}
      </div>
      <div className="mt-1 text-xs leading-5 text-gray-500">{item.detail}</div>
    </Link>
  )
}

export default function GatewayCommandRail({
  busy,
  onFocusPlatforms,
  onFocusWeChat,
  onRefresh,
  view,
}) {
  if (!view) return null
  const stateClassName = classForState(view.state)

  return (
    <section aria-label="Gateway Routing Gate" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Execution workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{GATEWAY_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{GATEWAY_COPY.commandRailDescription}</p>
        </div>
        <button
          type="button"
          className={`rounded-lg border px-3 py-2 text-xs font-medium ${stateClassName}`}
          onClick={onRefresh}
          disabled={busy}
        >
          <RefreshCw size={13} className={`mr-1 inline ${busy ? 'animate-spin' : ''}`} />
          {view.statusLabel}
        </button>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.9fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{GATEWAY_COPY.focusTitle}</div>
          <div className="grid gap-2">
            <FocusAction
              action={view.focusAction}
              onFocusPlatforms={onFocusPlatforms}
              onFocusWeChat={onFocusWeChat}
              onRefresh={onRefresh}
            />
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{GATEWAY_COPY.gateTitle}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {view.gateCards.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{GATEWAY_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {GATEWAY_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>

      <GatewayRoutingHandoff steps={view.handoffSteps} />
    </section>
  )
}
