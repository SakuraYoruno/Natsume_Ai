import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { buildLifeTimelinePath } from '../../../../models/lifeBodyRoutes.js'
import {
  LIFE_BODY_PATHOLOGY_SEVERITY_LABELS,
  LIFE_BODY_SEVERITY_CLASSES,
} from '../../../../shared/statusMeta.js'

export default function LifeBodyPathologyPanel({ pathologies = [] }) {
  return (
    <section className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,_rgba(244,63,94,0.08),_transparent_34%),radial-gradient(circle_at_bottom_left,_rgba(249,115,22,0.08),_transparent_30%)]" />
      <div className="relative">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-rose-600/80">病灶面板</div>
            <div className="mt-1 text-sm text-gray-600">
              优先展示真正会影响行为输出的病灶，而不是没有上下文的孤立统计。
            </div>
          </div>
          <div className="rounded-full border border-white/80 bg-white/85 px-3 py-1 text-xs text-gray-500 shadow-sm">
            {`${pathologies.length} 个病灶`}
          </div>
        </div>

        {pathologies.length > 0 ? (
          <div className="mt-4 space-y-4">
            {pathologies.map((item) => (
              <div key={item.id} className="rounded-3xl border border-white/80 bg-white/86 p-4 shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-gray-500">{item.organLabel}</div>
                    <div className="mt-1 text-sm font-semibold text-gray-900">{item.title}</div>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${LIFE_BODY_SEVERITY_CLASSES[item.severity] || LIFE_BODY_SEVERITY_CLASSES.info}`}>
                    {LIFE_BODY_PATHOLOGY_SEVERITY_LABELS[item.severity] || LIFE_BODY_PATHOLOGY_SEVERITY_LABELS.info}
                  </span>
                </div>

                <div className="mt-3 text-sm leading-6 text-gray-600">{item.symptom}</div>

                <div className="mt-4 grid gap-3">
                  <div className="rounded-2xl border border-white/75 bg-white/82 px-3 py-2 text-xs text-gray-500 shadow-sm">
                    <span className="font-medium text-gray-700">持续：</span>
                    {item.durationLabel}
                  </div>
                  <div className="rounded-2xl border border-white/75 bg-white/82 px-3 py-2 text-xs text-gray-500 shadow-sm">
                    <span className="font-medium text-gray-700">影响：</span>
                    {item.impactLabel}
                  </div>
                  <div className="rounded-2xl border border-white/75 bg-white/82 px-3 py-2 text-xs text-gray-500 shadow-sm">
                    <span className="font-medium text-gray-700">来源：</span>
                    {item.source}
                  </div>
                </div>

                {item.representativeRefs?.length > 0 ? (
                  <div className="mt-4">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">Representative Requests</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {item.representativeRefs.slice(0, 3).map((ref) => (
                        <Link
                          key={`${item.id}-${ref.id}`}
                          to={ref.to}
                          className="rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                        >
                          {ref.subtitle}
                        </Link>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="mt-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      to={item.detailPath || `/life/pathologies/${encodeURIComponent(item.id)}`}
                      className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                    >
                      查看病灶详情
                    </Link>
                    <Link
                      to={buildLifeTimelinePath({ organId: item.organId, type: 'pathology' })}
                      className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                    >
                      查看病灶事件链
                    </Link>
                    <Link
                      to={item.recommendedPath}
                      className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
                    >
                      {item.recommendedLabel}
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-4">
            <EmptyStateCard
              title="当前没有形成稳定病灶"
              hint="这不代表系统绝对健康，而是当前窗口还没有聚成足够稳定的异常簇。"
              className="p-5"
            />
          </div>
        )}
      </div>
    </section>
  )
}
