import { resolveLoadState } from '../../models/loadState.js'

export const LIFE_TIMELINE_ALL_FILTER = 'ALL'

export const LIFE_TIMELINE_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export function getLifeTimelineFilterSelection(searchParams) {
  return {
    selectedType: String(searchParams.get('type') || LIFE_TIMELINE_ALL_FILTER),
    selectedOrgan: String(searchParams.get('organ') || LIFE_TIMELINE_ALL_FILTER),
    selectedRequestId: String(searchParams.get('request_id') || '').trim(),
    selectedSessionId: String(searchParams.get('session_id') || '').trim(),
    selectedTickId: String(searchParams.get('tick_id') || '').trim(),
  }
}

export function buildLifeTimelineQueryFilters({
  selectedType = LIFE_TIMELINE_ALL_FILTER,
  selectedOrgan = LIFE_TIMELINE_ALL_FILTER,
  selectedRequestId = '',
  selectedSessionId = '',
  selectedTickId = '',
} = {}) {
  return {
    eventType: selectedType !== LIFE_TIMELINE_ALL_FILTER ? selectedType : '',
    organ: selectedOrgan !== LIFE_TIMELINE_ALL_FILTER ? selectedOrgan : '',
    requestId: selectedRequestId,
    sessionId: selectedSessionId,
    tickId: selectedTickId,
  }
}

export function buildLifeTimelineFilterParams(searchParams, nextType, nextOrgan) {
  const params = new URLSearchParams(searchParams)
  if (nextType && nextType !== LIFE_TIMELINE_ALL_FILTER) {
    params.set('type', nextType)
  } else {
    params.delete('type')
  }
  if (nextOrgan && nextOrgan !== LIFE_TIMELINE_ALL_FILTER) {
    params.set('organ', nextOrgan)
  } else {
    params.delete('organ')
  }
  return params
}

function buildLifeTimelineCountOptions(items, keyField, labelField, allLabel) {
  const safeItems = Array.isArray(items) ? items : []
  const counts = new Map()
  safeItems.forEach((item) => {
    const key = item?.[keyField] || 'unknown'
    const label = item?.[labelField] || key
    const current = counts.get(key) || { key, label, count: 0 }
    current.count += 1
    counts.set(key, current)
  })
  return [{ key: LIFE_TIMELINE_ALL_FILTER, label: allLabel, count: safeItems.length }, ...Array.from(counts.values())]
}

export function buildLifeTimelineTypeOptions(items) {
  return buildLifeTimelineCountOptions(items, 'type', 'typeLabel', '全部事件')
}

export function buildLifeTimelineOrganOptions(items) {
  return buildLifeTimelineCountOptions(items, 'organId', 'organLabel', '全部器官')
}

export function countLifeTimelineEventTypes(items) {
  return new Set((Array.isArray(items) ? items : []).map((item) => item?.type).filter(Boolean)).size
}

export function filterLifeTimelineEvents({
  items = [],
  selectedType = LIFE_TIMELINE_ALL_FILTER,
  selectedOrgan = LIFE_TIMELINE_ALL_FILTER,
} = {}) {
  return (Array.isArray(items) ? items : []).filter((item) => {
    if (selectedType !== LIFE_TIMELINE_ALL_FILTER && item?.type !== selectedType) return false
    if (selectedOrgan !== LIFE_TIMELINE_ALL_FILTER && item?.organId !== selectedOrgan) return false
    return true
  })
}

export function buildLifeTimelineLoadState({
  timelineData,
  timelineLoadError = null,
  timelineItems = [],
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: timelineLoadError,
    hasData: Boolean(timelineData),
    hasSamples: timelineItems.length > 0,
    available: true,
    stale,
  })
}

export function buildLifeTimelineSampleMeta({
  overviewLoadState,
  timelineLoadError = null,
  timelineRefreshWarning = null,
  windowMinutes,
  overviewTrackedSections = 0,
  overviewUpdatedAt = null,
} = {}) {
  return {
    loadState: overviewLoadState,
    loadStateDetail: timelineLoadError || timelineRefreshWarning,
    windowMinutes,
    sampleSize: overviewTrackedSections,
    sampleLabel: '聚合投影',
    updatedAt: overviewUpdatedAt,
    source: '/obs/life-timeline',
    definition: '生命时间轴页现在直接消费 obs sidecar 的专用时间轴接口；当前已统一投影 request / bond / memory / tick / immune，但还不是最终统一事件总线。',
  }
}

export function buildLifeTimelineQueryView({
  timelineData,
  timelineError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  windowMinutes,
} = {}) {
  const timelineItems = Array.isArray(timelineData?.items) ? timelineData.items : []
  const hasTimelinePayload = Boolean(timelineData)
  const timelineLoadError = hasTimelinePayload ? null : timelineError
  const timelineRefreshWarning = hasTimelinePayload ? timelineError : null
  const overviewTrackedSections = hasTimelinePayload ? 1 : 0
  const overviewUpdatedAt = lastUpdatedAt || timelineData?.timestamp || null
  const overviewLoadState = buildLifeTimelineLoadState({
    timelineData,
    timelineLoadError,
    timelineItems,
    loading,
    refreshing,
    stale,
  })

  return {
    timelineItems,
    hasTimelinePayload,
    timelineLoadError,
    timelineRefreshWarning,
    primaryPathology: timelineData?.primaryPathology || null,
    primaryOrgan: timelineData?.primaryOrgan || null,
    overviewTrackedSections,
    overviewUpdatedAt,
    overviewLoadState,
    sampleMeta: buildLifeTimelineSampleMeta({
      overviewLoadState,
      timelineLoadError,
      timelineRefreshWarning,
      windowMinutes,
      overviewTrackedSections,
      overviewUpdatedAt,
    }),
  }
}

export function buildLifeTimelineHeroTitle({
  hasTimelineError = false,
  hasTimelineEvents = false,
  itemCount = 0,
} = {}) {
  if (hasTimelineError) return '生命事件暂不可用'
  if (hasTimelineEvents) return `最近 ${itemCount} 个生命事件`
  return '当前还没有可读生命事件'
}

export function buildLifeTimelineHeroMetaItems({
  eventTypeCount = 0,
  primaryPathology = null,
  primaryOrgan = null,
} = {}) {
  return [
    `事件类型：${eventTypeCount}`,
    `主病灶：${primaryPathology?.title || '暂无'}`,
    `最紧张器官：${primaryOrgan?.name || '暂无'}`,
  ]
}

export function buildLifeTimelineFocusNotice(kind, id) {
  if (!id) return null
  const labels = {
    request: 'request_id',
    session: 'session_id',
    tick: 'tick_id',
  }
  const label = labels[kind] || kind
  const prefix = label.replace('_id', '')
  const shortId = `${id.slice(0, 18)}${id.length > 18 ? '...' : ''}`
  return {
    title: `当前已按 ${label} 聚焦`,
    message: `正在只看 ${prefix} ${shortId} 的生命事件链。`,
  }
}

export function buildLifeTimelineLatestEventHint({
  selectedRequestId = '',
  selectedSessionId = '',
  selectedTickId = '',
  latestEvent = null,
} = {}) {
  if (selectedRequestId) return `request ${selectedRequestId.slice(0, 12)}...`
  if (selectedSessionId) return `session ${selectedSessionId.slice(0, 12)}...`
  if (selectedTickId) return `tick ${selectedTickId.slice(0, 12)}...`
  return latestEvent?.title || '暂无'
}

export function getLifeTimelineSelectedOrganCount(selectedOrgan, organOptions) {
  return selectedOrgan === LIFE_TIMELINE_ALL_FILTER ? Math.max((organOptions?.length || 1) - 1, 0) : 1
}

function hasActiveTimelineFilter({
  selectedType = LIFE_TIMELINE_ALL_FILTER,
  selectedOrgan = LIFE_TIMELINE_ALL_FILTER,
  selectedRequestId = '',
  selectedSessionId = '',
  selectedTickId = '',
} = {}) {
  return selectedType !== LIFE_TIMELINE_ALL_FILTER
    || selectedOrgan !== LIFE_TIMELINE_ALL_FILTER
    || Boolean(selectedRequestId || selectedSessionId || selectedTickId)
}

function buildTimelineFocusLabel({
  selectedType = LIFE_TIMELINE_ALL_FILTER,
  selectedOrgan = LIFE_TIMELINE_ALL_FILTER,
  selectedRequestId = '',
  selectedSessionId = '',
  selectedTickId = '',
} = {}) {
  if (selectedRequestId) return `request ${selectedRequestId.slice(0, 12)}`
  if (selectedSessionId) return `session ${selectedSessionId.slice(0, 12)}`
  if (selectedTickId) return `tick ${selectedTickId.slice(0, 12)}`
  const parts = []
  if (selectedType !== LIFE_TIMELINE_ALL_FILTER) parts.push(`type=${selectedType}`)
  if (selectedOrgan !== LIFE_TIMELINE_ALL_FILTER) parts.push(`organ=${selectedOrgan}`)
  return parts.length ? parts.join(' / ') : '全部事件'
}

export function buildLifeTimelineCommandRailModel({
  hasTimelinePayload = false,
  hasTimelineError = false,
  timelineRefreshWarning = null,
  timelineItems = [],
  filteredEvents = [],
  latestEvent = null,
  selectedType = LIFE_TIMELINE_ALL_FILTER,
  selectedOrgan = LIFE_TIMELINE_ALL_FILTER,
  selectedRequestId = '',
  selectedSessionId = '',
  selectedTickId = '',
  eventTypeCount = 0,
  organOptions = [],
  primaryPathology = null,
  primaryOrgan = null,
} = {}) {
  const activeFilter = hasActiveTimelineFilter({
    selectedType,
    selectedOrgan,
    selectedRequestId,
    selectedSessionId,
    selectedTickId,
  })
  const totalCount = timelineItems.length
  const visibleCount = filteredEvents.length
  const latestTitle = latestEvent?.title || '暂无最近事件'
  const focusLabel = buildTimelineFocusLabel({
    selectedType,
    selectedOrgan,
    selectedRequestId,
    selectedSessionId,
    selectedTickId,
  })

  const primaryAction = latestEvent?.to
    ? {
      label: latestEvent.linkLabel || '打开最近事件',
      to: latestEvent.to,
      detail: latestEvent.detail || '从最新生命事件进入对应详情或外部链路。',
    }
    : primaryPathology
      ? {
        label: '进入主病灶',
        to: primaryPathology.detailPath || `/life/pathologies/${encodeURIComponent(primaryPathology.id || '')}`,
        detail: primaryPathology.impactLabel || '先确认病灶影响范围。',
      }
      : hasTimelineError
        ? {
          label: '检查观测总览',
          to: '/observability/overview',
          detail: '时间轴不可读时，先恢复观测链路。',
        }
        : {
          label: '回到 Cockpit',
          to: '/cockpit',
          detail: '当前事件样本不足，回到驾驶舱判断整体状态。',
        }

  const secondaryAction = activeFilter
    ? { label: '清除过滤', to: '/life/timeline', detail: '回到完整生命事件窗口。' }
    : primaryPathology
      ? { label: '打开病灶中心', to: '/life/pathologies', detail: '横向比对异常模式。' }
      : { label: '查看运行态', to: '/life/runtime', detail: '确认 runtime 与 memory 接入。' }

  const status = hasTimelineError
    ? 'blocked'
    : timelineRefreshWarning
      ? 'degraded'
      : visibleCount > 0 && (primaryPathology || activeFilter)
        ? 'attention'
        : visibleCount > 0
          ? 'healthy'
          : totalCount > 0
            ? 'degraded'
            : 'empty'

  const headline = hasTimelineError
    ? '时间轴不可读，先恢复观测链路'
    : visibleCount > 0
      ? `先追 ${latestTitle}`
      : totalCount > 0
        ? '当前过滤条件过窄，先放宽事件窗口'
        : '等待 runtime、memory 或执行闭环形成生命事件'

  const summary = visibleCount > 0
    ? `当前视图聚焦 ${focusLabel}，可见 ${visibleCount}/${totalCount} 条事件。先用最近事件判断状态变化来源，再沿病灶或器官入口继续追。`
    : totalCount > 0
      ? `原始窗口有 ${totalCount} 条事件，但当前过滤没有命中。先清除过滤或切换器官范围。`
      : '当前窗口还没有稳定生命事件样本。更像是观测样本不足，而不是系统已经完全健康。'

  return {
    ariaLabel: 'Life Timeline Command Rail',
    eyebrow: 'Life Timeline workflow',
    title: '下一步追哪条链',
    description: '首屏先回答：时间轴是否可信、当前过滤有没有命中、最近事件能不能直接 drill-down。',
    status,
    headline,
    summary,
    primaryAction,
    secondaryAction,
    metricCards: [
      {
        key: 'filtered',
        label: '可见事件',
        value: `${visibleCount}/${totalCount}`,
        detail: activeFilter ? `当前聚焦 ${focusLabel}` : '完整时间窗口',
        tone: visibleCount > 0 ? 'ready' : totalCount > 0 ? 'degraded' : 'blocked',
      },
      {
        key: 'types',
        label: '事件类型',
        value: eventTypeCount,
        detail: selectedType === LIFE_TIMELINE_ALL_FILTER ? '全部类型' : `已选 ${selectedType}`,
        tone: eventTypeCount > 0 ? 'ready' : 'degraded',
      },
      {
        key: 'organs',
        label: '器官范围',
        value: getLifeTimelineSelectedOrganCount(selectedOrgan, organOptions),
        detail: selectedOrgan === LIFE_TIMELINE_ALL_FILTER ? '全部器官' : selectedOrgan,
        tone: organOptions.length > 1 ? 'ready' : 'degraded',
      },
      {
        key: 'latest',
        label: '最近事件',
        value: latestEvent?.typeLabel || '暂无',
        detail: latestTitle,
        tone: latestEvent ? 'ready' : 'degraded',
      },
    ],
    gates: [
      {
        label: '数据可信度',
        state: hasTimelineError ? 'blocked' : timelineRefreshWarning ? 'warning' : hasTimelinePayload ? 'ready' : 'warning',
        detail: hasTimelineError ? '时间轴接口失败，先看观测总览。' : timelineRefreshWarning ? '当前显示旧事件。' : '时间轴接口已返回。',
      },
      {
        label: '过滤命中',
        state: visibleCount > 0 ? 'ready' : totalCount > 0 ? 'warning' : 'blocked',
        detail: visibleCount > 0 ? `${visibleCount} 条事件可继续追。` : totalCount > 0 ? '当前过滤无结果。' : '当前窗口没有事件样本。',
      },
      {
        label: '病灶关联',
        state: primaryPathology ? 'warning' : 'ready',
        detail: primaryPathology ? `关联主病灶：${primaryPathology.title}` : '当前没有主病灶关联。',
      },
      {
        label: '首要入口',
        state: primaryAction?.to ? 'ready' : 'warning',
        detail: `${primaryAction.label}：${primaryAction.detail}`,
      },
    ],
  }
}
