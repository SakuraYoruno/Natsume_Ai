import React from 'react'
import { Link } from 'react-router-dom'
import LineChart from '../charts/LineChart.jsx'
import EmptyStateCard from '../EmptyStateCard.jsx'
import KeyValueRow from '../KeyValueRow.jsx'
import MetricCard from '../MetricCard.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'
import RepresentativeRequests from '../obs/RepresentativeRequests.jsx'
import SampleMeta from '../obs/SampleMeta.jsx'
import { createRequestRefs } from '../../models/observabilityRefs.js'
import { compactText } from '../../utils/digitalLife.js'

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return '--'
  }
  return `${Number(value).toFixed(1)}%`
}

function toTrendIntervalMinutes(windowMinutes) {
  if (windowMinutes <= 60) return 5
  if (windowMinutes <= 360) return 30
  if (windowMinutes <= 1440) return 60
  return 360
}

function getHumanoidTrendTone(status) {
  if (status === 'critical') return '#EF4444'
  if (status === 'degraded') return '#F97316'
  return '#10B981'
}

function getHumanoidAlertBannerVariant(status) {
  if (status === 'critical') return 'error'
  if (status === 'degraded') return 'warning'
  return 'info'
}

function getHumanoidDiagnosticStatusClass(status) {
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700'
  if (status === 'completed_without_loop') return 'border-orange-200 bg-orange-50 text-orange-700'
  if (status === 'in_progress') return 'border-blue-200 bg-blue-50 text-blue-700'
  if (status === 'closed_loop') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return 'border-gray-200 bg-gray-50 text-gray-600'
}

function getHumanoidHotspotSeverityClass(severity) {
  if (severity === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (severity === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

function getHumanoidSourceImpactClass(impactLevel) {
  if (impactLevel === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (impactLevel === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

function getHumanoidHotspotTrendColor(organSystem, index = 0) {
  const preset = {
    motor_execution: '#EF4444',
    language_feedback: '#F97316',
    somatic_progress: '#6366F1',
    immune_realign: '#E11D48',
    prefrontal_governor: '#8B5CF6',
    closed_loop_integration: '#0EA5E9',
  }
  const fallback = ['#EF4444', '#F97316', '#6366F1', '#0EA5E9', '#8B5CF6']
  return preset[organSystem] || fallback[index % fallback.length]
}

export default function DigitalLifeGatewaySection({ model }) {
  const {
    windowMinutes,
    updatedAt,
    loadState,
    humanoidLoopData,
    humanoidLoopTrendData,
    humanoidLoopAlertsData,
    humanoidLoopRemediationData,
    humanoidLoopDiagnosticsData,
    humanoidLoopHotspotsData,
    humanoidLoopHotspotTrendData,
    humanoidLoopSourceCorrelationData,
  } = model

  const hasHumanoidLoopData = Boolean(humanoidLoopData)
  const humanoidClosedLoopRate = formatPercent(humanoidLoopData?.closed_loop_rate)
  const humanoidAckTouchRate = formatPercent(humanoidLoopData?.ack_touch_rate)
  const humanoidProgressTouchRate = formatPercent(humanoidLoopData?.progress_touch_rate)
  const humanoidDeviationRecoveryRate = formatPercent(humanoidLoopData?.deviation_recovery_rate)
  const humanoidTotalRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.total_gateway_requests ?? 0) : null
  const humanoidClosedLoopRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.closed_loop_requests ?? 0) : null
  const humanoidFailedRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.failed_requests ?? 0) : 0
  const humanoidDeviationDetected = hasHumanoidLoopData ? Number(humanoidLoopData?.deviation_detected_requests ?? 0) : null
  const humanoidClosedLoopHint = humanoidClosedLoopRequests != null && humanoidTotalRequests != null
    ? `${humanoidClosedLoopRequests}/${humanoidTotalRequests}`
    : '--/--'
  const humanoidAckHint = hasHumanoidLoopData
    ? `确认 ${humanoidLoopData?.ack_confirmed_requests ?? 0}`
    : '确认 --'
  const humanoidProgressHint = hasHumanoidLoopData
    ? `触达 ${humanoidLoopData?.progress_touched_requests ?? 0}`
    : '触达 --'
  const humanoidDeviationHint = humanoidDeviationDetected != null
    ? `检测 ${humanoidDeviationDetected}`
    : '检测 --'
  const humanoidTrendLabels = humanoidLoopTrendData?.timeseries_labels || []
  const humanoidClosedLoopRateSeries = humanoidLoopTrendData?.timeseries_closed_loop_rate || []
  const humanoidAckTouchRateSeries = humanoidLoopTrendData?.timeseries_ack_touch_rate || []
  const humanoidProgressTouchRateSeries = humanoidLoopTrendData?.timeseries_progress_touch_rate || []
  const humanoidTrendRequestSeries = humanoidLoopTrendData?.timeseries_total_gateway_requests || []
  const humanoidAlertStatus = String(humanoidLoopAlertsData?.status || '').toLowerCase()
  const humanoidAlertSummary = humanoidLoopAlertsData?.summary || ''
  const humanoidAlertItems = humanoidLoopAlertsData?.alerts || []
  const humanoidTrendClosedLoopColor = getHumanoidTrendTone(humanoidAlertStatus)
  const humanoidRemediationSteps = humanoidLoopRemediationData?.steps || []
  const humanoidRepresentativeRefs = createRequestRefs(humanoidLoopRemediationData?.representative_requests || [])
  const humanoidDiagnostics = humanoidLoopDiagnosticsData?.diagnostics || []
  const humanoidHotspots = humanoidLoopHotspotsData?.hotspots || []
  const humanoidHotspotTrendLabels = humanoidLoopHotspotTrendData?.timeseries_labels || []
  const humanoidHotspotTrendSeries = humanoidLoopHotspotTrendData?.series || []
  const humanoidHotspotTrendBuckets = humanoidLoopHotspotTrendData?.dominant_buckets || []
  const humanoidSourceItems = humanoidLoopSourceCorrelationData?.items || []

  return (
    <LifeSection
      title="人体闭环健康（Gateway）"
      hint="展示请求从即时确认、进度触达到偏航修复与终态收束的健康度。"
      actionTo="/gateway"
      actionLabel="查看通道详情"
    >
      <div className="mb-4">
        <SampleMeta
          loadState={loadState}
          windowMinutes={windowMinutes}
          sampleSize={humanoidTotalRequests}
          sampleLabel="gateway request"
          updatedAt={updatedAt}
          source="/obs/input/humanoid-loop-stats"
          definition="终态收束、ACK 触达、进度触达与偏航恢复都以当前窗口中的 gateway request 为统一统计口径。"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          label="闭环率"
          value={humanoidClosedLoopRate}
          hint={humanoidClosedLoopHint}
          tone="text-emerald-600"
        />
        <MetricCard
          label="ACK 触达率"
          value={humanoidAckTouchRate}
          hint={humanoidAckHint}
          tone="text-blue-600"
        />
        <MetricCard
          label="进度触达率"
          value={humanoidProgressTouchRate}
          hint={humanoidProgressHint}
          tone="text-indigo-600"
        />
        <MetricCard
          label="偏航恢复率"
          value={humanoidDeviationRecoveryRate}
          hint={humanoidDeviationHint}
          tone={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-600'}
        />
      </div>

      {humanoidTrendLabels.length > 0 ? (
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
            {`样本=${humanoidTrendRequestSeries.reduce((sum, item) => sum + Number(item || 0), 0)} | interval=${toTrendIntervalMinutes(windowMinutes)}m`}
          </div>
          {humanoidLoopAlertsData && humanoidAlertStatus !== 'healthy' ? (
            <div className="mt-3">
              <NoticeBanner
                variant={getHumanoidAlertBannerVariant(humanoidAlertStatus)}
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
      ) : null}

      {!humanoidLoopData ? (
        <div className="mt-4">
          <EmptyStateCard
            title="暂无人体闭环采样"
            hint="等待 Gateway 请求进入后，这里会显示 ACK、进度触达和偏航恢复指标。"
            className="p-4"
          />
        </div>
      ) : null}

      {humanoidLoopData && humanoidFailedRequests > 0 ? (
        <div className="mt-4">
          <NoticeBanner
            variant={humanoidFailedRequests > Math.max(2, Math.floor(humanoidTotalRequests * 0.2)) ? 'warning' : 'info'}
            title="闭环存在失败请求"
          >
            {`失败 ${humanoidFailedRequests} · 已进入终态 ${humanoidLoopData?.completed_requests ?? 0} · 终态率 ${formatPercent(humanoidLoopData?.completion_rate)}。该口径按是否出现终态事件统计，可能与失败计数重叠。`}
          </NoticeBanner>
        </div>
      ) : null}

      {humanoidLoopData && (humanoidLoopData?.stage_counts || []).length > 0 ? (
        <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">闭环阶段分布</div>
            <div className="space-y-2">
              {(humanoidLoopData?.stage_counts || []).slice(0, 6).map((item) => (
                <div key={item.stage} className="rounded-lg border border-primary-100/70 bg-white/80 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-slate-700">{item.stage}</span>
                    <span className="text-gray-500">{item.count}</span>
                  </div>
                  <div className="mt-1 text-gray-500">{`rate ${formatPercent(item.rate)}`}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 text-xs font-semibold text-gray-600">闭环核心计数</div>
            <div className="space-y-3">
              <KeyValueRow label="总 Gateway 请求" value={humanoidTotalRequests} />
              <KeyValueRow label="已进入终态" value={humanoidLoopData?.completed_requests ?? 0} />
              <KeyValueRow label="失败请求" value={humanoidFailedRequests} valueClassName={humanoidFailedRequests > 0 ? 'text-red-600' : 'text-gray-700'} />
              <KeyValueRow label="ACK 已确认" value={humanoidLoopData?.ack_confirmed_requests ?? 0} />
              <KeyValueRow label="进度触达请求" value={humanoidLoopData?.progress_touched_requests ?? 0} />
              <KeyValueRow
                label="偏航恢复"
                value={`${humanoidLoopData?.deviation_recovered_requests ?? 0}/${humanoidDeviationDetected}`}
                valueClassName={humanoidDeviationDetected > 0 ? 'text-orange-600' : 'text-gray-700'}
              />
            </div>
          </div>
        </div>
      ) : null}

      {humanoidLoopRemediationData ? (
        <div className="mt-4 card p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs font-semibold text-gray-600">闭环自愈建议</div>
              <div className="mt-1 text-sm text-gray-700">
                {humanoidLoopRemediationData?.summary || '当前暂无额外排查建议。'}
              </div>
            </div>
            <span
              className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                humanoidAlertStatus === 'critical'
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : humanoidAlertStatus === 'degraded'
                    ? 'border-orange-200 bg-orange-50 text-orange-700'
                    : 'border-emerald-200 bg-emerald-50 text-emerald-700'
              }`}
            >
              {humanoidLoopRemediationData?.primary_focus || '维持稳定'}
            </span>
          </div>

          {humanoidLoopRemediationData?.next_review_hint ? (
            <div className="mt-3 rounded-xl border border-primary-100/70 bg-primary-50/55 px-3 py-2 text-xs text-primary-700">
              {humanoidLoopRemediationData.next_review_hint}
            </div>
          ) : null}

          <div className="mt-4">
            <RepresentativeRequests
              title="快速钻取请求"
              hint="优先打开一条代表性 request，把闭环异常还原到主链路。"
              items={humanoidRepresentativeRefs}
              emptyTitle="暂无代表性 request"
              emptyHint="等异常样本更稳定后，这里会给出直达 Trace 的代表性 request。"
            />
          </div>

          {humanoidLoopHotspotsData ? (
            <div className="mt-4 rounded-2xl border border-rose-200 bg-[radial-gradient(circle_at_top_left,_rgba(244,63,94,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(249,115,22,0.08),_transparent_40%)] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-gray-600">病灶热区 / 器官聚类</div>
                  <div className="mt-1 text-sm text-gray-700">
                    {humanoidLoopHotspotsData?.summary || '当前窗口尚未形成系统级病灶热区。'}
                  </div>
                </div>
                <span className="rounded-full border border-white/80 bg-white/90 px-2.5 py-1 text-xs font-medium text-rose-700 shadow-sm">
                  {`hotspots ${humanoidLoopHotspotsData?.hotspot_count ?? humanoidHotspots.length}`}
                </span>
              </div>

              {humanoidLoopHotspotTrendData ? (
                <div className="mt-4 rounded-2xl border border-white/80 bg-white/88 p-4 shadow-sm backdrop-blur-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-gray-600">病灶演化趋势</div>
                      <div className="mt-1 text-sm text-gray-700">
                        {humanoidLoopHotspotTrendData?.summary || '当前窗口尚未形成清晰的病灶演化曲线。'}
                      </div>
                    </div>
                    {humanoidLoopHotspotTrendData?.latest_dominant_root_cause_code ? (
                      <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-700">
                        {`${humanoidLoopHotspotTrendData?.latest_dominant_organ_label || humanoidLoopHotspotTrendData?.latest_dominant_organ_system} · ${humanoidLoopHotspotTrendData.latest_dominant_root_cause_code}`}
                      </span>
                    ) : null}
                  </div>

                  {humanoidHotspotTrendLabels.length > 0 && humanoidHotspotTrendSeries.length > 0 ? (
                    <div className="mt-4">
                      <LineChart
                        title="器官病灶升温曲线"
                        xData={humanoidHotspotTrendLabels}
                        yUnit="req"
                        height={240}
                        series={humanoidHotspotTrendSeries.map((item, index) => ({
                          name: item.organ_label,
                          data: item.request_counts || [],
                          color: getHumanoidHotspotTrendColor(item.organ_system, index),
                        }))}
                      />
                      <div className="mt-3 flex flex-wrap gap-2">
                        {humanoidHotspotTrendBuckets
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
              ) : null}

              {humanoidLoopSourceCorrelationData ? (
                <div className="mt-4 rounded-2xl border border-amber-200 bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.08),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(59,130,246,0.08),_transparent_40%)] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-gray-600">源头归因 / 系统关联</div>
                      <div className="mt-1 text-sm text-gray-700">
                        {humanoidLoopSourceCorrelationData?.summary || '当前病灶已出现，但还没有形成稳定的源头归因。'}
                      </div>
                    </div>
                    {humanoidLoopSourceCorrelationData?.primary_source_label ? (
                      <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                        {`${humanoidLoopSourceCorrelationData.primary_source_label} · ${humanoidLoopSourceCorrelationData.primary_source_system || 'source'}`}
                      </span>
                    ) : null}
                  </div>

                  {humanoidSourceItems.length > 0 ? (
                    <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                      {humanoidSourceItems.map((item) => (
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
                              {`confidence ${formatPercent(item.confidence_pct)}`}
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
              ) : null}

              {humanoidHotspots.length > 0 ? (
                <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                  {humanoidHotspots.map((item) => (
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
                          {`${item.request_count} requests · ${formatPercent(item.share_pct)}`}
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
              ) : (
                <div className="mt-4">
                  <EmptyStateCard
                    title="暂无系统级病灶热区"
                    hint="当前窗口内虽然可能有单点异常，但还没有形成稳定聚类。"
                    className="p-4"
                  />
                </div>
              )}
            </div>
          ) : null}

          {humanoidLoopDiagnosticsData ? (
            <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/70 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-gray-600">异常回放摘要 / 根因归纳</div>
                  <div className="mt-1 text-sm text-gray-700">
                    {humanoidLoopDiagnosticsData?.summary || '当前暂无可展示的异常因果摘要。'}
                  </div>
                </div>
                <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600">
                  {`analysis ${humanoidLoopDiagnosticsData?.diagnosed_count ?? humanoidDiagnostics.length}`}
                </span>
              </div>

              {humanoidDiagnostics.length > 0 ? (
                <div className="mt-4 grid grid-cols-1 gap-3 xl:grid-cols-2">
                  {humanoidDiagnostics.map((item) => (
                    <div key={`${item.request_id}-${item.root_cause_code}`} className="rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${getHumanoidDiagnosticStatusClass(item.status)}`}>
                          {item.status}
                        </span>
                        <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                          {item.root_cause_code}
                        </span>
                        <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                          {item.suspected_breakpoint}
                        </span>
                        <span className="ml-auto font-mono text-[11px] text-gray-400">
                          {String(item.request_id || '').slice(0, 18)}
                        </span>
                      </div>

                      <div className="mt-3 text-sm font-semibold leading-6 text-gray-900">
                        {item.root_cause_summary}
                      </div>

                      <div className="mt-3 grid gap-3 xl:grid-cols-2">
                        <div className="rounded-xl border border-primary-100/70 bg-primary-50/50 px-3 py-2">
                          <div className="text-[11px] font-semibold text-gray-500">输入摘要</div>
                          <div className="mt-1 text-sm leading-6 text-gray-700">
                            {compactText(item.input_text || '暂无输入采样', 120)}
                          </div>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
                          <div className="text-[11px] font-semibold text-gray-500">输出摘要</div>
                          <div className="mt-1 text-sm leading-6 text-gray-700">
                            {compactText(item.output_text || '暂无输出采样', 120)}
                          </div>
                        </div>
                      </div>

                      {item.reason_hint ? (
                        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800">
                          {`reason_hint=${item.reason_hint}`}
                        </div>
                      ) : null}

                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        {(item.evidence_codes || []).slice(0, 4).map((code) => (
                          <span
                            key={`${item.request_id}-${code}`}
                            className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                          >
                            {code}
                          </span>
                        ))}
                        <Link
                          to={item.recommended_view_path}
                          className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                        >
                          {item.recommended_view_label || '打开异常回放'}
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-4">
                  <EmptyStateCard
                    title="暂无异常因果摘要"
                    hint="当前时间窗口内没有提取到可回放的代表性异常 request。"
                    className="p-4"
                  />
                </div>
              )}
            </div>
          ) : null}

          {humanoidRemediationSteps.length > 0 ? (
            <div className="mt-4 space-y-3">
              {humanoidRemediationSteps.slice(0, 6).map((step) => (
                <div key={step.step_id} className="rounded-2xl border border-primary-100/70 bg-white/88 p-4 shadow-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-[11px] font-semibold text-primary-700">
                      {`P${step.priority}`}
                    </span>
                    <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                      {step.category}
                    </span>
                    <div className="text-sm font-semibold text-gray-900">{step.title}</div>
                  </div>
                  <div className="mt-2 text-sm leading-6 text-gray-600">{step.detail}</div>
                  {step.operator_hint ? (
                    <div className="mt-2 text-xs text-gray-500">{step.operator_hint}</div>
                  ) : null}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {(step.evidence_codes || []).slice(0, 3).map((code) => (
                      <span
                        key={`${step.step_id}-${code}`}
                        className="rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700"
                      >
                        {code}
                      </span>
                    ))}
                    {step.target_path ? (
                      <Link
                        to={step.target_path}
                        className="ml-auto inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                      >
                        {step.target_label || '打开排查入口'}
                      </Link>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-4">
              <EmptyStateCard
                title="暂无自愈建议"
                hint="闭环健康时，这里会保持空白或仅给出预防性提示。"
                className="p-4"
              />
            </div>
          )}
        </div>
      ) : null}
    </LifeSection>
  )
}
