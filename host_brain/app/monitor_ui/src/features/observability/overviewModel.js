import { normalizeSubsystemStatus, SUBSYSTEM_STATUS_ORDER } from '../../shared/statusMeta.js'
import { resolveLoadState } from '../../models/loadState.js'

export function getSubsystemHeadline(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return '当前窗口无调用'
  if (subsystem?.detail) return subsystem.detail
  if (subsystem?.success_rate != null) return `成功率 ${subsystem.success_rate}%`
  return `当前窗口 ${subsystem?.total ?? 0} 次调用`
}

export function getSubsystemFootnote(subsystem) {
  if ((subsystem?.total ?? 0) <= 0) return '空闲'
  const parts = [`成功 ${subsystem?.success ?? 0}/${subsystem?.total ?? 0}`]
  if ((subsystem?.degraded ?? 0) > 0) parts.push(`降级 ${subsystem.degraded}`)
  if ((subsystem?.failed ?? 0) > 0) parts.push(`失败 ${subsystem.failed}`)
  if ((subsystem?.blocked ?? 0) > 0) parts.push(`拦截 ${subsystem.blocked}`)
  return parts.join(' · ')
}

export function getOverviewServiceGroups(services = []) {
  const items = Array.isArray(services) ? services : []
  return {
    edgeServices: items.filter((service) => service.service_type === 'edge'),
    llmNodes: items.filter((service) => service.service_type !== 'edge'),
  }
}

export function getActiveSubsystems(subsysData) {
  return [...(subsysData?.subsystems ?? [])]
    .filter((item) => (item?.total ?? 0) > 0)
    .map((item) => ({
      ...item,
      normalizedStatus: normalizeSubsystemStatus(item),
    }))
    .sort((a, b) => {
      const statusDelta = (SUBSYSTEM_STATUS_ORDER[a.normalizedStatus] ?? 99)
        - (SUBSYSTEM_STATUS_ORDER[b.normalizedStatus] ?? 99)
      if (statusDelta !== 0) return statusDelta
      return (b?.last_ts ?? 0) - (a?.last_ts ?? 0)
    })
}

export function buildOverviewPulseNodes({
  wsConnected,
  data,
  runningCount,
  uiAssertionData,
}) {
  return [
    { label: '中枢稳态', value: wsConnected ? '实时观测' : '轮询观测', tone: 'primary', active: true },
    { label: '外周器官', value: data?.up != null ? `${data.up}/${data.total ?? '—'}` : '—', tone: 'teal', active: true },
    { label: '节律推进', value: runningCount > 0 ? `${runningCount} 个节律在动` : '静息', tone: 'violet', active: runningCount > 0 },
    { label: '执行护栏', value: uiAssertionData?.block_rate != null ? `${uiAssertionData.block_rate}%` : '—', tone: 'amber', active: uiAssertionData?.block_rate != null },
  ]
}

export function buildOverviewHeroMetaItems({ data, activeSubsystemCount }) {
  return [
    `服务总数：${data?.total ?? '—'}`,
    `降级：${data?.degraded ?? '—'}`,
    `离线：${data?.down ?? '—'}`,
    `未知：${data?.unknown ?? '—'}`,
    `活跃子系统：${activeSubsystemCount}`,
  ]
}

export function buildOverviewLoadState({
  data,
  subsysData,
  agentLoopData,
  uiAssertionData,
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data || subsysData || agentLoopData || uiAssertionData),
    hasSamples: Boolean(
      (data?.services ?? []).length
      || (subsysData?.subsystems ?? []).length
      || (agentLoopData?.total_loops ?? 0) > 0
    ),
    available: true,
    stale,
  })
}

export function buildOverviewSampleMeta({
  overviewLoadState,
  windowMinutes,
  services = [],
  activeSubsystems = [],
  overviewUpdatedAt = null,
  wsConnected = false,
} = {}) {
  return {
    loadState: overviewLoadState,
    windowMinutes,
    sampleSize: services.length + activeSubsystems.length,
    sampleLabel: '观测对象',
    updatedAt: overviewUpdatedAt,
    source: wsConnected ? 'health websocket + overview queries' : 'overview queries',
    definition: 'Overview 保留实时健康快照，同时通过查询层统一拉取器官子系统、执行护栏与界面断言摘要。',
  }
}

function normalizeServiceStatus(service) {
  const status = String(service?.status || '').trim().toUpperCase()
  if (status === 'UP' || status === 'OK' || status === 'HEALTHY') return 'up'
  if (status === 'DEGRADED' || status === 'WARN' || status === 'WARNING') return 'degraded'
  if (status === 'DOWN' || status === 'FAIL' || status === 'FAILED' || status === 'ERROR') return 'down'
  return 'unknown'
}

function subsystemNeedsAttention(subsystem) {
  return subsystem?.normalizedStatus === 'down'
    || subsystem?.normalizedStatus === 'degraded'
    || Number(subsystem?.failed ?? 0) > 0
    || Number(subsystem?.degraded ?? 0) > 0
    || Number(subsystem?.blocked ?? 0) > 0
}

function buildOverviewHandoffEvidence({ data, activeSubsystems, agentLoopData, uiAssertionData, sampleMeta }) {
  const serviceCount = Number(data?.total ?? 0)
  const degradedServices = Number(data?.degraded ?? 0)
  const downServices = Number(data?.down ?? 0)
  const activeSubsystemCount = activeSubsystems.length
  const failedLoops = Number(agentLoopData?.failed ?? 0)
  const blockRate = Number(uiAssertionData?.block_rate ?? 0)
  const sampleSize = Number(sampleMeta?.sampleSize ?? 0)

  return [
    {
      key: 'health',
      label: 'Health',
      value: `${degradedServices + downServices} issue`,
      detail: `${serviceCount || '—'} services · ${degradedServices} degraded · ${downServices} down`,
      tone: downServices > 0 ? 'red' : degradedServices > 0 ? 'orange' : serviceCount > 0 ? 'green' : 'slate',
    },
    {
      key: 'trace',
      label: 'Trace',
      value: `${activeSubsystemCount} active`,
      detail: activeSubsystemCount > 0 ? `${activeSubsystems[0].display_name || activeSubsystems[0].subsystem} 是首个活跃子系统。` : '当前窗口还没有可追踪子系统样本。',
      tone: activeSubsystems.some((item) => item.normalizedStatus === 'down')
        ? 'red'
        : activeSubsystems.some((item) => subsystemNeedsAttention(item))
          ? 'orange'
          : activeSubsystemCount > 0 ? 'blue' : 'slate',
    },
    {
      key: 'guard',
      label: 'Guard',
      value: blockRate > 0 ? `${blockRate}% block` : `${failedLoops} failed`,
      detail: `agent loop failed ${failedLoops} · UI assertion ${uiAssertionData?.total_assertions ?? 0}`,
      tone: blockRate > 0 || failedLoops > 0 ? 'orange' : agentLoopData ? 'green' : 'slate',
    },
    {
      key: 'samples',
      label: 'Samples',
      value: `${sampleSize}`,
      detail: sampleMeta?.source || 'overview queries',
      tone: sampleSize > 0 ? 'green' : 'slate',
    },
  ]
}

function buildOverviewHandoffRoutes({ primaryKind, primaryService, primarySubsystem }) {
  return [
    {
      key: 'alerts',
      label: 'Alerts',
      to: '/observability/alerts',
      detail: primaryService ? `确认 ${primaryService.display_name || primaryService.service_id} 的告警和原因码。` : '查看服务健康和实时告警。',
      state: primaryKind === 'service' ? 'primary' : 'supporting',
      tone: primaryKind === 'service' ? 'orange' : 'slate',
    },
    {
      key: 'trace',
      label: 'Trace',
      to: '/observability/trace',
      detail: primarySubsystem ? `沿 ${primarySubsystem.display_name || primarySubsystem.subsystem} 追 request / node / stage。` : '从活跃 request 继续定位调用链。',
      state: primaryKind === 'subsystem' ? 'primary' : 'supporting',
      tone: primaryKind === 'subsystem' ? 'blue' : 'slate',
    },
    {
      key: 'metrics',
      label: 'Metrics',
      to: '/observability/metrics',
      detail: '用失败率、拒绝率和延迟确认影响面。',
      state: primaryKind === 'guard' ? 'primary' : 'supporting',
      tone: primaryKind === 'guard' ? 'orange' : 'slate',
    },
    {
      key: 'sessions',
      label: 'Sessions',
      to: '/observability/sessions',
      detail: '回看会话上下文和最近请求样本。',
      state: primaryKind === 'samples' ? 'primary' : 'supporting',
      tone: primaryKind === 'samples' ? 'green' : 'slate',
    },
  ]
}

export function buildOverviewEvidenceHandoff({
  data,
  activeSubsystems = [],
  agentLoopData,
  uiAssertionData,
  wsAlerts = [],
  sampleMeta,
} = {}) {
  const services = Array.isArray(data?.services) ? data.services : []
  const primaryService = services.find((service) => {
    const status = normalizeServiceStatus(service)
    return status === 'down' || status === 'degraded'
  }) || null
  const primarySubsystem = activeSubsystems.find((subsystem) => subsystem.normalizedStatus === 'down' || subsystem.normalizedStatus === 'degraded')
    || activeSubsystems.find((subsystem) => subsystemNeedsAttention(subsystem))
    || activeSubsystems[0]
    || null
  const degradedServices = Number(data?.degraded ?? 0)
  const downServices = Number(data?.down ?? 0)
  const failedLoops = Number(agentLoopData?.failed ?? 0)
  const blockRate = Number(uiAssertionData?.block_rate ?? 0)
  const alertCount = Number(wsAlerts?.length ?? 0)
  const sampleSize = Number(sampleMeta?.sampleSize ?? 0)

  let primaryKind = 'samples'
  let title = '观测链路稳定，保留样本交接'
  let detail = '当前窗口没有明显服务或执行护栏异常，可以从 Sessions 或 Trace 继续抽样确认。'
  let actionLabel = '查看会话样本'
  let actionTo = '/observability/sessions'
  let tone = 'green'

  if (primaryService || degradedServices > 0 || downServices > 0 || alertCount > 0) {
    primaryKind = 'service'
    tone = downServices > 0 ? 'red' : 'orange'
    title = primaryService
      ? `先确认 ${primaryService.display_name || primaryService.service_id}`
      : '先确认服务健康'
    detail = `当前 ${degradedServices} 个 degraded、${downServices} 个 down、实时告警 ${alertCount} 条；先进入 Alerts 收敛原因码，再按需要追 Trace。`
    actionLabel = '进入告警中心'
    actionTo = '/observability/alerts'
  } else if (blockRate > 0 || failedLoops > 0) {
    primaryKind = 'guard'
    tone = 'orange'
    title = '先确认执行护栏'
    detail = `agent loop failed ${failedLoops}，UI assertion block rate ${blockRate || 0}%；先看 Metrics 再回到 Trace 定位节点。`
    actionLabel = '查看指标与护栏'
    actionTo = '/observability/metrics'
  } else if (primarySubsystem) {
    primaryKind = 'subsystem'
    tone = primarySubsystem.normalizedStatus === 'up' ? 'blue' : 'orange'
    title = `沿 ${primarySubsystem.display_name || primarySubsystem.subsystem} 追链路`
    detail = `${primarySubsystem.total ?? 0} 次调用，${getSubsystemFootnote(primarySubsystem)}；从 Trace 进入 request / node / stage。`
    actionLabel = '进入链路 Trace'
    actionTo = '/observability/trace'
  }

  return {
    tone,
    primaryKind,
    title,
    detail,
    actionLabel,
    actionTo,
    issueCount: degradedServices + downServices + failedLoops + (blockRate > 0 ? 1 : 0) + alertCount,
    sampleSize,
    evidenceCards: buildOverviewHandoffEvidence({ data, activeSubsystems, agentLoopData, uiAssertionData, sampleMeta }),
    routeCards: buildOverviewHandoffRoutes({ primaryKind, primaryService, primarySubsystem }),
  }
}

export function buildOverviewQueryView({
  wsConnected = false,
  liveHealthData = null,
  healthData = null,
  liveHealthUpdatedAt = null,
  lastUpdatedAt = null,
  subsysData = null,
  agentLoopData = null,
  uiAssertionData = null,
  wsAlerts = [],
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
  windowMinutes,
} = {}) {
  const data = wsConnected && liveHealthData ? liveHealthData : (healthData || liveHealthData)
  const overviewUpdatedAt = Math.max(lastUpdatedAt || 0, liveHealthUpdatedAt || 0) || null
  const overviewLoadState = buildOverviewLoadState({
    data,
    subsysData,
    agentLoopData,
    uiAssertionData,
    loading,
    refreshing,
    error,
    stale,
  })
  const services = data?.services ?? []
  const serviceGroups = getOverviewServiceGroups(services)
  const activeSubsystems = getActiveSubsystems(subsysData)
  const runningCount = Number(agentLoopData?.running_count ?? 0)
  const pulseNodes = buildOverviewPulseNodes({
    wsConnected,
    data,
    runningCount,
    uiAssertionData,
  })
  const heroMetaItems = buildOverviewHeroMetaItems({
    data,
    activeSubsystemCount: activeSubsystems.length,
  })
  const sampleMeta = buildOverviewSampleMeta({
    overviewLoadState,
    windowMinutes,
    services,
    activeSubsystems,
    overviewUpdatedAt,
    wsConnected,
  })
  const evidenceHandoff = buildOverviewEvidenceHandoff({
    data,
    activeSubsystems,
    agentLoopData,
    uiAssertionData,
    wsAlerts,
    sampleMeta,
  })

  return {
    data,
    overviewUpdatedAt,
    overviewLoadState,
    services,
    edgeServices: serviceGroups.edgeServices,
    llmNodes: serviceGroups.llmNodes,
    activeSubsystems,
    runningCount,
    pulseNodes,
    heroMetaItems,
    sampleMeta,
    evidenceHandoff,
  }
}
