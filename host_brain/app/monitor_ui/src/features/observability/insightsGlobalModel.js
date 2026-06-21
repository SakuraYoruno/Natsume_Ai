import { resolveInsightsChainNodeColor } from './components/insights/insightsVisualMeta.js'

export const INSIGHTS_HEATMAP_HOURS = Array.from({ length: 24 }, (_, index) => index)

export function buildFailureStatCards(data = {}) {
  return [
    { label: '总失败事件', value: data?.total_failures ?? '—', color: 'text-gray-700' },
    { label: '中止次数', value: data?.abort_count ?? '—', color: 'text-red-600' },
    { label: '降级次数', value: data?.degraded_count ?? '—', color: 'text-orange-500' },
    {
      label: '中止率',
      value: data?.abort_rate != null ? `${data.abort_rate}%` : '—',
      color: data?.abort_rate > 30 ? 'text-red-500' : 'text-green-600',
    },
  ]
}

export function buildFailureEventRows(data = {}) {
  return (data?.top_events ?? []).map((item) => ({
    ...item,
    barWidth: `${item.pct}%`,
  }))
}

export function buildChainStatCards(data = {}) {
  return [
    { label: '总请求数', value: data?.total_requests ?? '—', color: 'text-gray-700' },
    {
      label: '入口守护拦截率',
      value: data?.governor_block_rate != null ? `${data.governor_block_rate}%` : '—',
      color: data?.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700',
    },
    { label: '记忆写入次数', value: data?.episodic_written_count ?? '—', color: 'text-green-600' },
    { label: '主设定提案次数', value: data?.canon_proposal_count ?? '—', color: 'text-indigo-600' },
  ]
}

export function buildChainNodeLatencyRows(nodes = []) {
  const activeNodes = (Array.isArray(nodes) ? nodes : []).filter((node) => (node?.total_calls ?? 0) > 0)
  const maxAvg = Math.max(...activeNodes.map((node) => node?.avg_latency_ms ?? 0), 1)
  return activeNodes.map((node) => {
    const avgPct = maxAvg > 0 ? Math.max(((node.avg_latency_ms ?? 0) / maxAvg) * 100, 1) : 0
    return {
      key: node.node_name,
      nodeName: node.node_name,
      barWidth: `${avgPct}%`,
      barColor: resolveInsightsChainNodeColor(node.node_name),
      latencyLabel: node.avg_latency_ms != null ? `avg ${node.avg_latency_ms}ms` : '—',
      p95Label: node.p95_latency_ms != null ? ` / p95 ${node.p95_latency_ms}ms` : '',
      degradedLabel: node.degraded_rate > 0 ? `${node.degraded_rate}% 降级` : '',
      timeoutLabel: node.timeout_count > 0 ? `${node.timeout_count} 超时` : '',
    }
  })
}

export function buildCapabilityGapStatCards(data = {}) {
  return [
    { label: 'Gap Detected', value: data?.detected_total ?? '--', color: 'text-gray-700' },
    { label: 'Gap Failed', value: data?.failed_total ?? '--', color: 'text-red-600' },
    { label: 'Runtime Caps', value: data?.active_runtime_capabilities ?? '--', color: 'text-violet-600' },
    { label: 'Forge Candidates', value: (data?.forge_candidates ?? []).length, color: 'text-blue-600' },
  ]
}

export function buildForgeQualityStatCards(qualitySummary = {}) {
  return [
    { label: 'Tracked Props', value: qualitySummary?.tracked_proposals ?? 0, color: 'text-gray-700' },
    { label: 'Unhealthy', value: qualitySummary?.unhealthy_proposals ?? 0, color: 'text-orange-500' },
    { label: 'Critical', value: qualitySummary?.critical_proposals ?? 0, color: 'text-red-600' },
    {
      label: 'Avg Health',
      value: qualitySummary?.avg_health_score != null ? `${Number(qualitySummary.avg_health_score).toFixed(1)}` : '--',
      color: Number(qualitySummary?.avg_health_score ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500',
    },
  ]
}

export function buildCapabilityGapHotspotRows(data = {}) {
  return (data?.hotspots ?? []).slice(0, 6).map((item) => ({
    ...item,
    failureRateLabel: `fail ${(Number(item.failure_rate || 0) * 100).toFixed(1)}%`,
    detail: `family=${item.suggested_family} | detected=${item.detected_count} | failed=${item.failed_count}`,
  }))
}

export function getWorstForgeRuntimeQualityItems(items = []) {
  return [...(Array.isArray(items) ? items : [])]
    .sort((left, right) => Number(left?.health_score ?? 0) - Number(right?.health_score ?? 0))
    .slice(0, 5)
}

export function buildForgeRuntimeQualityRows(items = []) {
  return getWorstForgeRuntimeQualityItems(items).map((item) => {
    const health = Number(item.health_score || 0)
    return {
      key: item.proposal_id,
      proposalLabel: (item.proposal_id || '').slice(0, 18),
      stageLabel: String(item.stage || '').toUpperCase(),
      healthLabel: health.toFixed(1),
      healthBarClass: health >= 75 ? 'bg-green-400' : health >= 55 ? 'bg-orange-400' : 'bg-red-400',
      healthBarWidth: `${Math.max(3, Math.min(100, health))}%`,
      detail: `fail=${(Number(item.window_failure_rate || 0) * 100).toFixed(1)}% | p95=${item.window_p95_latency_ms ?? 0}ms | tune=${item.last_tune_action || 'NONE'}`,
    }
  })
}

function buildCellMap(cells = []) {
  return (Array.isArray(cells) ? cells : []).reduce((acc, cell) => {
    if (!acc[cell.hour]) acc[cell.hour] = {}
    acc[cell.hour][cell.node_name] = cell.error_count
    return acc
  }, {})
}

function redCellBackground(value, maxValue, minimumIntensity = 0.15) {
  if (value <= 0) return 'rgba(243, 244, 246, 1)'
  const intensity = Math.max(value / maxValue, minimumIntensity)
  return `rgba(239, 68, 68, ${intensity})`
}

export function buildErrorHeatmapView(data = {}) {
  const cells = data?.cells ?? []
  const nodes = data?.nodes ?? []
  const hourlyTotals = data?.hourly_totals ?? []
  const cellMap = buildCellMap(cells)
  const maxCellCount = Math.max(...cells.map((cell) => cell.error_count), 1)
  const nodeTotals = nodes
    .map((node) => ({
      node,
      total: INSIGHTS_HEATMAP_HOURS.reduce((sum, hour) => sum + (cellMap[hour]?.[node] ?? 0), 0),
    }))
    .filter((item) => item.total > 0)
    .sort((left, right) => right.total - left.total)
  const maxNodeTotal = Math.max(...nodeTotals.map((item) => item.total), 1)
  const maxHourlyTotal = Math.max(...hourlyTotals.map((item) => item.total ?? 0), 1)

  return {
    nodes,
    hours: INSIGHTS_HEATMAP_HOURS,
    nodeTotals,
    mobileHourCells: INSIGHTS_HEATMAP_HOURS.map((hour) => {
      const total = hourlyTotals.find((item) => item.hour === hour)?.total ?? 0
      return {
        hour,
        total,
        backgroundColor: total > 0
          ? redCellBackground(total, maxHourlyTotal, 0.18)
          : 'rgba(249, 250, 251, 1)',
        valueClassName: total > 0 ? 'text-red-600' : 'text-gray-300',
      }
    }),
    mobileNodeTotals: nodeTotals.slice(0, 5).map((item) => ({
      ...item,
      barWidth: `${Math.max(5, (item.total / maxNodeTotal) * 100)}%`,
    })),
    desktopRows: nodes.slice(0, 15).map((node) => ({
      node,
      cells: INSIGHTS_HEATMAP_HOURS.map((hour) => {
        const count = cellMap[hour]?.[node] ?? 0
        return {
          hour,
          count,
          title: count > 0 ? `${node} @ ${hour}:00 - ${count} 次错误` : '',
          backgroundColor: redCellBackground(count, maxCellCount),
        }
      }),
    })),
    hourlyTotalCells: INSIGHTS_HEATMAP_HOURS.map((hour) => {
      const total = hourlyTotals.find((item) => item.hour === hour)?.total ?? 0
      return {
        hour,
        total,
        className: total > 0 ? 'text-red-500' : 'text-gray-300',
        label: total > 0 ? total : '·',
      }
    }),
    legendCells: [0.15, 0.35, 0.55, 0.75, 1.0].map((value) => ({
      value,
      backgroundColor: `rgba(239, 68, 68, ${value})`,
    })),
  }
}

export function buildGovernorDecisionStatCards(govData = {}) {
  return [
    { label: '总决策数', value: govData?.total, color: undefined },
    { label: '批准', value: govData?.approved, color: 'text-green-600' },
    { label: '拒绝', value: govData?.rejected, color: 'text-red-500' },
    {
      label: '拒绝率',
      value: govData?.rejection_rate != null ? `${govData.rejection_rate}%` : '—',
      color: govData?.rejection_rate > 20
        ? 'text-red-500'
        : (govData?.rejection_rate > 10 ? 'text-orange-500' : 'text-green-600'),
    },
  ]
}

export function buildGlobalErrorStatCards(errData = {}, humanizeToken = (value) => value) {
  return [
    {
      label: '错误总数',
      value: errData?.total_errors,
      color: errData?.total_errors > 0 ? 'text-red-500' : 'text-green-600',
    },
    ...(errData?.stages ?? []).slice(0, 3).map((stage) => ({
      label: humanizeToken(stage.stage),
      value: stage.count,
      sub: `${stage.pct}%`,
      color: 'text-orange-500',
      key: stage.stage,
    })),
  ]
}
