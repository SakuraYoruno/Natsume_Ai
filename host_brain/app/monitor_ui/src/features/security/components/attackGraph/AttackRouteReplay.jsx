import React, { useMemo } from 'react'
import { AlertTriangle, Bug, Crosshair, FileCheck2, GitBranch, LockKeyhole, Radar, ShieldCheck } from 'lucide-react'

const STEP_TONE_CLASS = {
  green: 'border-green-200 bg-green-50/75 text-green-800',
  orange: 'border-orange-200 bg-orange-50/75 text-orange-800',
  rose: 'border-rose-200 bg-rose-50/75 text-rose-800',
  red: 'border-red-200 bg-red-50/75 text-red-800',
  blue: 'border-blue-200 bg-blue-50/75 text-blue-800',
  gray: 'border-gray-200 bg-gray-50/75 text-gray-700',
}

const NODE_TYPE_LABEL = {
  target: 'Target',
  guard: 'Guard',
  phase: 'Phase',
  finding: 'Finding',
  exploit: 'Exploit',
  session: 'Session',
  report: 'Report',
  evidence: 'Evidence',
}

const NODE_TYPE_ICON = {
  target: Crosshair,
  guard: ShieldCheck,
  phase: Radar,
  finding: Bug,
  exploit: LockKeyhole,
  session: LockKeyhole,
  report: FileCheck2,
  evidence: GitBranch,
}

function compact(value, fallback = '-') {
  const text = String(value ?? '').trim()
  if (!text) return fallback
  return text.length > 82 ? `${text.slice(0, 79)}...` : text
}

function stageTone(node) {
  if (node?.status === 'blocked') return 'red'
  if (node?.status === 'gated') return 'orange'
  if (node?.status === 'finding' || node?.type === 'finding') return 'rose'
  if (node?.status === 'planned') return 'blue'
  if (node?.status === 'pending') return 'gray'
  return 'green'
}

function nodePriority(node) {
  if (node?.type === 'target') return 0
  if (node?.id === 'scope_guard' || String(node?.kind || '').includes('scope')) return 1
  if (node?.type === 'phase') return 2
  if (String(node?.kind || '').includes('approval')) return 4
  if (node?.type === 'finding') return 5
  if (node?.type === 'report') return 6
  return 3
}

function buildAttackRouteSteps(graph, limit) {
  const sourceNodes = graph?.nodes || []
  const sourceEdges = graph?.edges || []
  const incomingRelationByTarget = new Map()
  sourceEdges.forEach((edge) => {
    if (!incomingRelationByTarget.has(edge.target)) incomingRelationByTarget.set(edge.target, edge.relation)
  })

  return sourceNodes
    .map((node, index) => ({
      node,
      index,
      priority: nodePriority(node),
    }))
    .sort((left, right) => left.priority - right.priority || left.index - right.index)
    .slice(0, limit)
    .map(({ node }, index) => {
      const tone = stageTone(node)
      return {
        id: node.id || `${node.label}-${index}`,
        label: node.label || `Step ${index + 1}`,
        type: NODE_TYPE_LABEL[node.type] || node.kind || 'Node',
        status: node.status || 'observed',
        relation: incomingRelationByTarget.get(node.id) || (index === 0 ? 'entry' : 'next'),
        meta: compact(node.meta, node.kind || 'graph node'),
        evidenceCount: Number(node.evidenceCount || 0),
        tone,
        className: STEP_TONE_CLASS[tone] || STEP_TONE_CLASS.gray,
        icon: NODE_TYPE_ICON[node.type] || GitBranch,
      }
    })
}

function RouteStepCard({ step, index }) {
  const Icon = step.icon
  return (
    <article className={`min-w-[13.5rem] rounded-lg border px-3 py-3 shadow-sm ${step.className}`}>
      <div className="flex items-start justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs font-semibold">
          <Icon size={13} className="shrink-0" />
          <span className="truncate">{step.type}</span>
        </span>
        <span className="shrink-0 rounded-full border border-current/20 bg-white/70 px-2 py-0.5 text-[11px] font-bold">
          {index + 1}
        </span>
      </div>
      <div className="mt-2 truncate text-sm font-bold text-gray-950">{step.label}</div>
      <div className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 opacity-85">{step.meta}</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded border border-current/20 bg-white/70 px-1.5 py-0.5 text-[11px] font-semibold">
          {step.status}
        </span>
        <span className="rounded border border-current/20 bg-white/70 px-1.5 py-0.5 text-[11px] font-semibold">
          {step.relation}
        </span>
        {step.evidenceCount > 0 ? (
          <span className="rounded border border-current/20 bg-white/70 px-1.5 py-0.5 text-[11px] font-semibold">
            {step.evidenceCount} refs
          </span>
        ) : null}
      </div>
    </article>
  )
}

export default function AttackRouteReplay({
  graph,
  title = 'Attack Route Replay',
  description = '按可执行顺序扫读 target、scope、phase、approval、finding 与 report。',
  ariaLabel = 'Attack Route Replay',
  stepLimit = 12,
  className = '',
}) {
  const steps = useMemo(() => buildAttackRouteSteps(graph, stepLimit), [graph, stepLimit])
  if (!steps.length) return null

  const blockedCount = steps.filter((step) => step.tone === 'red').length
  const gatedCount = steps.filter((step) => step.tone === 'orange').length
  const findingCount = steps.filter((step) => step.tone === 'rose').length

  return (
    <section aria-label={ariaLabel} className={`rounded-xl border border-gray-100 bg-white/88 p-3 shadow-sm ${className}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="inline-flex items-center gap-1.5 text-sm font-bold text-gray-900">
            <GitBranch size={15} className="text-primary" />
            {title}
          </div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">{description}</div>
        </div>
        <div className="flex w-fit flex-wrap gap-1.5 text-[11px] font-semibold">
          <span className="rounded border border-cyan-100 bg-cyan-50 px-2 py-1 text-cyan-700">{steps.length} steps</span>
          {blockedCount > 0 ? (
            <span className="inline-flex items-center gap-1 rounded border border-red-100 bg-red-50 px-2 py-1 text-red-700">
              <AlertTriangle size={11} />
              {blockedCount} blocked
            </span>
          ) : null}
          {gatedCount > 0 ? (
            <span className="rounded border border-orange-100 bg-orange-50 px-2 py-1 text-orange-700">{gatedCount} gated</span>
          ) : null}
          {findingCount > 0 ? (
            <span className="rounded border border-rose-100 bg-rose-50 px-2 py-1 text-rose-700">{findingCount} finding</span>
          ) : null}
        </div>
      </div>
      <div className="mt-3 flex snap-x gap-2 overflow-x-auto pb-1">
        {steps.map((step, index) => (
          <React.Fragment key={step.id}>
            <RouteStepCard step={step} index={index} />
            {index < steps.length - 1 ? (
              <div className="hidden min-w-5 items-center justify-center text-cyan-500 md:flex" aria-hidden="true">
                <GitBranch size={14} />
              </div>
            ) : null}
          </React.Fragment>
        ))}
      </div>
    </section>
  )
}
