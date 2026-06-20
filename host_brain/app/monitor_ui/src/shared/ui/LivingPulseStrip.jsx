import React from 'react'

function PulseNode({ label, value, tone = 'primary', active = false, last = false }) {
  const toneMap = {
    primary: 'border-primary-200 bg-primary-50/85 text-primary',
    pink: 'border-pink-200 bg-pink-50/85 text-pink-700',
    rose: 'border-rose-200 bg-rose-50/85 text-rose-700',
    teal: 'border-teal-200 bg-teal-50/85 text-teal-700',
    amber: 'border-amber-200 bg-amber-50/85 text-amber-700',
    violet: 'border-violet-200 bg-violet-50/85 text-violet-700',
    gray: 'border-gray-200 bg-gray-50/85 text-gray-700',
  }
  const container = toneMap[tone] || toneMap.primary

  return (
    <div className="flex min-w-[160px] flex-1 items-center gap-2">
      <div className={`flex-1 rounded-2xl border px-3 py-2.5 shadow-sm backdrop-blur-sm ${container} ${active ? 'ring-2 ring-primary/20' : ''}`}>
        <div className="text-[11px] font-medium uppercase tracking-wide opacity-80">{label}</div>
        <div className="mt-1 text-sm font-semibold text-gray-900">{value || '--'}</div>
      </div>
      {!last ? (
        <div className="lifeline-rail relative hidden h-3 flex-[0_0_24px] items-center rounded-full sm:flex">
          <div className="lifeline-wave absolute inset-0 opacity-80" />
          <span className={`lifeline-node absolute left-[calc(50%-5px)] ${active ? '' : 'opacity-75'}`} />
        </div>
      ) : null}
    </div>
  )
}

export default function LivingPulseStrip({ title, subtitle, nodes = [], compact = false }) {
  if (!nodes.length) return null

  return (
    <div className={`rounded-2xl border border-primary-100/70 bg-white/85 p-4 shadow-sm backdrop-blur-sm ${compact ? '' : 'overflow-hidden'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">{title}</div>
          {subtitle ? <div className="mt-1 text-sm text-gray-600">{subtitle}</div> : null}
        </div>
        <div className="text-xs text-gray-400">{nodes.length} 个脉冲节点</div>
      </div>
      <div className={`mt-4 flex flex-col gap-3 ${compact ? '' : 'sm:flex-row sm:flex-wrap'}`}>
        {nodes.map((node, index) => (
          <PulseNode
            key={`${node.label}-${index}`}
            label={node.label}
            value={node.value}
            tone={node.tone}
            active={Boolean(node.active)}
            last={index === nodes.length - 1}
          />
        ))}
      </div>
    </div>
  )
}
