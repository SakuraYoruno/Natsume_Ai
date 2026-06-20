import React from 'react'
import LineChart from '../../../../shared/ui/charts/LineChart.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'

export default function GatewayTrendPanel({ view, hasAlertsData }) {
  const {
    humanoidTrendLabels,
    humanoidClosedLoopRateSeries,
    humanoidAckTouchRateSeries,
    humanoidProgressTouchRateSeries,
    humanoidTrendRequestTotal,
    humanoidTrendIntervalMinutes,
    humanoidAlertStatus,
    humanoidAlertSummary,
    humanoidAlertItems,
    humanoidAlertBannerVariant,
    humanoidTrendClosedLoopColor,
  } = view

  if (humanoidTrendLabels.length === 0) return null

  return (
    <div className="mt-4 card p-4">
      <LineChart
        title="闭环触达趋势（Closed Loop / ACK / Progress）"
        xData={humanoidTrendLabels}
        yUnit="%"
        height={260}
        series={[
          { name: 'Closed Loop', data: humanoidClosedLoopRateSeries, color: humanoidTrendClosedLoopColor },
          { name: 'ACK Touch', data: humanoidAckTouchRateSeries, color: '#2970FF' },
          { name: 'Progress Touch', data: humanoidProgressTouchRateSeries, color: '#6366F1' },
        ]}
      />
      <div className="mt-2 text-xs text-gray-400">
        {`样本=${humanoidTrendRequestTotal} | interval=${humanoidTrendIntervalMinutes}m`}
      </div>
      {hasAlertsData && humanoidAlertStatus !== 'healthy' ? (
        <div className="mt-3">
          <NoticeBanner
            variant={humanoidAlertBannerVariant}
            title="闭环趋势告警"
          >
            {humanoidAlertSummary || '闭环趋势存在异常波动。'}
          </NoticeBanner>
        </div>
      ) : null}
      {humanoidAlertItems.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {humanoidAlertItems.slice(0, 6).map((item, index) => (
            <span
              key={`${item.code}-${index}`}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                item.severity === 'critical'
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : 'border-orange-200 bg-orange-50 text-orange-700'
              }`}
            >
              {`${item.code} · ${item.value == null ? '--' : `${item.value}%`}`}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}
