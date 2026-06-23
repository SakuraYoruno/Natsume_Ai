import React, { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import {
  Bug,
  Crosshair,
  FileCheck2,
  GitBranch,
  LockKeyhole,
  Network,
  Radar,
  Server,
  ShieldCheck,
} from 'lucide-react'

const NODE_ICON = {
  target: Crosshair,
  guard: ShieldCheck,
  phase: Radar,
  finding: Bug,
  exploit: LockKeyhole,
  session: Server,
  report: FileCheck2,
  evidence: Network,
}

const STATUS_CLASS = {
  observed: 'border-cyan-300 bg-cyan-50 text-cyan-800',
  planned: 'border-blue-300 bg-blue-50 text-blue-800',
  pending: 'border-gray-300 bg-gray-50 text-gray-700',
  gated: 'border-orange-300 bg-orange-50 text-orange-800',
  blocked: 'border-red-300 bg-red-50 text-red-800',
  finding: 'border-rose-300 bg-rose-50 text-rose-800',
}

function statusClassName(status) {
  return STATUS_CLASS[status] || STATUS_CLASS.observed
}

const AttackGraphNode = memo(({ data, selected }) => {
  const Icon = NODE_ICON[data.type] || GitBranch
  const statusClass = statusClassName(data.status)

  return (
    <div
      className={`relative w-[190px] rounded-lg border-2 bg-white shadow-sm transition ${
        selected ? 'border-primary shadow-md' : 'border-gray-200'
      }`}
      onClick={() => data.onSelect?.(data)}
    >
      <div className={`h-1.5 rounded-t-md ${statusClass}`} />
      <div className="space-y-2 px-3 py-2.5">
        <div className="flex items-start gap-2">
          <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border ${statusClass}`}>
            <Icon size={15} />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-gray-900">{data.label}</span>
            <span className="block truncate text-xs text-gray-500">{data.meta || data.kind}</span>
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <span className={`rounded border px-1.5 py-0.5 text-[11px] font-semibold ${data.riskClassName}`}>
            {data.riskLabel}
          </span>
          <span className={`rounded border px-1.5 py-0.5 text-[11px] font-semibold ${statusClass}`}>
            {data.status}
          </span>
          {(data.badges || []).slice(0, 2).map((badge) => (
            <span key={badge} className="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] text-gray-600">
              {badge}
            </span>
          ))}
        </div>
      </div>
      <Handle type="target" position={Position.Left} style={{ background: '#94A3B8', width: 7, height: 7 }} />
      <Handle type="source" position={Position.Right} style={{ background: '#94A3B8', width: 7, height: 7 }} />
    </div>
  )
})

AttackGraphNode.displayName = 'AttackGraphNode'

export default AttackGraphNode
