import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  Edit3,
  GitBranch,
  Loader2,
  MessageSquare,
  Radio,
  RefreshCw,
  Send,
  Square,
  Target,
  Workflow,
} from 'lucide-react'

const STATUS_META = {
  idle: {
    label: '待输入',
    className: 'border-slate-200 bg-slate-50/75',
    badgeClassName: 'border-slate-200 bg-slate-100 text-slate-600',
  },
  ready: {
    label: '可执行',
    className: 'border-emerald-200 bg-emerald-50/70',
    badgeClassName: 'border-emerald-200 bg-emerald-100 text-emerald-700',
  },
  streaming: {
    label: '流式运行',
    className: 'border-blue-200 bg-blue-50/70',
    badgeClassName: 'border-blue-200 bg-blue-100 text-blue-700',
  },
  attention: {
    label: '需要关注',
    className: 'border-orange-200 bg-orange-50/70',
    badgeClassName: 'border-orange-200 bg-orange-100 text-orange-700',
  },
  blocked: {
    label: '请求失败',
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
  input: MessageSquare,
  events: Radio,
  trace: GitBranch,
  latest: Target,
}

const CARD_TONES = {
  ready: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  degraded: 'border-orange-200 bg-orange-50/70 text-orange-700',
  blocked: 'border-red-200 bg-red-50/70 text-red-700',
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

function ActionButton({
  action,
  primary = false,
  streaming = false,
  message = '',
  onSend,
  onStop,
  onUseSample,
  onRefreshTrace,
}) {
  if (!action) return null

  const disabled = action.kind === 'send' && (!String(message || '').trim() || streaming)
  const className = `${primary ? 'btn-primary' : 'btn-secondary'} justify-center`

  if (action.kind === 'openTrace' && action.requestId) {
    return (
      <Link to={`/observability/trace/${action.requestId}`} className={className}>
        <GitBranch size={14} />
        {action.label}
      </Link>
    )
  }

  const icon = (() => {
    if (action.kind === 'send') return <Send size={14} />
    if (action.kind === 'stop') return <Square size={14} />
    if (action.kind === 'sample') return <Edit3 size={14} />
    if (action.kind === 'refreshTrace') return <RefreshCw size={14} className={streaming ? 'animate-spin' : ''} />
    if (action.kind === 'noop') return <Loader2 size={14} />
    return <Target size={14} />
  })()

  return (
    <button
      type="button"
      className={className}
      disabled={disabled || action.kind === 'noop'}
      onClick={() => {
        if (action.kind === 'send') onSend?.()
        if (action.kind === 'stop') onStop?.()
        if (action.kind === 'sample') onUseSample?.()
        if (action.kind === 'refreshTrace') onRefreshTrace?.()
      }}
    >
      {icon}
      {action.label}
    </button>
  )
}

function CommandCard({ item }) {
  const Icon = CARD_ICONS[item.key] || Target
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

function GateCard({ item }) {
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

function HandoffStep({ step, index }) {
  const meta = HANDOFF_META[step.state] || HANDOFF_META.warning
  const value = String(step.value || '').trim()
  const content = (
    <div className={`h-full rounded-lg border px-3 py-3 ${meta.className}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold">
            <span className={`h-2 w-2 rounded-full ${meta.dotClassName}`} />
            <span>{index + 1}. {step.label}</span>
          </div>
          <div className="mt-1 truncate text-sm font-bold text-gray-950">{value}</div>
        </div>
        <span className="shrink-0 rounded-full border border-current/20 bg-white/70 px-2 py-0.5 text-[11px] font-semibold">
          {meta.label}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 opacity-85">{step.detail}</div>
    </div>
  )

  if (step.key === 'detail' && step.requestId) {
    return (
      <Link to={`/observability/trace/${step.requestId}`} className="block min-w-[13rem] focus:outline-none focus:ring-2 focus:ring-primary-200">
        {content}
      </Link>
    )
  }

  return <div className="min-w-[13rem]">{content}</div>
}

function DebugTraceHandoff({ steps = [] }) {
  if (!steps.length) return null

  return (
    <section aria-label="Debug Trace Handoff" className="mt-4 rounded-xl border border-gray-100 bg-white/88 p-3 shadow-sm">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 text-sm font-bold text-gray-900">
            <Workflow size={15} className="text-primary" />
            Debug Trace Handoff
          </div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">从输入、SSE、request_id 到 Trace 快照和详情页的交付链。</div>
        </div>
        <span className="w-fit rounded border border-cyan-100 bg-cyan-50 px-2 py-1 text-xs font-semibold text-cyan-700">
          {steps.filter((step) => step.state === 'ready').length}/{steps.length} ready
        </span>
      </div>
      <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
        {steps.map((step, index) => (
          <HandoffStep key={step.key} step={step} index={index} />
        ))}
      </div>
    </section>
  )
}

export default function ChatDebugCommandRail({
  model,
  streaming = false,
  message = '',
  onSend,
  onStop,
  onUseSample,
  onRefreshTrace,
}) {
  if (!model) return null
  const statusMeta = STATUS_META[model.status] || STATUS_META.idle
  const cards = model.cards || []
  const gates = model.gates || []
  const handoffSteps = model.handoffSteps || []

  return (
    <section
      aria-label="Chat Debug Command Rail"
      className="mt-3 rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{model.eyebrow || 'Chat Debug Command'}</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{model.title || '聊天调试控制台'}</h2>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.12fr)_minmax(320px,0.88fr)]">
        <div className={`self-start rounded-lg border p-4 ${statusMeta.className}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-primary/75">
                  <Target size={13} />
                  Next debug move
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
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[180px]">
              <ActionButton action={model.primaryAction} primary streaming={streaming} message={message} onSend={onSend} onStop={onStop} onUseSample={onUseSample} onRefreshTrace={onRefreshTrace} />
              <ActionButton action={model.secondaryAction} streaming={streaming} message={message} onSend={onSend} onStop={onStop} onUseSample={onUseSample} onRefreshTrace={onRefreshTrace} />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {cards.map((item) => (
            <CommandCard key={item.key} item={item} />
          ))}
        </div>
      </div>

      {gates.length ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {gates.map((item) => (
            <GateCard key={item.label} item={item} />
          ))}
        </div>
      ) : null}

      <DebugTraceHandoff steps={handoffSteps} />
    </section>
  )
}
