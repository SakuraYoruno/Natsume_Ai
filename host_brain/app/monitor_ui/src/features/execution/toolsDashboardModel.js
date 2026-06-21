import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildToolsOrganSystemModel } from '../../models/organSystemModel.js'
import {
  buildToolsHeroMetaItems,
  filterToolsBySideEffect,
  getToolsCatalogCounts,
} from './toolsCatalogModel.js'

export function buildToolsLoadState({
  health = null,
  tools = [],
  catalogError = null,
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  const toolItems = Array.isArray(tools) ? tools : []
  const hasCatalogData = Boolean(health || toolItems.length > 0)
  return resolveLoadState({
    loading: loading || refreshing,
    error: catalogError,
    hasData: hasCatalogData,
    hasSamples: Number(health?.catalog_size ?? 0) > 0 || toolItems.length > 0,
    available: hasCatalogData,
    stale,
  })
}

export function buildToolsSampleMeta({
  toolsLoadState,
  health = null,
  tools = [],
  lastUpdatedAt = null,
} = {}) {
  return {
    loadState: toolsLoadState,
    sampleSize: health?.catalog_size ?? (Array.isArray(tools) ? tools.length : 0),
    sampleLabel: '工具',
    updatedAt: lastUpdatedAt,
    source: 'tool catalog',
    definition: '工具页把工具目录、权限拦截与执行计划拆成独立观测面；搜索为空结果不会被误判成系统无样本。',
  }
}

export function buildToolsNoticeView({
  catalogError = null,
  loading = false,
  refreshing = false,
  toolsLoadState,
} = {}) {
  const quiet = !catalogError && !loading && !refreshing
  return {
    catalogError,
    showUnavailable: quiet && toolsLoadState === LOAD_STATES.UNAVAILABLE,
    showNoSample: quiet && toolsLoadState === LOAD_STATES.NO_SAMPLE,
  }
}

export function buildToolsOrganModel({
  toolsLoadState,
  health = null,
  tools = [],
  readCount = 0,
  writeCount = 0,
  filter = 'ALL',
  query = '',
  lastUpdatedAt = null,
  runtimeSnapshot = null,
} = {}) {
  return buildToolsOrganSystemModel({
    loadState: toolsLoadState,
    updatedAt: lastUpdatedAt,
    healthStatus: health?.status,
    catalogSize: health?.catalog_size ?? (Array.isArray(tools) ? tools.length : 0),
    readCount,
    writeCount,
    filter,
    query,
    runtimeSummary: runtimeSnapshot?.runtime_summary?.headline,
  })
}

export function buildToolsHealthMetricCards({ health } = {}) {
  if (!health) return []
  return [
    {
      label: '引擎状态',
      value: health.status,
      color: health.status === 'OK' ? 'text-green-600' : 'text-yellow-600',
    },
    { label: '工具总数', value: health.catalog_size, color: 'text-gray-800' },
    { label: '只读工具', value: health.read_tools, color: 'text-green-600' },
    { label: '写入工具', value: health.write_tools, color: 'text-orange-600' },
  ]
}

export function buildToolsDashboardViewModel({
  health = null,
  tools = [],
  catalogError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  filter = 'ALL',
  query = '',
  runtimeSnapshot = null,
} = {}) {
  const toolItems = Array.isArray(tools) ? tools : []
  const filteredTools = filterToolsBySideEffect(toolItems, filter)
  const { readCount, writeCount } = getToolsCatalogCounts(toolItems)
  const toolsLoadState = buildToolsLoadState({
    health,
    tools: toolItems,
    catalogError,
    loading,
    refreshing,
    stale,
  })

  return {
    filteredTools,
    readCount,
    writeCount,
    toolsLoadState,
    toolsOrganModel: buildToolsOrganModel({
      toolsLoadState,
      health,
      tools: toolItems,
      readCount,
      writeCount,
      filter,
      query,
      lastUpdatedAt,
      runtimeSnapshot,
    }),
    heroView: {
      title: health?.status === 'OK' ? '工具引擎健康' : '工具引擎待关注',
      metaItems: buildToolsHeroMetaItems({
        health,
        tools: toolItems,
        readCount,
        writeCount,
        filter,
      }),
    },
    sampleMeta: buildToolsSampleMeta({
      toolsLoadState,
      health,
      tools: toolItems,
      lastUpdatedAt,
    }),
    noticeView: buildToolsNoticeView({
      catalogError,
      loading,
      refreshing,
      toolsLoadState,
    }),
    healthMetricCards: buildToolsHealthMetricCards({ health }),
  }
}
