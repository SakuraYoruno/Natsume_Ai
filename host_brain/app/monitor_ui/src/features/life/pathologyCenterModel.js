import { resolveLoadState } from '../../models/loadState.js'

export function getPathologyFilterSelection(searchParams) {
  return {
    selectedOrgan: String(searchParams.get('organ') || 'ALL'),
    selectedSeverity: String(searchParams.get('severity') || 'ALL'),
  }
}

export function buildPathologyFilterParams(searchParams, nextOrgan, nextSeverity) {
  const params = new URLSearchParams(searchParams)
  if (nextOrgan && nextOrgan !== 'ALL') {
    params.set('organ', nextOrgan)
  } else {
    params.delete('organ')
  }
  if (nextSeverity && nextSeverity !== 'ALL') {
    params.set('severity', nextSeverity)
  } else {
    params.delete('severity')
  }
  return params
}

export function buildPathologyOrganOptions({
  allPathologies = [],
  organSystems = [],
  selectedOrgan,
}) {
  const counts = new Map()
  organSystems.forEach((item) => {
    counts.set(item.id, { key: item.id, label: item.name, count: 0 })
  })
  allPathologies.forEach((item) => {
    const current = counts.get(item.organId) || { key: item.organId, label: item.organLabel || item.organId, count: 0 }
    current.count += 1
    counts.set(item.organId, current)
  })
  const options = Array.from(counts.values()).filter((item) => item.count > 0 || item.key === selectedOrgan)
  return [{ key: 'ALL', label: '全部器官', count: allPathologies.length }, ...options]
}

export function buildPathologySeverityOptions(allPathologies = []) {
  const counts = new Map([
    ['critical', { key: 'critical', label: '高风险', count: 0 }],
    ['warning', { key: 'warning', label: '波动', count: 0 }],
    ['info', { key: 'info', label: '提示', count: 0 }],
  ])
  allPathologies.forEach((item) => {
    const current = counts.get(item.severity) || { key: item.severity, label: item.severity || '未分类', count: 0 }
    current.count += 1
    counts.set(item.severity, current)
  })
  return [{ key: 'ALL', label: '全部级别', count: allPathologies.length }, ...Array.from(counts.values())]
}

export function filterPathologiesBySelection({
  allPathologies = [],
  selectedOrgan,
  selectedSeverity,
}) {
  return allPathologies.filter((item) => {
    if (selectedOrgan !== 'ALL' && item.organId !== selectedOrgan) return false
    if (selectedSeverity !== 'ALL' && item.severity !== selectedSeverity) return false
    return true
  })
}

export function summarizePathologySeverity(pathologies = []) {
  return pathologies.reduce((acc, item) => {
    acc[item.severity] = (acc[item.severity] || 0) + 1
    acc[item.organLabel] = (acc[item.organLabel] || 0) + 0
    return acc
  }, {})
}

export function summarizePathologyOrgans(pathologies = []) {
  return pathologies.reduce((acc, item) => {
    acc[item.organLabel] = (acc[item.organLabel] || 0) + 1
    return acc
  }, {})
}

export function getHottestPathologyOrgan(organSummary) {
  return Object.entries(organSummary || {}).sort((left, right) => right[1] - left[1])[0]?.[0] || '暂无'
}

export function buildPathologyCenterHeroTitle({
  hasPathologyError,
  hasPathologies,
  filteredCount,
  hasAnyPathologies,
}) {
  if (hasPathologyError) return '病灶视图暂不可用'
  if (hasPathologies) return `${filteredCount} 个当前病灶`
  if (hasAnyPathologies) return '当前过滤条件下没有病灶'
  return '当前没有形成稳定病灶'
}

export function buildPathologyCenterHeroMetaItems(severitySummary, hottestOrgan) {
  return [
    `高风险：${severitySummary?.critical || 0}`,
    `波动：${severitySummary?.warning || 0}`,
    `最热器官：${hottestOrgan}`,
  ]
}

export function buildPathologyCenterLoadState({
  pathologyData,
  pathologyLoadError = null,
  allPathologies = [],
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: pathologyLoadError,
    hasData: Boolean(pathologyData),
    hasSamples: allPathologies.length > 0,
    available: true,
    stale,
  })
}

export function buildPathologyCenterSampleMeta({
  overviewLoadState,
  pathologyLoadError = null,
  pathologyRefreshWarning = null,
  windowMinutes,
  overviewTrackedSections = 0,
  overviewUpdatedAt = null,
} = {}) {
  return {
    loadState: overviewLoadState,
    loadStateDetail: pathologyLoadError || pathologyRefreshWarning,
    windowMinutes,
    sampleSize: overviewTrackedSections,
    sampleLabel: '病灶聚合',
    updatedAt: overviewUpdatedAt,
    source: '/obs/pathologies',
    definition: '病灶中心页现在直接消费 obs sidecar 的专用病灶接口，由后端统一汇总 runtime alerts、Gateway hotspots 与 Forge runtime 风险。',
  }
}

export function buildPathologyCenterQueryView({
  pathologyData,
  pathologyError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  windowMinutes,
} = {}) {
  const allPathologies = Array.isArray(pathologyData?.items) ? pathologyData.items : []
  const hasPathologyPayload = Boolean(pathologyData)
  const pathologyLoadError = hasPathologyPayload ? null : pathologyError
  const pathologyRefreshWarning = hasPathologyPayload ? pathologyError : null
  const overviewTrackedSections = hasPathologyPayload ? 1 : 0
  const overviewUpdatedAt = lastUpdatedAt || pathologyData?.timestamp || null
  const overviewLoadState = buildPathologyCenterLoadState({
    pathologyData,
    pathologyLoadError,
    allPathologies,
    loading,
    refreshing,
    stale,
  })

  return {
    allPathologies,
    hasPathologyPayload,
    pathologyLoadError,
    pathologyRefreshWarning,
    organSystems: Array.isArray(pathologyData?.organSystems) ? pathologyData.organSystems : [],
    primaryPathology: pathologyData?.primaryPathology || null,
    overviewTrackedSections,
    overviewUpdatedAt,
    overviewLoadState,
    sampleMeta: buildPathologyCenterSampleMeta({
      overviewLoadState,
      pathologyLoadError,
      pathologyRefreshWarning,
      windowMinutes,
      overviewTrackedSections,
      overviewUpdatedAt,
    }),
  }
}

export function buildPathologyDetailLoadState({
  pathologyData,
  pathologyLoadError = null,
  pathology = null,
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: pathologyLoadError,
    hasData: Boolean(pathologyData),
    hasSamples: Boolean(pathology),
    available: true,
    stale,
  })
}

export function buildPathologyDetailSampleMeta({
  overviewLoadState,
  pathologyLoadError = null,
  pathologyRefreshWarning = null,
  windowMinutes,
  overviewTrackedSections = 0,
  overviewUpdatedAt = null,
} = {}) {
  return {
    loadState: overviewLoadState,
    loadStateDetail: pathologyLoadError || pathologyRefreshWarning,
    windowMinutes,
    sampleSize: overviewTrackedSections,
    sampleLabel: '病灶详情',
    updatedAt: overviewUpdatedAt,
    source: '/obs/pathologies',
    definition: '病灶详情页现在直接消费 obs sidecar 的专用病灶接口，并由后端返回聚焦病灶、所属器官和相关生命事件。',
  }
}

export function buildPathologyDetailQueryView({
  pathologyData,
  pathologyError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  windowMinutes,
} = {}) {
  const availablePathologies = Array.isArray(pathologyData?.items) ? pathologyData.items : []
  const hasPathologyPayload = Boolean(pathologyData)
  const pathologyLoadError = hasPathologyPayload ? null : pathologyError
  const pathologyRefreshWarning = hasPathologyPayload ? pathologyError : null
  const pathology = pathologyData?.focusedItem || null
  const relatedOrgan = pathologyData?.focusedOrgan || null
  const relatedEvents = Array.isArray(pathologyData?.relatedEvents) ? pathologyData.relatedEvents : []
  const overviewTrackedSections = hasPathologyPayload ? 1 : 0
  const overviewUpdatedAt = lastUpdatedAt || pathologyData?.timestamp || null
  const overviewLoadState = buildPathologyDetailLoadState({
    pathologyData,
    pathologyLoadError,
    pathology,
    loading,
    refreshing,
    stale,
  })

  return {
    availablePathologies,
    hasPathologyPayload,
    pathologyLoadError,
    pathologyRefreshWarning,
    pathology,
    relatedOrgan,
    relatedEvents,
    overviewTrackedSections,
    overviewUpdatedAt,
    overviewLoadState,
    sampleMeta: buildPathologyDetailSampleMeta({
      overviewLoadState,
      pathologyLoadError,
      pathologyRefreshWarning,
      windowMinutes,
      overviewTrackedSections,
      overviewUpdatedAt,
    }),
  }
}
