import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Gamepad2,
  GitBranch,
  LayoutDashboard,
  MessageSquare,
  Radio,
  RefreshCw,
  Send,
  Type,
} from 'lucide-react'
import { INTEGRATIONS_COPY } from '../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  cockpit: LayoutDashboard,
  edgeCommand: Send,
  gateway: Radio,
  chatDebug: MessageSquare,
}

const CARD_TONES = {
  ready: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  degraded: 'border-orange-200 bg-orange-50/70 text-orange-700',
  blocked: 'border-red-200 bg-red-50/70 text-red-700',
}

const FOCUS_TONES = {
  ready: 'border-emerald-200 bg-emerald-50/70',
  syncing: 'border-blue-200 bg-blue-50/70',
  degraded: 'border-orange-200 bg-orange-50/70',
  blocked: 'border-red-200 bg-red-50/70',
}

const HANDOFF_META = {
  ready: {
    className: 'border-emerald-200 bg-emerald-50/70 text-emerald-800',
    dotClassName: 'bg-emerald-500',
    label: 'OK',
  },
  warning: {
    className: 'border-orange-200 bg-orange-50/70 text-orange-800',
    dotClassName: 'bg-orange-500',
    label: 'Watch',
  },
  blocked: {
    className: 'border-red-200 bg-red-50/70 text-red-800',
    dotClassName: 'bg-red-500',
    label: 'Locked',
  },
}

function GateCard({ item }) {
  const ready = item.state === 'ready'
  const blocked = item.state === 'blocked'
  const className = ready
    ? 'border-emerald-200 bg-emerald-50/70 text-emerald-700'
    : blocked
      ? 'border-red-200 bg-red-50/70 text-red-700'
      : 'border-orange-200 bg-orange-50/70 text-orange-700'

  return (
    <div className={`rounded-lg border px-3 py-3 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{item.label}</div>
        {ready ? (
          <CheckCircle2 size={14} className="text-emerald-600" />
        ) : (
          <AlertTriangle size={14} className={blocked ? 'text-red-600' : 'text-orange-600'} />
        )}
      </div>
      <div className="mt-1 text-xs leading-5">{item.detail}</div>
    </div>
  )
}

function SummaryCard({ item }) {
  const toneClass = CARD_TONES[item.tone] || CARD_TONES.degraded
  return (
    <div className="rounded-lg border border-white/80 bg-white/86 px-3 py-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-500">{item.label}</div>
          <div className="mt-1 truncate text-lg font-semibold text-gray-950">{item.value}</div>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${toneClass}`}>
          {item.tone === 'blocked' ? 'Block' : item.tone === 'degraded' ? 'Watch' : 'OK'}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{item.detail}</div>
    </div>
  )
}

function FocusAction({ model, loading, onFocusConsole, onFocusText, onRefresh }) {
  const focus = model.focus || {}
  const Icon = focus.kind === 'focusText'
    ? Type
    : focus.kind === 'focusConsole'
      ? Gamepad2
      : focus.kind === 'refresh'
        ? RefreshCw
        : GitBranch
  const disabled = focus.kind === 'noop'

  const handleClick = () => {
    if (focus.kind === 'refresh') onRefresh?.()
    if (focus.kind === 'focusConsole') onFocusConsole?.()
    if (focus.kind === 'focusText') onFocusText?.()
  }

  return (
    <button
      type="button"
      className="group w-full rounded-lg border border-white/80 bg-white/82 px-3 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50 disabled:cursor-not-allowed disabled:opacity-60"
      disabled={disabled}
      onClick={handleClick}
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
          <Icon size={16} className={loading && focus.kind === 'refresh' ? 'animate-spin' : ''} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900">{focus.title}</div>
          <div className="mt-1 text-xs leading-5 text-gray-500">{focus.detail}</div>
          <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
            {focus.label}
            <ArrowRight size={12} />
          </div>
        </div>
      </div>
    </button>
  )
}

function QuickLink({ item }) {
  const Icon = QUICK_LINK_ICONS[item.key] || GitBranch
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

function BodyDispatchHandoff({ steps = [] }) {
  if (!steps.length) return null

  return (
    <section aria-label="Body Dispatch Handoff" className="mt-4 rounded-xl border border-gray-100 bg-white/88 p-3 shadow-sm">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 text-sm font-bold text-gray-900">
            <GitBranch size={15} className="text-primary" />
            Body Dispatch Handoff
          </div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">把桥接健康、能力目录、当前意图和最近一次写入结果连成可复核链路。</div>
        </div>
        <span className="w-fit rounded border border-cyan-100 bg-cyan-50 px-2 py-1 text-xs font-semibold text-cyan-700">
          {steps.filter((step) => step.state === 'ready').length}/{steps.length} ready
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {steps.map((step, index) => (
          <HandoffStep key={step.key} step={step} index={index} />
        ))}
      </div>
    </section>
  )
}

export default function BodyIntegrationCommandRail({
  loading = false,
  model,
  onFocusConsole,
  onFocusText,
  onRefresh,
}) {
  if (!model) return null
  const ready = model.status === 'ready'
  const focusToneClass = FOCUS_TONES[model.focus?.tone] || FOCUS_TONES.degraded

  return (
    <section
      aria-label="Body Bridge Gate"
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Integrations workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{INTEGRATIONS_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{INTEGRATIONS_COPY.commandRailDescription}</p>
        </div>
        <div className={`rounded-lg border px-3 py-2 text-xs font-medium ${ready ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-orange-200 bg-orange-50 text-orange-700'}`}>
          {ready ? '身体桥可写入' : '身体桥需确认'}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.85fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{INTEGRATIONS_COPY.focusTitle}</div>
          <div className={`rounded-lg border p-4 ${focusToneClass}`}>
            <div className="text-lg font-semibold leading-7 text-gray-950">{model.headline}</div>
            <p className="mt-1 text-sm leading-6 text-gray-600">{model.description}</p>
            <div className="mt-3">
              <FocusAction
                model={model}
                loading={loading}
                onFocusConsole={onFocusConsole}
                onFocusText={onFocusText}
                onRefresh={onRefresh}
              />
            </div>
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{INTEGRATIONS_COPY.gateTitle}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {model.gates.map((item) => (
              <GateCard key={item.label} item={item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">Bridge summary</div>
          <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-1">
            {model.cards.map((item) => (
              <SummaryCard key={item.label} item={item} />
            ))}
          </div>
        </div>
      </div>

      <BodyDispatchHandoff steps={model.handoffSteps} />

      <div className="mt-4">
        <div className="mb-2 text-xs font-semibold text-gray-500">{INTEGRATIONS_COPY.quickLinksTitle}</div>
        <div className="grid gap-2 md:grid-cols-4">
          {INTEGRATIONS_COPY.quickLinks.map((item) => (
            <QuickLink key={item.key} item={item} />
          ))}
        </div>
      </div>
    </section>
  )
}
