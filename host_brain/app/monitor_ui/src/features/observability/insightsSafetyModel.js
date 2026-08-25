function asArray(value) {
  return Array.isArray(value) ? value : []
}

function safeJsonPreview(value) {
  try {
    return JSON.stringify(value ?? {})
  } catch {
    return '[unserializable payload]'
  }
}

export function buildTribeFeedParams({
  eventTypeFilter = '',
  sourceFilter = '',
  targetFilter = '',
  limit = 20,
} = {}) {
  return {
    limit,
    event_type: String(eventTypeFilter || '').trim() || undefined,
    source_agent: String(sourceFilter || '').trim() || undefined,
    target_agent: String(targetFilter || '').trim() || undefined,
  }
}

export function buildTribeEventTypeSummary(items = [], limit = 4) {
  const eventTypeCounter = new Map()
  for (const event of asArray(items)) {
    const eventType = String(event?.event_type || 'unknown')
    eventTypeCounter.set(eventType, (eventTypeCounter.get(eventType) || 0) + 1)
  }

  return {
    eventTypeCount: eventTypeCounter.size,
    topEventTypes: Array.from(eventTypeCounter.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, limit)
      .map(([eventType, count]) => ({ eventType, count })),
  }
}

export function buildTribeEventRows(items = []) {
  return asArray(items).map((event, index) => ({
    key: event?.event_id || `${event?.event_type || 'tribe'}-${event?.ts_ms || index}`,
    tsMs: event?.ts_ms,
    eventType: event?.event_type || '—',
    sourceAgent: event?.source_agent || '—',
    targetAgent: event?.target_agent || '—',
    payloadPreview: safeJsonPreview(event?.payload),
  }))
}

export function buildTribeStatCards({
  data = {},
  loading = false,
  windowMinutes,
  eventTypeCount = 0,
  latestEventTime = '—',
} = {}) {
  return [
    { label: '最近事件数', value: loading ? '...' : (data?.total ?? '—'), color: 'text-gray-700' },
    { label: '窗口分钟', value: windowMinutes, color: 'text-gray-700' },
    { label: '事件类型数', value: eventTypeCount, color: 'text-blue-600' },
    { label: '最近事件时间', value: latestEventTime, color: 'text-gray-600' },
  ]
}

export function buildPermissionStatCards(data = {}) {
  return [
    { label: '总校验次数', value: data?.total_checks ?? '—', color: 'text-gray-700' },
    { label: '允许次数', value: data?.allowed_count ?? '—', color: 'text-green-600' },
    { label: '拒绝次数', value: data?.denied_count ?? '—', color: 'text-red-600' },
    { label: '节律拒绝', value: data?.scheduler_denied_count ?? '—', color: 'text-orange-500' },
  ]
}

export function buildPermissionDeniedRows(data = {}) {
  return asArray(data?.top_denied_tools).map((tool) => ({
    ...tool,
    key: tool?.tool_name || 'unknown_tool',
    barWidth: `${tool?.pct ?? 0}%`,
  }))
}

export function buildSchedulerGateStatCards(data = {}) {
  return [
    { label: '门控拦截', value: data?.gate_blocked_total ?? '—', color: 'text-orange-500' },
    {
      label: '门控拦截率',
      value: data?.gate_blocked_rate != null ? `${data.gate_blocked_rate}%` : '—',
      color: (data?.gate_blocked_rate ?? 0) > 25 ? 'text-red-500' : 'text-green-600',
    },
    { label: '推进中任务', value: data?.running_count ?? '—', color: 'text-blue-600' },
    { label: '异常任务', value: data?.failed_count ?? '—', color: 'text-red-600' },
  ]
}

export function buildAutomationGatePath(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    const normalized = String(value || '').trim()
    if (normalized) search.set(key, normalized)
  })
  const query = search.toString()
  return `/execution/automation${query ? `?${query}` : ''}`
}

export function buildMemoryGuardStatCards(data = {}) {
  return [
    { label: '总校验次数', value: data?.total_checks ?? '—', color: 'text-gray-700' },
    { label: '通过次数', value: data?.passed_count ?? '—', color: 'text-green-600' },
    { label: '拦截次数', value: data?.blocked_count ?? '—', color: 'text-red-600' },
    {
      label: '拦截率',
      value: data?.block_rate != null ? `${data.block_rate}%` : '—',
      color: data?.block_rate > 20 ? 'text-red-500' : 'text-green-600',
    },
  ]
}

export function buildMemoryGuardSecondaryStatCards(data = {}) {
  return [
    { label: '仅叙事', value: data?.narrative_only_count ?? '—', color: 'text-orange-500' },
    { label: '梦境拦截', value: data?.dream_blocked_count ?? '—', color: 'text-purple-500' },
    { label: '蒸馏资格', value: data?.distill_eligible_count ?? '—', color: 'text-blue-600' },
  ]
}

export function buildMemoryRejectReasonRows(data = {}) {
  return asArray(data?.top_reject_reasons).map((reason) => ({
    ...reason,
    key: reason?.reason || 'unknown_reason',
    barWidth: `${reason?.pct ?? 0}%`,
  }))
}

export function buildQaViolationStatCards(qaData = {}) {
  return [
    {
      label: '违规总数',
      value: qaData?.total_violations ?? '—',
      color: qaData?.total_violations > 0 ? 'text-red-500' : 'text-green-600',
    },
    {
      label: '降级次数',
      value: qaData?.degraded_count ?? '—',
      color: qaData?.degraded_count > 0 ? 'text-orange-500' : 'text-green-600',
    },
    {
      label: '降级率',
      value: qaData?.degrade_rate != null ? `${qaData.degrade_rate}%` : '—',
      color: qaData?.degrade_rate > 30
        ? 'text-red-500'
        : (qaData?.degrade_rate > 10 ? 'text-orange-500' : 'text-green-600'),
    },
    {
      label: '重写成功率',
      value: qaData?.rewrite_success_rate != null ? `${qaData.rewrite_success_rate}%` : '—',
      color: qaData?.rewrite_success_rate >= 80 ? 'text-green-600' : 'text-orange-500',
    },
  ]
}
