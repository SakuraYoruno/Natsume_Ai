import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Compass,
  Filter,
  Gauge,
  RefreshCw,
  Route,
  ShieldCheck,
  Stethoscope,
  Target,
  Waypoints,
} from 'lucide-react'

const STATUS_META = {
  healthy: {
    label: '可继续诊断',
    className: 'border-emerald-200 bg-emerald-50/65',
    badgeClassName: 'border-emerald-200 bg-emerald-100 text-emerald-700',
  },
  attention: {
    label: '需要处理',
    className: 'border-orange-200 bg-orange-50/65',
    badgeClassName: 'border-orange-200 bg-orange-100 text-orange-700',
  },
  degraded: {
    label: '结果降级',
    className: 'border-blue-200 bg-blue-50/65',
    badgeClassName: 'border-blue-200 bg-blue-100 text-blue-700',
  },
  empty: {
    label: '等待样本',
    className: 'border-slate-200 bg-slate-50/70',
    badgeClassName: 'border-slate-200 bg-slate-100 text-slate-600',
  },
  blocked: {
    label: '先恢复链路',
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

const METRIC_ICONS = {
  trust: ShieldCheck,
  filtered: Filter,
  types: Waypoints,
  organs: Activity,
  latest: Gauge,
  high: AlertTriangle,
  warning: Gauge,
  pathology: Stethoscope,
}

const METRIC_TONE_CLASSES = {
  ready: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  degraded: 'border-orange-200 bg-orange-50/70 text-orange-700',
  blocked: 'border-red-200 bg-red-50/70 text-red-700',
}

function MetricCard({ item }) {
  const Icon = METRIC_ICONS[item.key] || Activity
  const toneClass = METRIC_TONE_CLASSES[item.tone] || METRIC_TONE_CLASSES.ready

  return (
    <div className="rounded-lg border border-white/80 bg-white/86 px-3 py-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
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

function ActionLink({ action, primary }) {
  if (!action?.to) return null
  const Icon = primary ? Target : Route

  return (
    <Link to={action.to} className={`${primary ? 'btn-primary' : 'btn-secondary'} justify-center`}>
      <Icon size={14} />
      {action.label}
    </Link>
  )
}

export default function LifeSystemsCommandRail({
  ariaLabel,
  model,
  onRefresh,
  refreshing = false,
}) {
  const railModel = model || {}
  const statusMeta = STATUS_META[railModel.status] || STATUS_META.degraded
  const metricCards = railModel.metricCards || []
  const gates = railModel.gates || []

  return (
    <section
      aria-label={ariaLabel || railModel.ariaLabel || 'Life Systems Command Rail'}
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{railModel.eyebrow || 'Life Systems workflow'}</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{railModel.title || '下一步看什么'}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{railModel.description}</p>
        </div>
        {onRefresh ? (
          <button type="button" className="btn-secondary" disabled={refreshing} onClick={onRefresh}>
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        ) : null}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.85fr)]">
        <div className={`self-start rounded-lg border p-4 ${statusMeta.className}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-primary/75">
                  <Compass size={13} />
                  Mission brief
                </span>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusMeta.badgeClassName}`}>
                  {statusMeta.label}
                </span>
              </div>
              <h3 className="mt-2 text-lg font-semibold leading-7 text-gray-950">{railModel.headline}</h3>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-600">{railModel.summary}</p>
              {railModel.primaryAction?.detail ? (
                <div className="mt-2 text-xs leading-5 text-gray-500">主路径：{railModel.primaryAction.detail}</div>
              ) : null}
            </div>
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[180px]">
              <ActionLink action={railModel.primaryAction} primary />
              <ActionLink action={railModel.secondaryAction} />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {metricCards.map((item) => (
            <MetricCard key={item.key} item={item} />
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

      {railModel.footerHint ? (
        <div className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary">
          {railModel.footerHint}
          <ArrowRight size={12} />
        </div>
      ) : null}
    </section>
  )
}
