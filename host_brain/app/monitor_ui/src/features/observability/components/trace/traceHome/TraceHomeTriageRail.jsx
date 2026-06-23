import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart2,
  CheckCircle2,
  GitBranch,
  ListChecks,
  MessageSquare,
  Radio,
  ShieldAlert,
} from 'lucide-react'
import { TRACE_COPY } from '../../../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  full: GitBranch,
  sessions: MessageSquare,
  metrics: BarChart2,
  safety: ShieldAlert,
}

function GateCard({ label, state = 'ready', detail }) {
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

function buildFocusActions({
  latestRequest,
  liveTraceItems,
  selectedIssueCount,
  selectedRequestId,
  selectedTraceStageMeta,
}) {
  const actions = []
  const selectedDetailPath = selectedRequestId ? `/observability/trace/${selectedRequestId}` : null

  if (selectedRequestId && selectedIssueCount > 0) {
    actions.push({
      icon: AlertTriangle,
      title: '先定位异常节点',
      detail: `当前请求有 ${selectedIssueCount} 个异常或降级节点，优先进入详情核对输入、输出和护栏记录。`,
      to: selectedDetailPath,
      label: '打开请求详情',
    })
  }

  if (selectedRequestId) {
    actions.push({
      icon: GitBranch,
      title: '查看当前请求路径',
      detail: `已选中 ${selectedRequestId}，当前阶段是 ${selectedTraceStageMeta?.currentStageLabel || '未识别'}。`,
      to: '/observability/trace/full',
      label: '打开完整链路图',
    })
  }

  if (liveTraceItems.length > 0) {
    actions.push({
      icon: Radio,
      title: '沿实时流继续排查',
      detail: `${liveTraceItems.length} 条活跃请求正在进入 Trace，看最新样本是否集中在同一阶段。`,
      to: '/observability/trace/full',
      label: '查看实时链路',
    })
  }

  if (!actions.length && latestRequest?.request_id) {
    actions.push({
      icon: ListChecks,
      title: '从最近请求开始',
      detail: `最近可用样本是 ${latestRequest.request_id}，适合先打开详情确认 trace 是否落盘。`,
      to: `/observability/trace/${latestRequest.request_id}`,
      label: '打开最近请求',
    })
  }

  if (!actions.length) {
    actions.push({
      icon: MessageSquare,
      title: '先确认会话样本',
      detail: '当前没有活跃 Trace 或选中请求，先从 Sessions 回看是否有 request_id 可追踪。',
      to: '/observability/sessions',
      label: '查看会话样本',
    })
  }

  return actions.slice(0, 3)
}

export default function TraceHomeTriageRail({
  currentTraceHealthy,
  latestRequest,
  liveFeedConnected,
  liveFeedStatusText,
  liveTraceError,
  liveTraceItems,
  pollingEnabled,
  recentLoading,
  recentRequests,
  selectedIssueCount,
  selectedRequestId,
  selectedTraceStageMeta,
  traceInfo,
}) {
  const focusActions = buildFocusActions({
    latestRequest,
    liveTraceItems,
    selectedIssueCount,
    selectedRequestId,
    selectedTraceStageMeta,
  })
  const hasSamples = liveTraceItems.length > 0 || recentRequests.length > 0
  const hasSelection = Boolean(selectedRequestId)
  const liveGateState = liveTraceError ? 'blocked' : (liveFeedConnected || liveTraceItems.length > 0 ? 'ready' : 'warning')
  const sampleGateState = hasSamples ? 'ready' : (recentLoading ? 'warning' : 'blocked')
  const selectionGateState = hasSelection ? 'ready' : 'warning'
  const healthGateState = !hasSelection ? 'warning' : (currentTraceHealthy ? 'ready' : 'blocked')

  const gates = [
    {
      label: '实时流',
      state: liveGateState,
      detail: liveTraceError
        ? '实时流加载失败，当前只能依赖历史样本。'
        : liveFeedConnected
          ? 'live trace 已连接，会随请求自动更新。'
          : `${liveFeedStatusText || '使用轮询快照'}，可继续排查但不是实时推送。`,
    },
    {
      label: '样本覆盖',
      state: sampleGateState,
      detail: hasSamples
        ? `活跃 ${liveTraceItems.length} 条，历史 ${recentRequests.length} 条。`
        : (recentLoading ? '正在拉取最近请求样本。' : '当前窗口没有可展示请求。'),
    },
    {
      label: '当前请求',
      state: selectionGateState,
      detail: hasSelection
        ? `${selectedRequestId}，节点 ${traceInfo?.nodeCount ?? 0} 个。`
        : '尚未选中 request_id，下方实时流或历史列表可选。'
    },
    {
      label: '链路健康',
      state: healthGateState,
      detail: !hasSelection
        ? '选中请求后才能判断节点健康。'
        : currentTraceHealthy
          ? '当前请求没有异常或降级节点。'
          : `${selectedIssueCount} 个节点需要关注。`,
    },
  ]

  return (
    <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Trace workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{TRACE_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{TRACE_COPY.commandRailDescription}</p>
        </div>
        <div className={`rounded-lg border px-3 py-2 text-xs font-medium ${pollingEnabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 bg-gray-50 text-gray-600'}`}>
          {pollingEnabled ? '自动同步开启' : '自动同步暂停'}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.85fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRACE_COPY.focusTitle}</div>
          <div className="grid gap-2">
            {focusActions.map((item) => (
              <FocusAction key={item.title} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRACE_COPY.gateTitle}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {gates.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{TRACE_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {TRACE_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
