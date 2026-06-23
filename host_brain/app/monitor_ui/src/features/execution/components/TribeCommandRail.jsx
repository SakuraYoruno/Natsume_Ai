import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  GitBranch,
  ListFilter,
  Network,
  ScrollText,
  Users,
  Wrench,
} from 'lucide-react'
import { TRIBE_COPY } from '../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  agentLoop: Bot,
  tools: Wrench,
  trace: GitBranch,
  logs: ScrollText,
}

function GateCard({ label, state, detail }) {
  const ready = state === 'ready'
  const blocked = state === 'blocked'
  const className = ready
    ? 'border-emerald-200 bg-emerald-50/70 text-emerald-700'
    : blocked
      ? 'border-red-200 bg-red-50/70 text-red-700'
      : 'border-orange-200 bg-orange-50/70 text-orange-700'

  return (
    <div className={`rounded-lg border px-3 py-3 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-gray-800">{label}</div>
        {ready ? (
          <CheckCircle2 size={14} className="text-emerald-600" />
        ) : (
          <AlertTriangle size={14} className={blocked ? 'text-red-600' : 'text-orange-600'} />
        )}
      </div>
      <div className="mt-1 text-xs leading-5">{detail}</div>
    </div>
  )
}

function FocusAction({ icon: Icon, title, detail, label, to, onClick }) {
  const body = (
    <div className="flex items-start gap-3">
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white text-primary shadow-sm">
        <Icon size={16} />
      </span>
      <div className="min-w-0 flex-1 text-left">
        <div className="text-sm font-semibold text-gray-900">{title}</div>
        <div className="mt-1 text-xs leading-5 text-gray-500">{detail}</div>
        <div className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary group-hover:text-primary-700">
          {label}
          <ArrowRight size={12} />
        </div>
      </div>
    </div>
  )
  const className = 'group rounded-lg border border-white/80 bg-white/82 px-3 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-primary-200 hover:bg-primary-50/50'

  if (to) {
    return <Link to={to} className={className}>{body}</Link>
  }
  return (
    <button type="button" className={className} onClick={onClick}>
      {body}
    </button>
  )
}

function QuickLink({ item }) {
  const Icon = QUICK_LINK_ICONS[item.key] || Network
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

function buildFocusActions({ errorCount, eventCount, handlerCount, onFocusEvents, onFocusSubscribers }) {
  if (errorCount > 0) {
    return [{
      icon: AlertTriangle,
      title: '先恢复 Tribe 接口',
      detail: `${errorCount} 个 Tribe 数据源降级，优先查看日志或 host internal route。`,
      to: '/observability/logs',
      label: '查看日志',
    }]
  }

  if (eventCount > 0) {
    return [{
      icon: ListFilter,
      title: '沿最近事件排查协作链',
      detail: `${eventCount} 条事件进入当前窗口，可按 event_type、source 或 target 筛选。`,
      onClick: onFocusEvents,
      label: '查看事件流',
    }]
  }

  if (handlerCount === 0) {
    return [{
      icon: Users,
      title: '确认订阅者是否注册',
      detail: '当前没有 handler 样本，先检查 subscribers，再确认事件是否被消费。',
      onClick: onFocusSubscribers,
      label: '查看订阅者',
    }]
  }

  return [{
    icon: Bot,
    title: '从 Agent Loop 复核协作动作',
    detail: '状态和订阅者可用，但当前事件样本较少，适合从执行回路反查。',
    to: '/execution/agent-loop',
    label: '进入 Agent Loop',
  }]
}

export default function TribeCommandRail({
  errorCount,
  eventCount,
  handlerCount,
  loading,
  onFocusEvents,
  onFocusSubscribers,
  summaryReady,
}) {
  const focusActions = buildFocusActions({
    errorCount,
    eventCount,
    handlerCount,
    onFocusEvents,
    onFocusSubscribers,
  })
  const gates = [
    {
      label: 'State',
      state: errorCount > 0 && !summaryReady ? 'blocked' : (loading ? 'warning' : 'ready'),
      detail: summaryReady ? 'state summary 已返回。' : (loading ? '正在同步 state summary。' : 'state summary 暂无数据。'),
    },
    {
      label: 'Event Bus',
      state: eventCount > 0 ? 'ready' : (errorCount > 0 ? 'blocked' : 'warning'),
      detail: eventCount > 0 ? `${eventCount} 条最近事件。` : '当前窗口没有事件样本。',
    },
    {
      label: 'Subscribers',
      state: handlerCount > 0 ? 'ready' : (errorCount > 0 ? 'blocked' : 'warning'),
      detail: handlerCount > 0 ? `${handlerCount} 个 handler 已注册。` : '暂无 handler 样本。',
    },
  ]

  return (
    <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Execution workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{TRIBE_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{TRIBE_COPY.commandRailDescription}</p>
        </div>
        <div className={`rounded-lg border px-3 py-2 text-xs font-medium ${errorCount > 0 ? 'border-orange-200 bg-orange-50 text-orange-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
          {errorCount > 0 ? `${errorCount} 个接口降级` : '协作数据可读'}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.85fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRIBE_COPY.focusTitle}</div>
          <div className="grid gap-2">
            {focusActions.map((item) => (
              <FocusAction key={item.title} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRIBE_COPY.gateTitle}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {gates.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRIBE_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {TRIBE_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
