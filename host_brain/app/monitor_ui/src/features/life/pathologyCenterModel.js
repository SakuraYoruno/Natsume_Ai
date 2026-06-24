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

function hasActivePathologyFilter(selectedOrgan = 'ALL', selectedSeverity = 'ALL') {
  return selectedOrgan !== 'ALL' || selectedSeverity !== 'ALL'
}

function buildPathologyTimelinePath(pathology) {
  if (!pathology?.organId) return '/life/timeline'
  const params = new URLSearchParams()
  params.set('type', 'pathology')
  params.set('organ', pathology.organId)
  return `/life/timeline?${params.toString()}`
}

export function buildPathologyCenterCommandRailModel({
  hasPathologyPayload = false,
  hasPathologyError = false,
  pathologyRefreshWarning = null,
  allPathologies = [],
  filteredPathologies = [],
  selectedOrgan = 'ALL',
  selectedSeverity = 'ALL',
  primaryPathology = null,
  severitySummary = {},
  hottestOrgan = '暂无',
} = {}) {
  const activeFilter = hasActivePathologyFilter(selectedOrgan, selectedSeverity)
  const totalCount = allPathologies.length
  const visibleCount = filteredPathologies.length
  const leadPathology = filteredPathologies[0] || primaryPathology || null
  const criticalCount = Number(severitySummary.critical || 0)
  const warningCount = Number(severitySummary.warning || 0)

  const primaryAction = leadPathology
    ? {
      label: '进入主病灶',
      to: leadPathology.detailPath || `/life/pathologies/${encodeURIComponent(leadPathology.id || '')}`,
      detail: leadPathology.impactLabel || leadPathology.symptom || '先确认病灶影响范围。',
    }
    : activeFilter && totalCount > 0
      ? {
        label: '清除过滤',
        to: '/life/pathologies',
        detail: '当前过滤没有命中，先回到完整病灶窗口。',
      }
      : hasPathologyError
        ? {
          label: '检查观测总览',
          to: '/observability/overview',
          detail: '病灶聚合失败时，先恢复观测链路。',
        }
        : {
          label: '查看时间轴',
          to: '/life/timeline',
          detail: '当前没有稳定病灶，先沿事件链继续观察。',
        }

  const secondaryAction = activeFilter
    ? { label: '清除过滤', to: '/life/pathologies', detail: '回到完整病灶窗口。' }
    : leadPathology
      ? { label: '查看事件链', to: buildPathologyTimelinePath(leadPathology), detail: '沿生命时间轴确认病灶升温过程。' }
      : { label: '回到 Cockpit', to: '/cockpit', detail: '回到驾驶舱判断整体状态。' }

  const status = hasPathologyError
    ? 'blocked'
    : pathologyRefreshWarning
      ? 'degraded'
      : criticalCount || warningCount || leadPathology
        ? 'attention'
        : totalCount > 0 && visibleCount === 0
          ? 'degraded'
          : totalCount > 0
            ? 'healthy'
            : 'empty'

  const headline = hasPathologyError
    ? '病灶聚合不可读，先恢复观测链路'
    : leadPathology
      ? `先处理 ${leadPathology.organLabel || '未知器官'} · ${leadPathology.title}`
      : totalCount > 0
        ? '当前过滤没有命中，先放宽病灶范围'
        : '当前没有稳定病灶，继续观察事件链'

  const filterLabel = activeFilter
    ? `${selectedOrgan !== 'ALL' ? `organ=${selectedOrgan}` : '全部器官'} / ${selectedSeverity !== 'ALL' ? `severity=${selectedSeverity}` : '全部级别'}`
    : '全部病灶'

  const summary = leadPathology
    ? `${filterLabel} 下可见 ${visibleCount}/${totalCount} 个病灶。先确认主病灶影响，再沿事件链或器官网页继续追。`
    : totalCount > 0
      ? `原始窗口有 ${totalCount} 个病灶，但当前过滤无结果。先清除过滤或切换级别。`
      : '当前窗口还没有形成稳定异常簇。继续观察 runtime、gateway 和 forge 风险输入。'

  return {
    ariaLabel: 'Pathology Command Rail',
    eyebrow: 'Pathology workflow',
    title: '先处理哪个病灶',
    description: '首屏先回答：病灶聚合是否可信、过滤有没有命中、主病灶应该进入详情还是事件链。',
    status,
    headline,
    summary,
    primaryAction,
    secondaryAction,
    metricCards: [
      {
        key: 'filtered',
        label: '可见病灶',
        value: `${visibleCount}/${totalCount}`,
        detail: filterLabel,
        tone: visibleCount > 0 ? 'ready' : totalCount > 0 ? 'degraded' : 'blocked',
      },
      {
        key: 'high',
        label: '高风险',
        value: criticalCount,
        detail: '需要优先处理',
        tone: criticalCount ? 'blocked' : 'ready',
      },
      {
        key: 'warning',
        label: '波动病灶',
        value: warningCount,
        detail: '需要继续观察',
        tone: warningCount ? 'degraded' : 'ready',
      },
      {
        key: 'pathology',
        label: '最热器官',
        value: hottestOrgan,
        detail: leadPathology?.title || '当前暂无主病灶',
        tone: leadPathology ? 'degraded' : 'ready',
      },
    ],
    gates: [
      {
        label: '数据可信度',
        state: hasPathologyError ? 'blocked' : pathologyRefreshWarning ? 'warning' : hasPathologyPayload ? 'ready' : 'warning',
        detail: hasPathologyError ? '病灶接口失败，先看观测总览。' : pathologyRefreshWarning ? '当前显示旧病灶聚合。' : '病灶接口已返回。',
      },
      {
        label: '过滤命中',
        state: visibleCount > 0 ? 'ready' : totalCount > 0 ? 'warning' : 'blocked',
        detail: visibleCount > 0 ? `${visibleCount} 个病灶可继续追。` : totalCount > 0 ? '当前过滤无结果。' : '当前窗口没有病灶样本。',
      },
      {
        label: '主病灶',
        state: leadPathology ? 'warning' : 'ready',
        detail: leadPathology ? `${leadPathology.organLabel || '未知器官'}：${leadPathology.title}` : '当前没有主病灶。',
      },
      {
        label: '事件链',
        state: leadPathology ? 'ready' : 'warning',
        detail: leadPathology ? '可以进入详情或病灶事件链。' : '暂无可追踪病灶事件链。',
      },
    ],
  }
}
