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
import FreshnessPill from '../../../../shared/ui/observability/FreshnessPill.jsx'
import {
  LIFE_BODY_STATUS_CLASSES,
  LIFE_BODY_STATUS_LABELS,
  LIFE_BODY_TONE_CLASSES,
} from '../../../../shared/statusMeta.js'
import { buildLifeTimelinePath, buildPathologyCenterPath } from '../../../../models/lifeBodyRoutes.js'

const ICON_MAP = {
  nervous: Brain,
  limbic: Heart,
  memory: Database,
  endocrine: Activity,
  immune: ShieldAlert,
  motor: Sparkles,
  self: Orbit,
}

function OrganSystemCard({ item, isPrimary = false }) {
  const Icon = ICON_MAP[item.id] || Brain
  const badgeClass = LIFE_BODY_STATUS_CLASSES[item.status] || LIFE_BODY_STATUS_CLASSES.pending
  const badgeLabel = LIFE_BODY_STATUS_LABELS[item.status] || LIFE_BODY_STATUS_LABELS.pending
  const timelinePath = buildLifeTimelinePath({ organId: item.id })
  const pathologyPath = buildPathologyCenterPath({ organId: item.id })

  return (
    <div className={`rounded-3xl border bg-white/86 p-4 shadow-sm ${isPrimary ? 'border-primary-200 ring-1 ring-primary-100' : 'border-white/80'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-white/80 bg-white/90 text-primary shadow-sm">
            <Icon size={18} />
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-sm font-semibold text-gray-900">{item.name}</div>
              {isPrimary ? (
                <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[10px] font-semibold text-primary-700">
                  当前首要
                </span>
              ) : null}
            </div>
            <div className="mt-1 text-xs text-gray-500">{item.summary}</div>
          </div>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${badgeClass}`}>
          {badgeLabel}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-white/80 bg-white/85 px-3 py-1 text-xs text-gray-500 shadow-sm">
          {`趋势 ${item.trend || '--'}`}
        </span>
        <span className="rounded-full border border-white/80 bg-white/85 px-3 py-1 text-xs text-gray-500 shadow-sm">
          {`风险 ${item.riskLevel || '--'}`}
        </span>
        <FreshnessPill timestamp={item.freshness} staleAfterMs={180000} />
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">症状</div>
          <div className="mt-2 space-y-2">
            {(item.symptoms || []).slice(0, 2).map((symptom) => (
              <div key={symptom} className="rounded-2xl border border-white/75 bg-white/78 px-3 py-2 text-xs text-gray-600 shadow-sm">
                {symptom}
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">行为影响</div>
          <div className="mt-2 space-y-2">
            {(item.downstreamImpacts || []).slice(0, 2).map((impact) => (
              <div key={impact} className="rounded-2xl border border-white/75 bg-white/78 px-3 py-2 text-xs text-gray-600 shadow-sm">
                {impact}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {item.representativeRefs?.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {item.representativeRefs.slice(0, 2).map((ref) => (
              <Link
                key={`${item.id}-${ref.id}`}
                to={ref.to}
                className="rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
              >
                {ref.subtitle}
              </Link>
            ))}
          </div>
        ) : (
          <span className="text-xs text-gray-400">当前没有稳定的代表性请求</span>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={timelinePath}
            className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
          >
            相关事件
          </Link>
          <Link
            to={pathologyPath}
            className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
          >
            相关病灶
          </Link>
          <Link
            to={item.drilldownPath}
            className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
          >
            {item.drilldownLabel}
          </Link>
        </div>
      </div>
    </div>
  )
}

export default function LifeBodyTopology({ lifeState, organSystems = [], organStatusCounts = {}, primaryPathology = null, primaryOrganId = null }) {
  const leftColumn = organSystems.filter((item) => ['nervous', 'limbic', 'memory'].includes(item.id))
  const rightColumn = organSystems.filter((item) => ['endocrine', 'immune', 'motor'].includes(item.id))
  const bottomRow = organSystems.find((item) => item.id === 'self') || null
  const stableCount = organStatusCounts.stable || 0
  const warningCount = organStatusCounts.warning || 0
  const criticalCount = organStatusCounts.critical || 0

  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_center,_rgba(41,112,255,0.08),_transparent_42%),radial-gradient(circle_at_top,_rgba(236,72,153,0.06),_transparent_30%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">生命体本体图</div>
            <div className="mt-1 text-sm text-gray-600">
              中央先回答“这个生命体现在整体怎么样”，周围再展开各器官系统的状态和影响。
            </div>
          </div>
          <div className="rounded-full border border-white/80 bg-white/85 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${organSystems.length} 个器官系统`}
          </div>
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,360px)_minmax(0,1fr)]">
          <div className="space-y-4">
            {leftColumn.map((item) => (
              <OrganSystemCard key={item.id} item={item} isPrimary={item.id === primaryOrganId} />
            ))}
          </div>

          <div className="rounded-[32px] border border-primary-100 bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.95),_rgba(239,246,255,0.92)_52%,_rgba(253,242,248,0.9))] p-6 shadow-card">
            <div className="text-center">
              <div className={`mx-auto inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${LIFE_BODY_TONE_CLASSES[lifeState.tone] || LIFE_BODY_TONE_CLASSES.pending}`}>
                数字生命主体
              </div>
              <div className="mt-4 text-4xl font-semibold text-gray-900">{lifeState.label}</div>
              <div className="mt-2 text-sm leading-6 text-gray-600">{lifeState.summary}</div>
            </div>

            <div className="mt-5 grid grid-cols-3 gap-3">
              <div className="rounded-2xl border border-white/80 bg-white/85 px-3 py-3 text-center shadow-sm">
                <div className="text-xl font-semibold text-emerald-600">{stableCount}</div>
                <div className="mt-1 text-[11px] text-gray-500">稳定器官</div>
              </div>
              <div className="rounded-2xl border border-white/80 bg-white/85 px-3 py-3 text-center shadow-sm">
                <div className="text-xl font-semibold text-orange-500">{warningCount}</div>
                <div className="mt-1 text-[11px] text-gray-500">波动器官</div>
              </div>
              <div className="rounded-2xl border border-white/80 bg-white/85 px-3 py-3 text-center shadow-sm">
                <div className="text-xl font-semibold text-red-600">{criticalCount}</div>
                <div className="mt-1 text-[11px] text-gray-500">重压器官</div>
              </div>
            </div>

            <div className="mt-5 rounded-3xl border border-white/80 bg-white/85 p-4 shadow-sm">
              <div className="text-xs font-semibold text-gray-500">当前最紧张病灶</div>
              <div className="mt-2 text-sm font-semibold text-gray-900">
                {primaryPathology ? `${primaryPathology.organLabel} · ${primaryPathology.title}` : '当前没有稳定病灶'}
              </div>
              <div className="mt-2 text-xs leading-5 text-gray-500">
                {primaryPathology ? primaryPathology.impactLabel : '生命体当前没有形成稳定的异常病灶聚集。'}
              </div>
              {primaryPathology ? (
                <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
                  <Link
                    to={primaryPathology.detailPath || `/life/pathologies/${encodeURIComponent(primaryPathology.id)}`}
                    className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                  >
                    查看病灶详情
                  </Link>
                  <Link
                    to={buildLifeTimelinePath({ organId: primaryPathology.organId, type: 'pathology' })}
                    className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                  >
                    查看病灶事件链
                  </Link>
                  <Link
                    to={primaryPathology.recommendedPath}
                    className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                  >
                    {primaryPathology.recommendedLabel}
                  </Link>
                </div>
              ) : null}
            </div>
          </div>

          <div className="space-y-4">
            {rightColumn.map((item) => (
              <OrganSystemCard key={item.id} item={item} isPrimary={item.id === primaryOrganId} />
            ))}
          </div>
        </div>

        {bottomRow ? (
          <div className="mt-4">
            <OrganSystemCard item={bottomRow} isPrimary={bottomRow.id === primaryOrganId} />
          </div>
        ) : null}
      </div>
    </section>
  )
}
