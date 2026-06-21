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
