import React from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Database,
  FileSearch,
  FileText,
  RefreshCw,
  ShieldCheck,
  Wand2,
} from 'lucide-react'
import { OPERATIONS_PROMPT_COPY } from '../../../../shared/content/terminology.js'

function scrollToSection(id) {
  if (typeof document === 'undefined') return
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function toneClass(tone) {
  return {
    green: 'border-green-100 bg-green-50 text-green-700',
    blue: 'border-blue-100 bg-blue-50 text-blue-700',
    amber: 'border-amber-100 bg-amber-50 text-amber-700',
    orange: 'border-orange-100 bg-orange-50 text-orange-700',
    red: 'border-red-100 bg-red-50 text-red-700',
    gray: 'border-gray-100 bg-gray-50 text-gray-600',
  }[tone] || 'border-gray-100 bg-gray-50 text-gray-600'
}

function toneLabel(tone) {
  return {
    green: 'ready',
    blue: 'sync',
    amber: 'check',
    orange: 'blocked',
    red: 'error',
    gray: 'pending',
  }[tone] || 'watch'
}

const CARD_ICONS = {
  catalog: FileSearch,
  filter: Database,
  selection: Wand2,
  source: ShieldCheck,
  runtime: FileText,
}

function GateCard({ card }) {
  const Icon = CARD_ICONS[card.key] || FileText
  return (
    <button
      type="button"
      onClick={() => scrollToSection(card.targetId)}
      className="min-w-0 rounded-lg border border-gray-100 bg-white/86 px-3 py-3 text-left transition hover:border-primary-100 hover:bg-primary-50/30"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="inline-flex min-w-0 items-center gap-2 text-xs font-semibold text-gray-500">
          <Icon size={14} className="shrink-0 text-primary" />
          <span className="truncate">{card.label}</span>
        </div>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] font-semibold ${toneClass(card.tone)}`}>
          {toneLabel(card.tone)}
        </span>
      </div>
      <div className="mt-2 truncate text-sm font-bold text-gray-900">{card.value}</div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">{card.detail}</div>
    </button>
  )
}

export default function PromptDecisionRail({ decisionRail, onRefreshView, busy }) {
  const mission = decisionRail?.mission || {
    tone: 'gray',
    title: '等待 PromptSpec 数据',
    detail: '目录同步后会显示当前建议动作。',
    action: '等待同步',
  }
  const gateCards = decisionRail?.gateCards || []

  return (
    <section
      aria-labelledby="prompt-decision-rail-title"
      aria-label="PromptSpec Decision Rail"
      className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm"
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 text-sm font-semibold text-gray-800">
            <FileText size={16} className="text-primary" />
            <h2 id="prompt-decision-rail-title">{OPERATIONS_PROMPT_COPY.commandRailTitle}</h2>
          </div>
          <p className="mt-1 text-xs leading-5 text-gray-500">
            {OPERATIONS_PROMPT_COPY.commandRailDescription}
          </p>
        </div>
        <div className={`rounded-lg border px-3 py-2 text-sm ${toneClass(mission.tone)}`}>
          <div className="font-bold">{mission.title}</div>
          <div className="mt-1 text-xs leading-5">{mission.detail}</div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-5">
        {gateCards.map((card) => (
          <GateCard key={card.key} card={card} />
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="mr-1 inline-flex items-center gap-1.5 text-xs font-semibold text-gray-500">
          <ShieldCheck size={13} />
          {OPERATIONS_PROMPT_COPY.focusTitle}：{mission.action}
        </span>
        <button
          type="button"
          onClick={() => scrollToSection('prompt-template-catalog')}
          className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white/80 px-2.5 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
        >
          定位 Catalog
          <ArrowRight size={12} />
        </button>
        <button
          type="button"
          onClick={() => scrollToSection('prompt-template-detail')}
          className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white/80 px-2.5 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
        >
          定位 Detail
          <ArrowRight size={12} />
        </button>
        <Link
          to="/operations/persona"
          className="inline-flex items-center gap-1 rounded border border-primary-200 bg-primary-50 px-2.5 py-1.5 text-xs font-semibold text-primary hover:bg-primary-100"
        >
          人格运营
          <ArrowRight size={12} />
        </Link>
        <button
          type="button"
          onClick={onRefreshView}
          disabled={busy}
          className="inline-flex items-center gap-1 rounded border border-primary-200 bg-white/80 px-2.5 py-1.5 text-xs font-semibold text-primary hover:bg-primary-50 disabled:opacity-50"
        >
          <RefreshCw size={12} className={busy ? 'animate-spin' : ''} />
          刷新视图
        </button>
      </div>
    </section>
  )
}
