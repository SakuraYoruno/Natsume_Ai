import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  GitBranch,
  Radio,
  RadioTower,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
} from 'lucide-react'
import { EDGE_COMMAND_COPY } from '../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  gateway: Radio,
  agentLoop: Bot,
  tools: Wrench,
  trace: GitBranch,
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

function buildFocusActions({
  controlGate,
  error,
  onEnableGate,
  onFocusPayload,
  payloadValidation,
  status,
}) {
  if (error) {
    return [{
      icon: AlertTriangle,
      title: '先恢复状态接口',
      detail: 'Host 无法读取 Edge Command 状态，push 入口保持关闭。',
      to: '/execution/gateway',
      label: '检查 Gateway',
    }]
  }

  if (!status?.enabled) {
    return [{
      icon: RadioTower,
      title: 'Edge pusher 未启用',
      detail: '下行链路当前不可发送命令，先确认 Edge base URL、通道和 host 配置。',
      to: '/execution/gateway',
      label: '查看通道健康',
    }]
  }

  if (!controlGate) {
    return [{
      icon: ShieldCheck,
      title: '显式打开控制门',
      detail: '状态可用，但发送仍被本页控制门挡住。打开后才允许执行 push。',
      onClick: onEnableGate,
      label: '打开控制门',
    }]
  }

  if (!payloadValidation.valid) {
    return [{
      icon: SlidersHorizontal,
      title: '修正 payload JSON',
      detail: payloadValidation.error || 'payload 无法解析，当前不会发送到 Edge。',
      onClick: onFocusPayload,
      label: '定位 payload',
    }]
  }

  return [{
    icon: CheckCircle2,
    title: '可以执行 gated push',
    detail: '四个发送条件都已满足。提交前再核对 command_type、target_agent 和 payload。',
    onClick: onFocusPayload,
    label: '复核命令表单',
  }]
}

export default function EdgeCommandGateRail({
  controlGate,
  error,
  loading,
  onEnableGate,
  onFocusPayload,
  payloadValidation,
  status,
}) {
  const focusActions = buildFocusActions({
    controlGate,
    error,
    onEnableGate,
    onFocusPayload,
    payloadValidation,
    status,
  })
  const gates = [
    {
      label: '状态接口',
      state: error ? 'blocked' : (loading ? 'warning' : 'ready'),
      detail: error ? 'Host 状态接口不可用。' : (loading ? '正在读取 Edge 状态。' : 'Host 已返回 Edge Command 状态。'),
    },
    {
      label: 'Edge pusher',
      state: status?.enabled ? 'ready' : 'blocked',
      detail: status?.enabled ? '下行通道已启用。' : 'Edge pusher 未启用，禁止发送。',
    },
    {
      label: '控制门',
      state: controlGate ? 'ready' : 'warning',
      detail: controlGate ? '操作者已显式打开控制门。' : '控制门关闭，push 按钮保持禁用。',
    },
    {
      label: 'Payload',
      state: payloadValidation.valid ? 'ready' : 'blocked',
      detail: payloadValidation.valid ? 'payload JSON 可解析。' : payloadValidation.error,
    },
  ]

  return (
    <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-card backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Execution workflow</div>
          <h2 className="mt-1 text-base font-semibold text-gray-900">{EDGE_COMMAND_COPY.commandRailTitle}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{EDGE_COMMAND_COPY.commandRailDescription}</p>
        </div>
        <div className={`rounded-lg border px-3 py-2 text-xs font-medium ${controlGate ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-orange-200 bg-orange-50 text-orange-700'}`}>
          {controlGate ? '控制门已打开' : '控制门关闭'}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.9fr)_minmax(280px,0.75fr)]">
        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{EDGE_COMMAND_COPY.focusTitle}</div>
          <div className="grid gap-2">
            {focusActions.map((item) => (
              <FocusAction key={item.title} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{EDGE_COMMAND_COPY.gateTitle}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {gates.map((item) => (
              <GateCard key={item.label} {...item} />
            ))}
          </div>
        </div>

        <div>
          <div className="mb-2 text-xs font-semibold text-gray-500">{EDGE_COMMAND_COPY.quickLinksTitle}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {EDGE_COMMAND_COPY.quickLinks.map((item) => (
              <QuickLink key={item.key} item={item} />
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
