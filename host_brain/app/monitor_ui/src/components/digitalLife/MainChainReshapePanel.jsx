import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  BrainCircuit,
  ChevronRight,
  CircleDot,
  Database,
  HeartPulse,
  Network,
  Repeat2,
  Route,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import StatusBadge from '../StatusBadge.jsx'

const ICONS = {
  entry: Route,
  guard: ShieldCheck,
  context: BrainCircuit,
  memory: Database,
  organs: Network,
  growth: Sparkles,
  output: Repeat2,
  tick: Activity,
}

const STAGE_CLASS = {
  healthy: 'border-emerald-200 bg-emerald-50/70',
  running: 'border-blue-200 bg-blue-50/70',
  degraded: 'border-orange-200 bg-orange-50/70',
  down: 'border-red-200 bg-red-50/75',
  unknown: 'border-gray-200 bg-gray-50/75',
}

const DOT_CLASS = {
  healthy: 'bg-emerald-500',
  running: 'bg-blue-500',
  degraded: 'bg-orange-500',
  down: 'bg-red-500',
  unknown: 'bg-gray-400',
}

function MetricPill({ item }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/80 px-3 py-2 shadow-sm">
      <div className="text-xs text-gray-500">{item.label}</div>
      <div className="mt-1 text-lg font-semibold text-gray-900">{item.value ?? '--'}</div>
      <div className="mt-0.5 text-xs text-gray-400">{item.hint ?? '--'}</div>
    </div>
  )
}

function StageNode({ item, index, total }) {
  const Icon = ICONS[item.id] || CircleDot
  const statusClass = STAGE_CLASS[item.status] || STAGE_CLASS.unknown
  const dotClass = DOT_CLASS[item.status] || DOT_CLASS.unknown

  return (
    <div className="relative">
      {index < total - 1 ? (
        <div className="pointer-events-none absolute left-[1.15rem] top-10 hidden h-[calc(100%-0.5rem)] w-px bg-gray-200 md:block" />
      ) : null}
      <Link
        to={item.to || '/digital-life'}
        className={`group block rounded-2xl border p-3 transition hover:-translate-y-0.5 hover:shadow-sm ${statusClass}`}
      >
        <div className="flex items-start gap-3">
          <span className="relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/80 bg-white text-primary shadow-sm">
            <span className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ${dotClass}`} />
            <Icon size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <div className="text-sm font-semibold text-gray-800">{item.title}</div>
              <ChevronRight size={14} className="mt-0.5 shrink-0 text-gray-400 transition group-hover:text-primary" />
            </div>
            <div className="mt-1 text-xs leading-5 text-gray-600">{item.detail}</div>
            {item.evidence?.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {item.evidence.slice(0, 3).map((text) => (
                  <span key={text} className="rounded-full border border-white/80 bg-white/75 px-2 py-0.5 text-[11px] text-gray-500">
                    {text}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </Link>
    </div>
  )
}

export default function MainChainReshapePanel({ model }) {
  if (!model) return null

  return (
    <section className="relative overflow-hidden rounded-xl border border-white/80 bg-white/82 p-5 shadow-card backdrop-blur-sm">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary-100 to-transparent" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={model.status} label={model.title} />
              <span className="rounded-full border border-primary-100 bg-primary-50 px-2.5 py-1 text-xs font-medium text-primary-700">
                重塑主链
              </span>
            </div>
            <div className="mt-3 flex items-center gap-2 text-base font-semibold text-gray-900">
              <HeartPulse size={18} className="text-primary" />
              数字生命主链闭环
            </div>
            <div className="mt-1 max-w-4xl text-sm leading-6 text-gray-600">{model.summary}</div>
          </div>
          <div className="grid min-w-[260px] grid-cols-2 gap-2">
            {model.metrics?.slice(0, 4).map((item) => (
              <MetricPill key={item.label} item={item} />
            ))}
          </div>
        </div>

        <div className="mt-5 grid gap-3 lg:grid-cols-4">
          {model.stages?.map((item, index) => (
            <StageNode key={item.id} item={item} index={index} total={model.stages.length} />
          ))}
        </div>

        <div className="mt-4 rounded-2xl border border-white/80 bg-white/75 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
            <span className="font-semibold text-gray-700">断链判定</span>
            {model.gaps?.length ? (
              model.gaps.map((item) => (
                <Link key={item.id} to={item.to || '/digital-life'} className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-orange-700 hover:border-orange-300">
                  {item.title}
                </Link>
              ))
            ) : (
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-emerald-700">
                未发现断链
              </span>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
