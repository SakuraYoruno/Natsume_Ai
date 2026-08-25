import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart2,
  Bell,
  CheckCircle2,
  GitBranch,
  MessageSquare,
  Radio,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { getLoadStateMeta } from '../../../../models/loadState.js'
import { OBSERVABILITY_COPY } from '../../../../shared/content/terminology.js'
import DataState from '../../../../shared/ui/DataState.jsx'

const QUICK_LINK_ICONS = {
  trace: GitBranch,
  metrics: BarChart2,
  alerts: Bell,
  sessions: MessageSquare,
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
      <div className={`mt-1 text-xs leading-5 ${ready ? 'text-emerald-700' : 'text-orange-700'}`}>{detail}</div>
    </div>
  )
}

function FocusAction({ icon: Icon, title, detail, to, label }) {
  return (
    <Link
      to={to}
      className="group rounded-lg border border-white/80 bg-white/82 px-3 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50"
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900">{title}</div>
          <div className="mt-1 text-xs leading-5 text-gray-500">{detail}</div>
          <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
            {label}
            <ArrowRight size={12} />
          </div>
        </div>
      </div>
    </Link>
  )
}

function QuickLink({ item }) {
  const Icon = QUICK_LINK_ICONS[item.key] || Activity
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

function buildFocusActions({ data, activeSubsystems, agentLoopData, wsAlerts }) {
  const actions = []
  const degraded = Number(data?.degraded ?? 0)
  const down = Number(data?.down ?? 0)
  const alertCount = Number(wsAlerts?.length ?? 0)
  const failedLoops = Number(agentLoopData?.failed ?? 0)

  if (alertCount || degraded || down) {
    actions.push({
      icon: Bell,
      title: '先看告警与服务状态',
      detail: `实时告警 ${alertCount} 条，降级 ${degraded}，离线 ${down}。`,
      to: '/observability/alerts',
      label: '进入告警中心',
    })
  }

  if (failedLoops > 0) {
    actions.push({
      icon: ShieldAlert,
      title: '检查执行护栏',
      detail: `失败执行 ${failedLoops}，继续沿 Metrics / Trace 定位。`,
      to: '/observability/metrics',
      label: '查看指标与护栏',
    })
  }

  if (activeSubsystems.length > 0) {
    actions.push({
      icon: GitBranch,
      title: '沿活跃子系统追链路',
      detail: `${activeSubsystems.length} 个子系统在当前窗口有样本，适合进入 Trace 继续定位。`,
      to: '/observability/trace',
      label: '进入链路 Trace',
    })
  }

  if (!actions.length) {
    actions.push({
      icon: Radio,
      title: '先确认观测链路',
      detail: '当前窗口没有明显异常或样本不足，先确认 overview / runtime / sessions 的观测源是否连通。',
      to: '/observability/sessions',
      label: '查看会话样本',
    })
  }

  return actions.slice(0, 3)
}

export default function ObservabilityCommandRail({
  activeSubsystems = [],
  agentLoopData,
  data,
  error,
  loading,
  onRefresh,
  overviewLoadState,
  refreshing,
  sampleMeta,
  wsAlerts = [],
  wsConnected,
}) {
  const loadMeta = getLoadStateMeta(overviewLoadState)
  const sampleSize = Number(sampleMeta?.sampleSize ?? 0)
  const focusActions = buildFocusActions({ data, activeSubsystems, agentLoopData, wsAlerts })
  const busy = Boolean(loading || refreshing)

  const gates = [
    {
      label: '数据聚合',
      state: error ? 'blocked' : 'ready',
      detail: error ? 'Overview 查询失败，先检查 obs backend 或网络代理。' : loadMeta.description,
    },
    {
      label: '实时健康',
      state: wsConnected ? 'ready' : 'blocked',
      detail: wsConnected ? 'health WebSocket 已连接，服务状态会实时刷新。' : 'WebSocket 未连接，当前使用轮询快照。',
    },
    {
      label: '样本覆盖',
      state: sampleSize > 0 ? 'ready' : 'blocked',
      detail: sampleSize > 0 ? `${sampleSize} 个观测对象进入当前窗口。` : '当前窗口缺少服务或子系统样本。',
    },
  ]

  return (
    <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Observability workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{OBSERVABILITY_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{OBSERVABILITY_COPY.commandRailDescription}</p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={onRefresh}
        >
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
          重新同步
        </button>
      </div>

      {error ? (
        <DataState
          compact
          state="error"
          title="观测总览降级"
          hint={error.message || String(error)}
          className="mt-3"
        />
      ) : null}

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.75fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{OBSERVABILITY_COPY.focusTitle}</div>
          <div className="grid gap-2">
            {focusActions.map((item) => (
              <FocusAction key={item.title} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{OBSERVABILITY_COPY.gateTitle}</div>
          <div className="grid gap-2">
            {gates.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{OBSERVABILITY_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {OBSERVABILITY_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
