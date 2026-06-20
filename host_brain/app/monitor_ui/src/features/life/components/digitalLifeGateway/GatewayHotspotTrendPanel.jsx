import React from 'react'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { getHumanoidHotspotTrendColor } from '../../digitalLifeGatewayModel.js'

export default function GatewayHotspotTrendPanel({
  trendData,
  labels,
  series,
  buckets,
}) {
  if (!trendData) return null

  return (
    <div className="mt-4 rounded-2xl border border-white/80 bg-white/88 p-4 shadow-sm backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-gray-600">病灶演化趋势</div>
          <div className="mt-1 text-sm text-gray-700">
            {trendData?.summary || '当前窗口尚未形成清晰的病灶演化曲线。'}
          </div>
        </div>
        {trendData?.latest_dominant_root_cause_code ? (
          <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">
            {`${trendData?.latest_dominant_organ_label || trendData?.latest_dominant_organ_system} · ${trendData.latest_dominant_root_cause_code}`}
          </span>
        ) : null}
      </div>

      {labels.length > 0 && series.length > 0 ? (
        <div className="mt-4">
          <LineChart
            title="器官病灶升温曲线"
            xData={labels}
            yUnit="req"
            height={240}
            series={series.map((item, index) => ({
              name: item.organ_label,
              data: item.request_counts || [],
              color: getHumanoidHotspotTrendColor(item.organ_system, index),
            }))}
          />
          <div className="mt-3 flex flex-wrap gap-2">
            {buckets
              .filter((item) => Number(item?.abnormal_requests || 0) > 0)
              .slice(-4)
              .map((item) => (
                <span
                  key={`bucket-${item.bucket_index}`}
                  className="rounded-full border border-rose-100 bg-rose-50 px-2.5 py-1 text-[11px] font-medium text-rose-700"
                >
                  {`${item.label} · ${item.dominant_organ_label || '--'} · ${item.dominant_root_cause_code || 'NONE'}`}
                </span>
              ))}
          </div>
        </div>
      ) : (
        <div className="mt-4">
          <EmptyStateCard
            title="暂无病灶演化趋势"
            hint="当前时间窗口内还没有足够的异常样本形成可读趋势。"
            className="p-4"
          />
        </div>
      )}
    </div>
  )
}
