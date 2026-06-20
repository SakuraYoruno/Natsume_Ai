import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import {
  formatGatewayPercent,
  getHumanoidSourceImpactClass,
} from '../../digitalLifeGatewayModel.js'

export default function GatewaySourceCorrelationPanel({ data, items }) {
  if (!data) return null

  return (
    <div className="mt-4 rounded-2xl border border-amber-200 bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(59,130,246,0.08),_transparent_40%)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">源头归因 / 系统关联</div>
          <div className="mt-1 text-sm text-gray-700">
            {data?.summary || '当前病灶已出现，但还没有形成稳定的源头归因。'}
          </div>
        </div>
        {data?.primary_source_label ? (
          <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
            {`${data.primary_source_label} · ${data.primary_source_system || 'source'}`}
          </span>
        ) : null}
      </div>

      {items.length > 0 ? (
        <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
          {items.map((item) => (
            <div key={item.source_id} className="rounded-2xl border border-white/80 bg-white/92 p-4 shadow-sm backdrop-blur-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidSourceImpactClass(item.impact_level)}`}>
                  {item.impact_level}
                </span>
                <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                  {item.source_label}
                </span>
                {item.source_status ? (
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                    {item.source_status}
                  </span>
                ) : null}
                <span className="ml-auto text-xs text-gray-500">
                  {`confidence ${formatGatewayPercent(item.confidence_pct)}`}
                </span>
              </div>

              <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                {item.narrative}
              </div>

              <div className="mt-3 rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs leading-6 text-amber-800">
                {item.operator_focus}
              </div>

              {(item.correlated_hotspots || []).length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {(item.correlated_hotspots || []).slice(0, 4).map((code) => (
                    <span
                      key={`${item.source_id}-${code}`}
                      className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700"
                    >
                      {code}
                    </span>
                  ))}
                </div>
              ) : null}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {(item.evidence || []).slice(0, 4).map((evidence) => (
                  <span
                    key={`${item.source_id}-${evidence}`}
                    className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                  >
                    {evidence}
                  </span>
                ))}
                <Link
                  to={item.recommended_view_path}
                  className="ml-auto inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 hover:border-amber-300 hover:bg-amber-100"
                >
                  {item.recommended_view_label || '打开源头归因'}
                </Link>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4">
          <EmptyStateCard
            title="暂无稳定源头归因"
            hint="当前窗口内虽然已经形成病灶，但底层系统关联还不够稳定。"
            className="p-4"
          />
        </div>
      )}
    </div>
  )
}
