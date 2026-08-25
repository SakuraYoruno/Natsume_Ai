import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Compass,
  Database,
  Gauge,
  HeartPulse,
  RefreshCw,
  Route,
  ShieldCheck,
  Stethoscope,
  Target,
  Waypoints,
} from 'lucide-react'
import { LIFE_COPY } from '../../../shared/content/terminology.js'
import { LIFE_BODY_TONE_CLASSES, LIFE_BODY_TONE_LABELS } from '../../../shared/statusMeta.js'
import DataState from '../../../shared/ui/DataState.jsx'

const ACTION_ICONS = {
  pathology: Stethoscope,
  organ: HeartPulse,
  timeline: Waypoints,
  recovery: RefreshCw,
}

const QUICK_LINK_ICONS = {
  timeline: Waypoints,
  pathologies: Stethoscope,
  runtime: Activity,
  memory: Database,
}

const METRIC_ICONS = {
  trust: ShieldCheck,
  runtime: Gauge,
  organs: HeartPulse,
  samples: Clock3,
}

const MISSION_STATUS_META = {
  healthy: {
    label: '可直接诊断',
    className: 'border-emerald-200 bg-emerald-50/65',
    badgeClassName: 'border-emerald-200 bg-emerald-100 text-emerald-700',
  },
  attention: {
    label: '需要处理',
    className: 'border-orange-200 bg-orange-50/65',
    badgeClassName: 'border-orange-200 bg-orange-100 text-orange-700',
  },
  degraded: {
    label: '观测降级',
    className: 'border-blue-200 bg-blue-50/65',
    badgeClassName: 'border-blue-200 bg-blue-100 text-blue-700',
  },
  blocked: {
    label: '先恢复链路',
    className: 'border-red-200 bg-red-50/70',
    badgeClassName: 'border-red-200 bg-red-100 text-red-700',
  },
}

const GATE_STATE_META = {
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

const METRIC_TONE_CLASSES = {
  ready: 'border-emerald-200 bg-emerald-50/70 text-emerald-700',
  degraded: 'border-orange-200 bg-orange-50/70 text-orange-700',
  blocked: 'border-red-200 bg-red-50/70 text-red-700',
}

const DECISION_TONE_CLASSES = {
  green: 'border-emerald-100 bg-emerald-50/80 text-emerald-800',
  orange: 'border-orange-100 bg-orange-50/80 text-orange-800',
  red: 'border-red-100 bg-red-50/80 text-red-800',
  blue: 'border-blue-100 bg-blue-50/80 text-blue-800',
  gray: 'border-gray-100 bg-gray-50/80 text-gray-700',
}

const DECISION_ICONS = {
  next: Target,
  evidence: Stethoscope,
  trust: ShieldCheck,
  fallback: Route,
}

function decisionToneClass(tone) {
  return DECISION_TONE_CLASSES[tone] || DECISION_TONE_CLASSES.gray
}

function GateCard({ label, state, detail }) {
  const meta = GATE_STATE_META[state] || GATE_STATE_META.warning
  const Icon = meta.icon

  return (
    <div className={`rounded-lg border px-3 py-3 ${meta.className}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{label}</div>
        <Icon size={14} className={meta.iconClassName} />
      </div>
      <div className={`mt-1 text-xs leading-5 ${meta.textClassName}`}>
        {detail}
      </div>
    </div>
  )
}

function DecisionCard({ item }) {
  const Icon = DECISION_ICONS[item.key] || Compass
  const content = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs font-semibold">
          <Icon size={14} className="shrink-0" />
          <span className="truncate">{item.label}</span>
        </span>
        <span className="shrink-0 rounded border border-white/75 bg-white/75 px-1.5 py-0.5 text-[11px] font-semibold">
          {item.tone}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 text-sm font-bold leading-5 text-gray-950">{item.title}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5">{item.detail}</div>
      <div className="mt-3 inline-flex items-center gap-1 text-xs font-semibold">
        {item.cta || '打开'}
        <ArrowRight size={12} />
      </div>
    </>
  )

  if (item.to) {
    return (
      <Link
        to={item.to}
        className={`group rounded-lg border px-3 py-3 transition hover:-translate-y-0.5 hover:shadow-sm ${decisionToneClass(item.tone)}`}
      >
        {content}
      </Link>
    )
  }

  return (
    <div className={`rounded-lg border px-3 py-3 ${decisionToneClass(item.tone)}`}>
      {content}
    </div>
  )
}

function DecisionReadout({ readout }) {
  const cards = readout?.cards || []
  if (!cards.length) return null

  return (
    <div role="region" aria-label="Cockpit Decision Readout" className="mt-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-600">
          <Compass size={14} className="text-primary" />
          决策读数
        </div>
        <span className="rounded-full border border-gray-200 bg-white/80 px-2 py-0.5 text-[11px] font-semibold text-gray-500">
          {readout.status || 'unknown'}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((item) => (
          <DecisionCard key={item.key} item={item} />
        ))}
      </div>
    </div>
  )
}

function MetricCard({ item }) {
  const Icon = METRIC_ICONS[item.key] || Activity
  const toneClass = METRIC_TONE_CLASSES[item.tone] || METRIC_TONE_CLASSES.degraded

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
          {item.tone === 'ready' ? 'OK' : item.tone === 'blocked' ? 'Block' : 'Watch'}
        </span>
      </div>
      <div className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{item.detail}</div>
    </div>
  )
}

function MissionBrief({ model, dashboardBusy, onRefreshDashboard }) {
  const statusMeta = MISSION_STATUS_META[model.status] || MISSION_STATUS_META.degraded
  const primaryAction = model.primaryAction || { label: '查看 Cockpit', to: '/cockpit', detail: '' }
  const secondaryAction = model.secondaryAction || { label: '查看时间轴', to: '/life/timeline', detail: '' }

  return (
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
          <h3 className="mt-2 text-lg font-semibold leading-7 text-gray-950">{model.headline}</h3>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-600">{model.summary}</p>
          <div className="mt-2 text-xs leading-5 text-gray-500">
            主路径：{primaryAction.detail}
          </div>
        </div>

        <div className="flex w-full flex-col gap-2 sm:w-auto sm:min-w-[180px]">
          <Link to={primaryAction.to} className="btn-primary justify-center">
            <Target size={14} />
            {primaryAction.label}
          </Link>
          <Link to={secondaryAction.to} className="btn-secondary justify-center">
            <Route size={14} />
            {secondaryAction.label}
          </Link>
          <button
            type="button"
            className="btn-secondary justify-center"
            disabled={dashboardBusy}
            onClick={onRefreshDashboard}
          >
            <RefreshCw size={14} className={dashboardBusy ? 'animate-spin' : ''} />
            重新同步
          </button>
        </div>
      </div>
    </div>
  )
}

function GuidanceAction({ item }) {
  const Icon = ACTION_ICONS[item.id] || Route
  const toneClass = LIFE_BODY_TONE_CLASSES[item.tone] || LIFE_BODY_TONE_CLASSES.info
  const toneLabel = LIFE_BODY_TONE_LABELS[item.tone] || LIFE_BODY_TONE_LABELS.info

  return (
    <Link
      to={item.to || '/cockpit'}
      className="group rounded-lg border border-white/80 bg-white/82 px-3 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50"
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <div className="truncate text-sm font-semibold text-gray-900">{item.title}</div>
            <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${toneClass}`}>{toneLabel}</span>
          </div>
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">{item.detail}</div>
          <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
            {item.label || '查看详情'}
            <ArrowRight size={12} />
          </div>
        </div>
      </div>
    </Link>
  )
}

function QuickLink({ item }) {
  const Icon = QUICK_LINK_ICONS[item.key] || Route
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

export default function DigitalLifeCommandRail({
  model,
  dashboardBusy,
  onRefreshDashboard,
  refreshWarning,
}) {
  const railModel = model || {}
  const guidanceActions = railModel.guidanceActions || []
  const gates = railModel.gates || []
  const metricCards = railModel.metricCards || []

  return (
    <section
      aria-label="Cockpit Mission Control"
      id="cockpit-mission-control"
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Cockpit workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{LIFE_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{LIFE_COPY.commandRailDescription}</p>
        </div>
      </div>

      {refreshWarning ? (
        <DataState
          compact
          state="refreshing"
          title="当前展示旧快照"
          hint={refreshWarning.message || String(refreshWarning)}
          className="mt-3"
        />
      ) : null}

      <DecisionReadout readout={railModel.decisionReadout} />

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <MissionBrief
          model={railModel}
          dashboardBusy={dashboardBusy}
          onRefreshDashboard={onRefreshDashboard}
        />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {metricCards.map((item) => (
            <MetricCard key={item.key} item={item} />
          ))}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(300px,0.8fr)_minmax(280px,0.72fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{LIFE_COPY.guidanceTitle}</div>
          <div className="grid gap-2">
            {guidanceActions.map((item) => (
              <GuidanceAction key={`${item.id}-${item.title}`} item={item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{LIFE_COPY.gateTitle}</div>
          <div className="grid gap-2">
            {gates.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{LIFE_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {LIFE_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
