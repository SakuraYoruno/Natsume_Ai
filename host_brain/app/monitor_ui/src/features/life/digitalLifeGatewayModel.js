import { createRequestRefs } from '../../models/observabilityRefs.js'
import { formatPercent } from '../../shared/formatters.js'

export const formatGatewayPercent = (value) => formatPercent(value, 1, { placeholder: '--' })

export function toGatewayTrendIntervalMinutes(windowMinutes) {
  if (windowMinutes <= 60) return 5
  if (windowMinutes <= 360) return 30
  if (windowMinutes <= 1440) return 60
  return 360
}

export function getHumanoidTrendTone(status) {
  if (status === 'critical') return '#EF4444'
  if (status === 'degraded') return '#F97316'
  return '#10B981'
}

export function getHumanoidAlertBannerVariant(status) {
  if (status === 'critical') return 'error'
  if (status === 'degraded') return 'warning'
  return 'info'
}

export function getHumanoidDiagnosticStatusClass(status) {
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700'
  if (status === 'completed_without_loop') return 'border-orange-200 bg-orange-50 text-orange-700'
  if (status === 'in_progress') return 'border-blue-200 bg-blue-50 text-blue-700'
  if (status === 'closed_loop') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  return 'border-gray-200 bg-gray-50 text-gray-600'
}

export function getHumanoidHotspotSeverityClass(severity) {
  if (severity === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (severity === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

export function getHumanoidSourceImpactClass(impactLevel) {
  if (impactLevel === 'critical') return 'border-red-200 bg-red-50 text-red-700'
  if (impactLevel === 'warning') return 'border-orange-200 bg-orange-50 text-orange-700'
  return 'border-emerald-200 bg-emerald-50 text-emerald-700'
}

export function getHumanoidHotspotTrendColor(organSystem, index = 0) {
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

export function buildDigitalLifeGatewayView(model = {}) {
  const {
    windowMinutes,
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
  const humanoidTotalRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.total_gateway_requests ?? 0) : null
  const humanoidClosedLoopRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.closed_loop_requests ?? 0) : null
  const humanoidFailedRequests = hasHumanoidLoopData ? Number(humanoidLoopData?.failed_requests ?? 0) : 0
  const humanoidDeviationDetected = hasHumanoidLoopData ? Number(humanoidLoopData?.deviation_detected_requests ?? 0) : null
  const humanoidAlertStatus = String(humanoidLoopAlertsData?.status || '').toLowerCase()
  const humanoidTrendRequestSeries = humanoidLoopTrendData?.timeseries_total_gateway_requests || []

  return {
    hasHumanoidLoopData,
    humanoidClosedLoopRate: formatGatewayPercent(humanoidLoopData?.closed_loop_rate),
    humanoidReplyCommitRate: formatGatewayPercent(humanoidLoopData?.reply_commit_rate),
    humanoidAckTouchRate: formatGatewayPercent(humanoidLoopData?.ack_touch_rate),
    humanoidProgressTouchRate: formatGatewayPercent(humanoidLoopData?.progress_touch_rate),
    humanoidDeviationRecoveryRate: formatGatewayPercent(humanoidLoopData?.deviation_recovery_rate),
    humanoidTotalRequests,
    humanoidClosedLoopRequests,
    humanoidFailedRequests,
    humanoidDeviationDetected,
    humanoidClosedLoopHint: humanoidClosedLoopRequests != null && humanoidTotalRequests != null
      ? `${humanoidClosedLoopRequests}/${humanoidTotalRequests}`
      : '--/--',
    humanoidReplyCommitHint: hasHumanoidLoopData
      ? `Outbox ${humanoidLoopData?.reply_committed_requests ?? 0}`
      : 'Outbox --',
    humanoidAckHint: hasHumanoidLoopData
      ? `确认 ${humanoidLoopData?.ack_confirmed_requests ?? 0}`
      : '确认 --',
    humanoidProgressHint: hasHumanoidLoopData
      ? `触达 ${humanoidLoopData?.progress_touched_requests ?? 0}`
      : '触达 --',
    humanoidDeviationHint: humanoidDeviationDetected != null
      ? `检测 ${humanoidDeviationDetected}`
      : '检测 --',
    humanoidTrendLabels: humanoidLoopTrendData?.timeseries_labels || [],
    humanoidClosedLoopRateSeries: humanoidLoopTrendData?.timeseries_closed_loop_rate || [],
    humanoidReplyCommitRateSeries: humanoidLoopTrendData?.timeseries_reply_commit_rate || [],
    humanoidCompletionRateSeries: humanoidLoopTrendData?.timeseries_completion_rate || [],
    humanoidAckTouchRateSeries: humanoidLoopTrendData?.timeseries_ack_touch_rate || [],
    humanoidProgressTouchRateSeries: humanoidLoopTrendData?.timeseries_progress_touch_rate || [],
    humanoidTrendRequestSeries,
    humanoidTrendRequestTotal: humanoidTrendRequestSeries.reduce((sum, item) => sum + Number(item || 0), 0),
    humanoidTrendIntervalMinutes: toGatewayTrendIntervalMinutes(windowMinutes),
    humanoidAlertStatus,
    humanoidAlertSummary: humanoidLoopAlertsData?.summary || '',
    humanoidAlertItems: humanoidLoopAlertsData?.alerts || [],
    humanoidAlertBannerVariant: getHumanoidAlertBannerVariant(humanoidAlertStatus),
    humanoidTrendClosedLoopColor: getHumanoidTrendTone(humanoidAlertStatus),
    humanoidRemediationSteps: humanoidLoopRemediationData?.steps || [],
    humanoidRepresentativeRefs: createRequestRefs(humanoidLoopRemediationData?.representative_requests || []),
    humanoidDiagnostics: humanoidLoopDiagnosticsData?.diagnostics || [],
    humanoidHotspots: humanoidLoopHotspotsData?.hotspots || [],
    humanoidHotspotTrendLabels: humanoidLoopHotspotTrendData?.timeseries_labels || [],
    humanoidHotspotTrendSeries: humanoidLoopHotspotTrendData?.series || [],
    humanoidHotspotTrendBuckets: humanoidLoopHotspotTrendData?.dominant_buckets || [],
    humanoidSourceItems: humanoidLoopSourceCorrelationData?.items || [],
    humanoidFailureBannerVariant: humanoidFailedRequests > Math.max(2, Math.floor((humanoidTotalRequests || 0) * 0.2))
      ? 'warning'
      : 'info',
    humanoidCompletionRate: formatGatewayPercent(humanoidLoopData?.completion_rate),
  }
}
