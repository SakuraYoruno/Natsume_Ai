import React from 'react'
import { Link } from 'react-router-dom'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import RepresentativeRequests from '../../../../shared/ui/observability/RepresentativeRequests.jsx'
import { createRequestRefs } from '../../../../models/observabilityRefs.js'
import {
  formatGatewayPercent,
  getHumanoidHotspotSeverityClass,
} from '../../digitalLifeGatewayModel.js'

export default function GatewayHotspotCards({ hotspots }) {
  if (hotspots.length === 0) {
    return (
      <div className="mt-4">
        <EmptyStateCard
          title="暂无系统级病灶热区"
          hint="当前窗口内虽然可能有单点异常，但还没有形成稳定聚类。"
          className="p-4"
        />
      </div>
    )
  }

  return (
    <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
      {hotspots.map((item) => (
        <div key={item.hotspot_id} className="rounded-2xl border border-white/80 bg-white/92 p-4 shadow-sm backdrop-blur-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidHotspotSeverityClass(item.severity)}`}>
              {item.severity}
            </span>
            <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
              {item.organ_label}
            </span>
            <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
              {item.root_cause_code}
            </span>
            <span className="ml-auto text-xs text-gray-500">
              {`${item.request_count} requests · ${formatGatewayPercent(item.share_pct)}`}
            </span>
          </div>

          <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
            {item.narrative}
          </div>

          <div className="mt-3 rounded-xl border border-rose-100 bg-rose-50/60 px-3 py-2 text-xs leading-6 text-rose-800">
            {item.operator_focus}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {(item.evidence_codes || []).slice(0, 4).map((code) => (
              <span
                key={`${item.hotspot_id}-${code}`}
                className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
              >
                {code}
              </span>
            ))}
            <Link
              to={item.recommended_view_path}
              className="ml-auto inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 hover:border-rose-300 hover:bg-rose-100"
            >
              {item.recommended_view_label || '打开病灶热区'}
            </Link>
          </div>

          <div className="mt-3">
            <RepresentativeRequests
              title="病灶样本"
              items={createRequestRefs((item.representative_requests || []).slice(0, 3))}
              emptyTitle="暂无病灶样本"
              emptyHint="当前病灶还未沉淀出稳定的代表性 request。"
            />
          </div>
        </div>
      ))}
    </div>
  )
}
