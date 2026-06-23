import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  HeartPulse,
  RefreshCw,
  Route,
  Stethoscope,
  Waypoints,
} from 'lucide-react'
import { LIFE_COPY } from '../../../shared/content/terminology.js'
import { getLoadStateMeta } from '../../../models/loadState.js'
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

function GateCard({ label, state, detail }) {
  const ready = state === 'ready'
  return (
    <div className={`rounded-lg border px-3 py-3 ${ready ? 'border-emerald-200 bg-emerald-50/70' : 'border-orange-200 bg-orange-50/70'}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{label}</div>
        {ready ? (
          <CheckCircle2 size={14} className="text-emerald-600" />
        ) : (
          <AlertTriangle size={14} className="text-orange-600" />
        )}
      </div>
      <div className={`mt-1 text-xs leading-5 ${ready ? 'text-emerald-700' : 'text-orange-700'}`}>
        {detail}
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
  dashboardBusy,
  error,
  hasRuntimeSnapshot,
  lifeBodyModel,
  onRefreshDashboard,
  overviewLoadState,
  refreshWarning,
  runtimeError,
}) {
  const guidanceActions = lifeBodyModel?.diagnosticGuidance?.actions || []
  const loadMeta = getLoadStateMeta(overviewLoadState)
  const pathologies = lifeBodyModel?.pathologies || []
  const events = lifeBodyModel?.timelineEvents || []
  const runtimeIssue = runtimeError || (!hasRuntimeSnapshot ? 'no runtime snapshot' : null)

  const gates = [
    {
      label: '总览聚合',
      state: error ? 'blocked' : 'ready',
      detail: error ? '观测聚合加载失败，页面会显示降级状态。' : loadMeta.description,
    },
    {
      label: '运行快照',
      state: runtimeIssue ? 'blocked' : 'ready',
      detail: runtimeIssue ? '缺少 runtime snapshot，器官排序可信度会下降。' : 'runtime 已接入，可用于判断存在态和执行层。',
    },
    {
      label: '病灶/事件',
      state: pathologies.length || events.length ? 'ready' : 'blocked',
      detail: pathologies.length || events.length ? `${pathologies.length} 个病灶，${events.length} 条事件可追踪。` : '当前窗口没有稳定病灶或事件样本。',
    },
  ]

  return (
    <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Cockpit workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{LIFE_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{LIFE_COPY.commandRailDescription}</p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          disabled={dashboardBusy}
          onClick={onRefreshDashboard}
        >
          <RefreshCw size={14} className={dashboardBusy ? 'animate-spin' : ''} />
          重新同步
        </button>
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

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.75fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{LIFE_COPY.guidanceTitle}</div>
          <div className="grid gap-2">
            {guidanceActions.slice(0, 3).map((item) => (
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
