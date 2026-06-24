import React, { useMemo } from 'react'
import {
  AlertTriangle,
  Bug,
  CheckCircle2,
  FileCheck2,
  GitBranch,
  LockKeyhole,
  ShieldCheck,
  Target,
} from 'lucide-react'

const TONE_CLASS = {
  green: 'border-emerald-200 bg-emerald-50/75 text-emerald-800',
  blue: 'border-cyan-200 bg-cyan-50/75 text-cyan-800',
  orange: 'border-orange-200 bg-orange-50/75 text-orange-800',
  red: 'border-red-200 bg-red-50/75 text-red-800',
  rose: 'border-rose-200 bg-rose-50/75 text-rose-800',
  gray: 'border-slate-200 bg-slate-50/75 text-slate-700',
}

const STEP_ICON = {
  target: Target,
  scope: ShieldCheck,
  phase: GitBranch,
  approval: LockKeyhole,
  evidence: Bug,
  report: FileCheck2,
}

function toneClass(tone) {
  return TONE_CLASS[tone] || TONE_CLASS.gray
}

function compact(value, fallback = '-') {
  const text = String(value ?? '').trim()
  if (!text) return fallback
  return text.length > 86 ? `${text.slice(0, 83)}...` : text
}

function firstNode(nodes, predicate) {
  return nodes.find(predicate) || null
}

function evidenceRefTotal(nodes) {
  return nodes.reduce((total, node) => total + Number(node?.evidenceCount || 0), 0)
}

function buildEvidenceFlow(graph, mode) {
  const nodes = graph?.nodes || []
  const edges = graph?.edges || []
  const blockedNodes = nodes.filter((node) => node.status === 'blocked')
  const gatedNodes = nodes.filter((node) => node.status === 'gated')
  const findingNodes = nodes.filter((node) => node.type === 'finding' || node.status === 'finding')
  const phaseNodes = nodes.filter((node) => node.type === 'phase')
  const targetNode = firstNode(nodes, (node) => node.type === 'target')
  const scopeNode = firstNode(nodes, (node) => node.kind === 'scope_guard' || String(node.id || '').includes('scope'))
  const approvalNode = firstNode(nodes, (node) => node.kind === 'approval_gate' || String(node.id || '').includes('approval'))
  const reportNode = firstNode(nodes, (node) => node.type === 'report' || String(node.kind || '').includes('report'))
  const primaryBlocker = firstNode(nodes, (node) => node.status === 'blocked')
  const primaryGate = approvalNode?.status === 'gated' || approvalNode?.status === 'blocked' ? approvalNode : gatedNodes[0]
  const evidenceRefs = evidenceRefTotal(findingNodes)
  const hasReport = Boolean(reportNode)
  const isRuntime = mode === 'runtime'

  let tone = 'green'
  let headline = isRuntime ? '攻击路径证据链可读' : '只读攻击路径可创建'
  let detail = isRuntime
    ? 'Target、ScopeGuard、Phase、Finding 与 Report 已串成可复核链路。'
    : '当前配置会按授权 scope 创建只读采证任务，并把结果写入报告和审计链。'
  let action = isRuntime ? '复核 Timeline、Findings 和 Reports。' : '确认 target/scope 后创建 readonly run。'

  if (primaryBlocker) {
    tone = 'red'
    headline = `先解除 ${primaryBlocker.label || '阻断节点'} 阻断`
    detail = compact(primaryBlocker.meta, '攻击路径存在阻断，需要先处理 scope、lab mode 或 approval 条件。')
    action = isRuntime ? '跳到 Approval Rail 或 Timeline 查阻断原因。' : '回到 Run Wizard 修正配置。'
  } else if (primaryGate) {
    tone = 'orange'
    headline = '攻击路径停在审批门'
    detail = compact(primaryGate.meta, 'Phase4/5 或 side-effect step 正在等待显式 approval。')
    action = isRuntime ? '处理 Approval Rail 后再继续 side-effect step。' : '确认 Lab Mode 与 approval policy 后再创建。'
  } else if (findingNodes.length > 0) {
    tone = 'rose'
    headline = '证据已从发现流入报告'
    detail = `${findingNodes.length} 条 finding 与 ${evidenceRefs} 条 evidence refs 已连接到 report artifact。`
    action = '先复核 High+ finding，再检查 markdown/json report。'
  }

  return {
    tone,
    headline,
    detail,
    action,
    stats: [
      { key: 'nodes', label: 'Nodes', value: nodes.length },
      { key: 'edges', label: 'Edges', value: edges.length },
      { key: 'findings', label: 'Findings', value: findingNodes.length },
      { key: 'refs', label: 'Evidence refs', value: evidenceRefs },
    ],
    steps: [
      {
        key: 'target',
        label: 'Target',
        value: targetNode?.label || 'target pending',
        detail: compact(targetNode?.meta, '等待目标解析'),
        tone: targetNode?.status === 'blocked' ? 'red' : targetNode ? 'blue' : 'gray',
      },
      {
        key: 'scope',
        label: 'ScopeGuard',
        value: scopeNode?.status === 'blocked' ? 'blocked' : scopeNode?.label || 'scope pending',
        detail: compact(scopeNode?.meta, '授权范围尚未命中'),
        tone: scopeNode?.status === 'blocked' ? 'red' : scopeNode ? 'green' : 'gray',
      },
      {
        key: 'phase',
        label: 'Phase path',
        value: `${phaseNodes.length} phases`,
        detail: compact(phaseNodes.map((node) => node.label).join(' / '), '等待 phase 选择'),
        tone: phaseNodes.some((node) => node.status === 'blocked') ? 'red' : phaseNodes.some((node) => node.status === 'gated') ? 'orange' : 'blue',
      },
      {
        key: 'approval',
        label: 'Approval gate',
        value: approvalNode ? approvalNode.status : 'not required',
        detail: compact(approvalNode?.meta, 'readonly 路径不需要 side-effect approval'),
        tone: approvalNode?.status === 'blocked' ? 'red' : approvalNode?.status === 'gated' ? 'orange' : approvalNode ? 'green' : 'gray',
      },
      {
        key: 'evidence',
        label: 'Evidence chain',
        value: `${findingNodes.length} findings · ${evidenceRefs} refs`,
        detail: findingNodes[0]?.label || '等待 finding 节点进入证据链',
        tone: findingNodes.length > 0 ? 'rose' : 'gray',
      },
      {
        key: 'report',
        label: 'Report artifact',
        value: reportNode?.label || 'report pending',
        detail: compact(reportNode?.meta, 'markdown/json report + audit refs'),
        tone: hasReport ? 'green' : 'gray',
      },
    ],
    controls: [
      {
        key: 'authorization',
        label: 'Authorization',
        value: scopeNode?.status === 'blocked' ? 'blocked' : 'ready',
        detail: scopeNode?.status === 'blocked' ? '目标未命中 scope 或 scope 缺失。' : 'ScopeGuard 已成为攻击路径入口控制点。',
        tone: scopeNode?.status === 'blocked' ? 'red' : 'green',
      },
      {
        key: 'side-effect',
        label: 'Side-effect gate',
        value: approvalNode ? approvalNode.status : 'not required',
        detail: approvalNode ? compact(approvalNode.meta, '等待 approval rail') : '只读阶段不会触发高风险执行。',
        tone: approvalNode?.status === 'blocked' ? 'red' : approvalNode?.status === 'gated' ? 'orange' : 'green',
      },
      {
        key: 'delivery',
        label: 'Evidence contract',
        value: hasReport ? 'linked' : 'pending',
        detail: hasReport ? '报告节点承接 findings、artifact refs 和 audit ledger。' : '等待 report artifact 回传。',
        tone: hasReport ? 'green' : 'gray',
      },
    ],
  }
}

function FlowStep({ step, index }) {
  const Icon = STEP_ICON[step.key] || CheckCircle2
  return (
    <article className={`min-w-0 rounded-lg border px-3 py-3 ${toneClass(step.tone)}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs font-semibold">
          <Icon size={14} className="shrink-0" />
          <span className="truncate">{step.label}</span>
        </span>
        <span className="shrink-0 rounded-full border border-current/20 bg-white/70 px-2 py-0.5 text-[11px] font-bold">
          {index + 1}
        </span>
      </div>
      <div className="mt-2 truncate text-sm font-bold text-gray-950">{step.value}</div>
      <div className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 opacity-85">{step.detail}</div>
    </article>
  )
}

function ControlCard({ item }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${toneClass(item.tone)}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold">{item.label}</span>
        <span className="shrink-0 rounded border border-current/20 bg-white/70 px-1.5 py-0.5 text-[11px] font-bold">
          {item.value}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 opacity-85">{item.detail}</div>
    </div>
  )
}

export default function AttackGraphEvidenceFlow({
  graph,
  mode = 'planned',
  ariaLabel = 'Attack Evidence Flow',
  className = '',
}) {
  const flow = useMemo(() => buildEvidenceFlow(graph, mode), [graph, mode])

  return (
    <section
      aria-label={ariaLabel}
      className={`rounded-xl border border-slate-200 bg-gradient-to-br from-white via-slate-50 to-cyan-50/50 p-3 shadow-sm ${className}`}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 text-sm font-bold text-gray-900">
            <GitBranch size={15} className="text-primary" />
            Attack Evidence Flow
          </div>
          <div className={`mt-2 rounded-lg border px-3 py-2 ${toneClass(flow.tone)}`}>
            <div className="flex items-start gap-2">
              {flow.tone === 'red' ? <AlertTriangle size={15} className="mt-0.5 shrink-0" /> : <CheckCircle2 size={15} className="mt-0.5 shrink-0" />}
              <div className="min-w-0">
                <div className="text-sm font-bold text-gray-950">{flow.headline}</div>
                <div className="mt-1 text-xs leading-5">{flow.detail}</div>
                <div className="mt-1 text-xs font-semibold text-gray-800">下一步：{flow.action}</div>
              </div>
            </div>
          </div>
        </div>
        <div className="grid w-full shrink-0 grid-cols-2 gap-2 sm:grid-cols-4 lg:w-[28rem]">
          {flow.stats.map((item) => (
            <div key={item.key} className="rounded-lg border border-white/80 bg-white/86 px-3 py-2 shadow-sm">
              <div className="truncate text-[11px] font-semibold uppercase text-gray-400">{item.label}</div>
              <div className="mt-1 text-xl font-bold leading-none text-gray-950">{item.value}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-6">
        {flow.steps.map((step, index) => (
          <FlowStep key={step.key} step={step} index={index} />
        ))}
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 lg:grid-cols-3">
        {flow.controls.map((item) => (
          <ControlCard key={item.key} item={item} />
        ))}
      </div>
    </section>
  )
}
