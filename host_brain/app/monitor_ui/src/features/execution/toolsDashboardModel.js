import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { buildToolsOrganSystemModel } from '../../models/organSystemModel.js'
import { EXECUTION_TOOLS_COPY } from '../../shared/content/terminology.js'
import {
  buildToolExecutionGate,
  buildToolsHeroMetaItems,
  filterToolsBySideEffect,
  getPentestToolMetadataView,
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

function buildToolsMission({
  toolsLoadState,
  toolItems,
  filteredTools,
  selectedTool,
  readCount,
  writeCount,
  filter,
  query,
  catalogError,
}) {
  if (catalogError || toolsLoadState === LOAD_STATES.ERROR) {
    return {
      tone: 'red',
      title: '工具目录加载失败',
      detail: '普通工具和写入审批状态暂不能信任，先刷新或检查 Observability Backend。',
      action: '刷新工具目录',
    }
  }

  if (toolsLoadState === LOAD_STATES.LOADING || toolsLoadState === LOAD_STATES.REFRESHING) {
    return {
      tone: 'blue',
      title: '正在同步工具目录',
      detail: '保留当前页面结构，等待健康、目录和权限元数据返回后再判断执行路径。',
      action: '等待同步完成',
    }
  }

  if (!toolItems.length) {
    return {
      tone: 'gray',
      title: '暂无可执行工具',
      detail: '工具目录接口没有返回普通工具；Pentest 能力仍只在 Security Lab 侧运行。',
      action: '检查 provider 状态',
    }
  }

  if (selectedTool) {
    const gate = buildToolExecutionGate(selectedTool)
    return {
      tone: gate.tone,
      title: gate.title,
      detail: `${selectedTool.name || selectedTool.id}：${gate.detail}`,
      action: gate.canExecute ? '打开执行面板' : gate.label === 'security lab' ? '进入 Security Lab' : '补齐审批令牌',
    }
  }

  if (query || filter !== 'ALL') {
    return {
      tone: filteredTools.length ? 'amber' : 'orange',
      title: filteredTools.length ? '正在查看筛选结果' : '当前筛选没有命中工具',
      detail: `筛选结果 ${filteredTools.length}/${toolItems.length}。清除搜索或切回全部后可恢复完整目录判断。`,
      action: filteredTools.length ? '选择一个工具' : '清除筛选',
    }
  }

  if (writeCount > 0) {
    return {
      tone: 'amber',
      title: '目录可执行，写入工具需要审批',
      detail: `${readCount} 个只读工具可直接提交，${writeCount} 个写入工具必须提供 approval_token_ref。`,
      action: '先选择只读或写入工具',
    }
  }

  return {
    tone: 'green',
    title: '普通工具均可只读执行',
    detail: `${readCount} 个工具不声明写入副作用，可从详情面板发起人工执行。`,
    action: '选择工具执行',
  }
}

export function buildToolsCommandRailView({
  toolsLoadState,
  toolItems,
  filteredTools,
  selectedTool = null,
  readCount = 0,
  writeCount = 0,
  filter = 'ALL',
  query = '',
  catalogError = null,
  pentestCapabilities = null,
  pentestLoading = false,
  pentestError = '',
  runtimeSnapshot = null,
} = {}) {
  const pentestMeta = getPentestToolMetadataView(pentestCapabilities)
  const selectedGate = buildToolExecutionGate(selectedTool)
  const runtimeReady = Boolean(runtimeSnapshot)

  return {
    mission: buildToolsMission({
      toolsLoadState,
      toolItems,
      filteredTools,
      selectedTool,
      readCount,
      writeCount,
      filter,
      query,
      catalogError,
    }),
    gateCards: [
      {
        key: 'catalog',
        label: EXECUTION_TOOLS_COPY.catalogTitle,
        value: toolItems.length ? `${filteredTools.length}/${toolItems.length}` : '0 tools',
        detail: query || filter !== 'ALL' ? '筛选会影响下方列表，但不会改变全局 read/write 计数。' : '完整普通工具目录已进入执行台。',
        tone: toolsLoadState === LOAD_STATES.READY ? 'green' : toolsLoadState === LOAD_STATES.ERROR ? 'red' : 'blue',
        targetId: 'tools-catalog-workspace',
      },
      {
        key: 'detail',
        label: EXECUTION_TOOLS_COPY.detailTitle,
        value: selectedTool?.name || '未选择',
        detail: selectedTool ? selectedGate.detail : '选择工具后查看 schema、权限、标签和执行入口。',
        tone: selectedTool ? selectedGate.tone : 'gray',
        targetId: 'tools-detail-panel',
      },
      {
        key: 'pentest',
        label: EXECUTION_TOOLS_COPY.pentestTitle,
        value: pentestLoading && !pentestCapabilities ? '同步中' : `${pentestMeta.totalCount} tools`,
        detail: pentestError || `${pentestMeta.sideEffectCount} 个 side-effect 能力固定由 Security Lab 和 approval rail 接管。`,
        tone: pentestError ? 'orange' : pentestMeta.totalCount ? 'amber' : 'gray',
        targetId: 'tools-pentest-metadata',
      },
      {
        key: 'runtime',
        label: EXECUTION_TOOLS_COPY.runtimeTitle,
        value: runtimeReady ? '已接入' : '待同步',
        detail: runtimeReady ? '可展开查看工具器官与数字生命体运行态。' : '运行态未返回时不阻断普通工具目录浏览。',
        tone: runtimeReady ? 'blue' : 'gray',
        targetId: 'tools-runtime-context',
      },
    ],
  }
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
  selectedTool = null,
  pentestCapabilities = null,
  pentestLoading = false,
  pentestError = '',
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
    commandRailView: buildToolsCommandRailView({
      toolsLoadState,
      toolItems,
      filteredTools,
      selectedTool,
      readCount,
      writeCount,
      filter,
      query,
      catalogError,
      pentestCapabilities,
      pentestLoading,
      pentestError,
      runtimeSnapshot,
    }),
  }
}
