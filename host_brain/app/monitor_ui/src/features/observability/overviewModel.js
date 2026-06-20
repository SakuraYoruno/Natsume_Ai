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

export function buildOverviewQueryView({
  wsConnected = false,
  liveHealthData = null,
  healthData = null,
  liveHealthUpdatedAt = null,
  lastUpdatedAt = null,
  subsysData = null,
  agentLoopData = null,
  uiAssertionData = null,
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
    sampleMeta: buildOverviewSampleMeta({
      overviewLoadState,
      windowMinutes,
      services,
      activeSubsystems,
      overviewUpdatedAt,
      wsConnected,
    }),
  }
}
