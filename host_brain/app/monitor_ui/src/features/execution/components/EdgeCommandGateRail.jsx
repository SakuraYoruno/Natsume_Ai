import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  FileJson,
  GitBranch,
  Radio,
  RadioTower,
  ShieldCheck,
  Wrench,
} from 'lucide-react'
import { EDGE_COMMAND_COPY } from '../../../shared/content/terminology.js'

const QUICK_LINK_ICONS = {
  gateway: Radio,
  tools: Wrench,
  trace: GitBranch,
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

function buildHandoffSteps({
  controlGate,
  formValidation,
  payloadValidation,
  pushing,
  pushResult,
  status,
}) {
  const linkReady = Boolean(status?.enabled)
  const fieldMissing = formValidation?.missing?.join(', ') || 'command_type / target_agent'

  return [
    {
      key: 'link',
      label: 'Link Status',
      value: status?.enabled ? 'enabled' : 'disabled',
      detail: linkReady
        ? `受治理执行链 ${status?.edge_base_url || '未配置'} 已就绪。`
        : 'Edge transport 未启用，禁止发送。',
      state: linkReady ? 'ready' : 'blocked',
    },
    {
      key: 'control',
      label: 'Control Authorization',
      value: controlGate ? 'operator confirmed' : 'locked',
      detail: controlGate ? '操作者已显式打开本页控制门。' : '控制门关闭时不会调用 push 接口。',
      state: controlGate ? 'ready' : 'warning',
    },
    {
      key: 'envelope',
      label: 'Command Envelope',
      value: formValidation?.valid ? formValidation.label : `${formValidation?.missing?.length || 3} missing`,
      detail: formValidation?.valid
        ? 'command_type、target_agent 均已填写。'
        : `缺少 ${fieldMissing}，发送前必须补齐。`,
      state: formValidation?.valid ? 'ready' : 'blocked',
    },
    {
      key: 'payload',
      label: 'Payload',
      value: payloadValidation?.valid ? (payloadValidation.label || 'object') : 'invalid json',
      detail: payloadValidation?.valid ? 'payload JSON 是对象，可随命令 envelope 一起发送。' : payloadValidation?.error,
      state: payloadValidation?.valid ? 'ready' : 'blocked',
    },
    {
      key: 'result',
      label: 'Dispatch Result',
      value: pushResult?.ok
        ? (pushResult.data?.command_id || pushResult.data?.request_id || 'accepted')
        : pushResult
          ? 'failed'
          : pushing
            ? 'pushing'
            : linkReady && controlGate && formValidation?.valid && payloadValidation?.valid
              ? 'ready to send'
              : 'locked',
      detail: pushResult?.ok
        ? '最近一次 push 已返回可追踪标识。'
        : pushResult
          ? '最近一次 push 失败，保留错误便于复核。'
          : linkReady && controlGate && formValidation?.valid && payloadValidation?.valid
            ? '发送按钮已开放，尚未写入 Edge。'
            : '前置条件未满足时保持只读状态。',
      state: pushResult?.ok ? 'ready' : pushResult ? 'blocked' : linkReady && controlGate && formValidation?.valid && payloadValidation?.valid ? 'warning' : 'blocked',
    },
  ]
}

function EdgeDispatchHandoff({ steps = [] }) {
  if (!steps.length) return null
  const readyCount = steps.filter((step) => step.state === 'ready').length

  return (
    <section aria-label="Edge Dispatch Handoff" className="mt-4 rounded-xl border border-gray-100 bg-white/88 p-3 shadow-sm">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-2 text-sm font-bold text-gray-900">
            <GitBranch size={15} className="text-primary" />
            Edge Dispatch Handoff
          </div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">把链路、授权、命令 envelope、payload 和最近 push 结果连成一条可复核发送链路。</div>
        </div>
        <span className="w-fit rounded border border-sky-100 bg-sky-50 px-2 py-1 text-xs font-semibold text-sky-700">
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
  formValidation,
  onEnableGate,
  onFocusCommand,
  onFocusPayload,
  payloadValidation,
  status,
}) {
  if (!status?.enabled) {
    return [{
      icon: RadioTower,
      title: 'Edge transport 未启用',
      detail: '下行传输当前不可用，先确认 Edge base URL、通道和 Host 配置。',
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

  if (!formValidation?.valid) {
    return [{
      icon: ClipboardCheck,
      title: '补齐命令字段',
      detail: formValidation?.error || 'command_type、target_agent 都需要明确填写。',
      onClick: onFocusCommand,
      label: '定位命令字段',
    }]
  }

  if (!payloadValidation.valid) {
    return [{
      icon: FileJson,
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
  formValidation,
  onEnableGate,
  onFocusCommand,
  onFocusPayload,
  payloadValidation,
  pushing,
  pushResult,
  status,
}) {
  const focusActions = buildFocusActions({
    controlGate,
    formValidation,
    onEnableGate,
    onFocusCommand,
    onFocusPayload,
    payloadValidation,
    status,
  })
  const handoffSteps = buildHandoffSteps({
    controlGate,
    formValidation,
    payloadValidation,
    pushing,
    pushResult,
    status,
  })
  const gates = [
    {
      label: '执行链路',
      state: status?.enabled ? 'ready' : 'blocked',
      detail: status?.enabled ? '受治理执行链已就绪。' : 'Edge transport 未启用，禁止发送。',
    },
    {
      label: '执行 authority',
      state: status?.enabled ? 'ready' : 'blocked',
      detail: status?.enabled
        ? '受治理下行通道已启用。'
        : 'Edge transport 未启用，禁止发送。',
    },
    {
      label: '控制门',
      state: controlGate ? 'ready' : 'warning',
      detail: controlGate ? '操作者已显式打开控制门。' : '控制门关闭，push 按钮保持禁用。',
    },
    {
      label: '命令字段',
      state: formValidation?.valid ? 'ready' : 'blocked',
      detail: formValidation?.valid ? 'command_type、target_agent 已填写。' : formValidation?.error,
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

      <EdgeDispatchHandoff steps={handoffSteps} />
    </section>
  )
}
