import React, { useMemo } from 'react'
import { AlertTriangle, CheckCircle2, FileCheck2, GitBranch, LockKeyhole, ShieldCheck } from 'lucide-react'
import { buildPentestAttackGraphNarrative } from '../../pentestAttackGraphModel.js'

const TONE_CLASS = {
  green: 'border-green-100 bg-green-50/75 text-green-800',
  orange: 'border-orange-100 bg-orange-50/75 text-orange-800',
  red: 'border-red-100 bg-red-50/75 text-red-800',
  rose: 'border-rose-100 bg-rose-50/75 text-rose-800',
  gray: 'border-gray-100 bg-gray-50/75 text-gray-700',
}

const TONE_DOT_CLASS = {
  green: 'bg-green-500',
  orange: 'bg-orange-500',
  red: 'bg-red-500',
  rose: 'bg-rose-500',
  gray: 'bg-gray-400',
}

const CARD_ICON = {
  target: GitBranch,
  scope: ShieldCheck,
  approval: LockKeyhole,
  report: FileCheck2,
}

function toneClass(tone) {
  return TONE_CLASS[tone] || TONE_CLASS.gray
}

function toneDotClass(tone) {
  return TONE_DOT_CLASS[tone] || TONE_DOT_CLASS.gray
}

function SummaryCard({ card }) {
  const Icon = CARD_ICON[card.key] || CheckCircle2
  return (
    <div className="rounded-lg border border-white/80 bg-white/90 px-3 py-2 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-xs font-semibold text-gray-500">
          <Icon size={13} className="shrink-0 text-primary" />
          <span className="truncate">{card.label}</span>
        </span>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-semibold ${toneClass(card.tone)}`}>
          {card.state}
        </span>
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-gray-900">{card.value}</div>
      <div className="mt-0.5 line-clamp-2 text-xs leading-5 text-gray-500">{card.detail}</div>
    </div>
  )
}

export default function AttackGraphNarrativePanel({
  graph,
  mode = 'planned',
  ariaLabel = 'Attack Path Narrative',
}) {
  const narrative = useMemo(() => buildPentestAttackGraphNarrative(graph, { mode }), [graph, mode])

  return (
    <section aria-label={ariaLabel} className="rounded-xl border border-primary-100/70 bg-white/88 p-3 shadow-sm">
      <div className={`rounded-lg border px-3 py-2 ${toneClass(narrative.tone)}`}>
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          <AlertTriangle size={14} />
          读图结论
        </div>
        <div className="mt-1 text-sm font-bold text-gray-950">{narrative.headline}</div>
        <div className="mt-1 text-xs leading-5">{narrative.detail}</div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        {narrative.cards.map((card) => (
          <SummaryCard key={card.key} card={card} />
        ))}
      </div>

      <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50/75 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-gray-700">关键路径</span>
          <span className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] font-semibold text-gray-500">
            {narrative.source}
          </span>
        </div>
        <div className="mt-2 space-y-2">
          {narrative.pathItems.length > 0 ? narrative.pathItems.map((item, index) => (
            <div key={item.id} className="grid grid-cols-[0.9rem_minmax(0,1fr)] gap-2 text-xs">
              <span className={`mt-1.5 h-2 w-2 rounded-full ${toneDotClass(item.tone)}`} />
              <span className="min-w-0">
                <span className="flex items-center gap-1.5">
                  <span className="font-semibold text-gray-800">{index + 1}. {item.label}</span>
                  <span className="rounded bg-white px-1.5 py-0.5 text-[11px] text-gray-500">{item.status}</span>
                </span>
                <span className="mt-0.5 block truncate text-gray-500">{item.meta}</span>
              </span>
            </div>
          )) : (
            <div className="rounded border border-gray-100 bg-white px-2 py-2 text-xs text-gray-500">
              暂无 graph 节点，等待后端或表单配置。
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-cyan-100 bg-cyan-50/70 px-3 py-2 text-xs leading-5 text-cyan-800">
        <span className="font-semibold">下一步：</span>
        {narrative.action}
      </div>
    </section>
  )
}
