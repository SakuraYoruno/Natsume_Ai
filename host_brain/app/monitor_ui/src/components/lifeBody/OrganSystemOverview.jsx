import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Brain,
  Database,
  Heart,
  Orbit,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import MetricCard from '../MetricCard.jsx'
import FreshnessPill from '../obs/FreshnessPill.jsx'
import LoadStateBadge from '../obs/LoadStateBadge.jsx'
import { buildLifeTimelinePath, buildPathologyCenterPath } from '../../models/lifeBodyRoutes.js'
import { getOrganStatusMeta } from '../../models/organSystemModel.js'

const ICON_MAP = {
  nervous: Brain,
  limbic: Heart,
  memory: Database,
  endocrine: Activity,
  immune: ShieldAlert,
  motor: Sparkles,
  self: Orbit,
}

function SummaryList({ title, items }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{title}</div>
      <div className="mt-2 space-y-2">
        {items.length > 0 ? items.map((item) => (
          <div key={item} className="rounded-2xl border border-white/75 bg-white/82 px-3 py-2 text-xs text-gray-600 shadow-sm">
            {item}
          </div>
        )) : (
          <div className="rounded-2xl border border-dashed border-gray-200 bg-gray-50/70 px-3 py-2 text-xs text-gray-400">
            暂无摘要
          </div>
        )}
      </div>
    </div>
  )
}

export default function OrganSystemOverview({ model }) {
  if (!model) return null

  const Icon = ICON_MAP[model.id] || Brain
  const statusMeta = getOrganStatusMeta(model.status)
  const timelinePath = buildLifeTimelinePath({ organId: model.id })
  const pathologyPath = buildPathologyCenterPath({ organId: model.id })

  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(244,63,94,0.05),_transparent_30%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <span className="inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-white/80 bg-white/90 text-primary shadow-sm">
              <Icon size={18} />
            </span>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">Organ System Model</div>
              <div className="mt-1 text-lg font-semibold text-gray-900">{model.name}</div>
              <div className="mt-1 max-w-3xl text-sm leading-6 text-gray-600">{model.summary}</div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusMeta.className}`}>
              {statusMeta.label}
            </span>
            <LoadStateBadge state={model.loadState} />
            {model.freshness ? <FreshnessPill timestamp={model.freshness} staleAfterMs={180000} /> : null}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2 text-xs text-gray-500">
          <span className="rounded-full border border-white/80 bg-white/80 px-3 py-1 shadow-sm">
            趋势 {model.trend || '--'}
          </span>
          <span className="rounded-full border border-white/80 bg-white/80 px-3 py-1 shadow-sm">
            风险 {model.riskLevel || '--'}
          </span>
        </div>

        {model.metrics?.length > 0 ? (
          <div className="mt-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
            {model.metrics.map((item) => (
              <MetricCard
                key={item.label}
                label={item.label}
                value={item.value}
                hint={item.hint}
                tone={item.tone}
                variant="stacked"
              />
            ))}
          </div>
        ) : null}

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <SummaryList title="症状摘要" items={(model.symptoms || []).slice(0, 3)} />
          <SummaryList title="行为影响" items={(model.downstreamImpacts || []).slice(0, 3)} />
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          {model.representativeRefs?.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {model.representativeRefs.slice(0, 2).map((ref) => (
                <Link
                  key={`${model.id}-${ref.id}`}
                  to={ref.to}
                  className="rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                >
                  {ref.subtitle}
                </Link>
              ))}
            </div>
          ) : (
            <span className="text-xs text-gray-400">当前没有稳定的代表性请求样本</span>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={timelinePath}
              className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
            >
              查看相关生命事件
            </Link>
            <Link
              to={pathologyPath}
              className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
            >
              查看相关病灶
            </Link>
            <Link
              to={model.drilldownPath}
              className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
            >
              {model.drilldownLabel}
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
