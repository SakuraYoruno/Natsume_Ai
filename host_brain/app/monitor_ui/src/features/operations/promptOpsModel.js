import { formatDateTime } from '../../shared/formatters.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { OPERATIONS_PROMPT_COPY } from '../../shared/content/terminology.js'
import {
  PROMPT_CONTENT_TYPE_BADGE_CLASSES,
  PROMPT_CONTENT_TYPE_LABELS,
  PROMPT_SOURCE_BADGE_CLASSES,
  PROMPT_SOURCE_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../shared/statusMeta.js'

export function getPromptFiltersFromSearchParams(searchParams) {
  return {
    keyword: String(searchParams.get('keyword') || ''),
    scope: String(searchParams.get('scope') || ''),
    category: String(searchParams.get('category') || ''),
    source: String(searchParams.get('source') || ''),
  }
}

export function buildPromptFilterOptions(items = [], field) {
  return [...new Set((Array.isArray(items) ? items : []).map((item) => item?.[field]).filter(Boolean))]
}

export function buildPromptTemplateSummary(items = []) {
  const safeItems = Array.isArray(items) ? items : []
  return {
    total: safeItems.length,
    override: safeItems.filter((item) => item?.source === 'override').length,
    json: safeItems.filter((item) => item?.content_type === 'json').length,
    guarded: safeItems.filter((item) => {
      const category = String(item?.category || '')
      return category.includes('fallback') || category.includes('guard')
    }).length,
  }
}

export function buildPromptTemplateListMetaState({
  templatesData,
  listError,
  listLoading = false,
  listRefreshing = false,
  listStale = false,
} = {}) {
  return resolveLoadState({
    loading: listLoading || listRefreshing,
    error: listError,
    hasData: Boolean(templatesData),
    hasSamples: true,
    available: Boolean(templatesData),
    stale: listStale,
  })
}

export function buildPromptTemplateSampleMeta({
  listMetaState,
  summary,
  listUpdatedAt,
} = {}) {
  return {
    loadState: listMetaState,
    sampleSize: summary?.total ?? 0,
    sampleLabel: '模板',
    updatedAt: listUpdatedAt,
    source: 'prompt-config',
    definition: '提示模板页当前是只读观测台：展示版本化 PromptSpec 与当前生效值；不再提供前端编辑入口。',
  }
}

function hasActivePromptFilters(filters = {}) {
  return Boolean(
    String(filters.keyword || '').trim()
      || String(filters.scope || '').trim()
      || String(filters.category || '').trim()
      || String(filters.source || '').trim(),
  )
}

function buildPromptDecisionMission({
  filters = {},
  listMetaState,
  listError,
  listLoading = false,
  listRefreshing = false,
  items = [],
  summary,
  selected,
  detailError,
  detailLoading = false,
  detailRefreshing = false,
}) {
  const filtered = hasActivePromptFilters(filters)
  const itemCount = Array.isArray(items) ? items.length : 0

  if (listError || listMetaState === LOAD_STATES.ERROR) {
    return {
      tone: 'red',
      title: 'PromptSpec 目录不可用',
      detail: '模板目录加载失败，不能判断当前生效模板。先刷新视图或检查 Host Main。',
      action: '刷新模板目录',
    }
  }

  if (listLoading || listRefreshing || listMetaState === LOAD_STATES.LOADING || listMetaState === LOAD_STATES.REFRESHING) {
    return {
      tone: 'blue',
      title: '正在同步 PromptSpec 目录',
      detail: '列表和详情会在数据返回后自动切换；同步期间不要把空列表当成无模板。',
      action: '等待同步完成',
    }
  }

  if (!itemCount) {
    return {
      tone: filtered ? 'orange' : 'gray',
      title: filtered ? '当前筛选没有命中模板' : '暂无 PromptSpec 样本',
      detail: filtered ? '清除 keyword、scope、category 或 source 后再判断全局模板状态。' : 'Host Main 当前没有返回模板目录。',
      action: filtered ? '清除筛选' : '检查 PromptSpec 接入',
    }
  }

  if (detailError) {
    return {
      tone: 'orange',
      title: '列表可读，详情降级',
      detail: '当前模板详情加载失败；仍可用左侧目录确认 scope/category/source。',
      action: '刷新模板详情',
    }
  }

  if (detailLoading || detailRefreshing) {
    return {
      tone: 'blue',
      title: '正在同步模板详情',
      detail: '目录已返回，等待选中模板的 default/resolved 内容同步。',
      action: '等待详情同步',
    }
  }

  if (selected?.has_override) {
    return {
      tone: 'amber',
      title: '当前模板存在 runtime override',
      detail: `${selected.template_id} 的实际生效内容可能不同于 YAML 默认值，需要先核对 override。`,
      action: '核对 override 差异',
    }
  }

  if (selected && String(selected.category || '').match(/fallback|guard/i)) {
    return {
      tone: 'amber',
      title: '正在查看守卫/兜底模板',
      detail: `${selected.template_id} 属于 ${selected.category}，建议同时复核 resolved 内容和调用场景。`,
      action: '核对兜底生效内容',
    }
  }

  if (filtered) {
    return {
      tone: 'amber',
      title: '正在查看筛选后的模板集',
      detail: `当前结果 ${itemCount} 项；全局总览数会随筛选变化，必要时清除筛选再判断覆盖面。`,
      action: '确认筛选范围',
    }
  }

  return {
    tone: Number(summary?.override ?? 0) > 0 ? 'amber' : 'green',
    title: Number(summary?.override ?? 0) > 0 ? '目录可读，存在覆盖模板' : 'PromptSpec 目录可读',
    detail: `${summary?.total ?? itemCount} 个模板已返回，${summary?.guarded ?? 0} 个 Guard/Fallback，${summary?.json ?? 0} 个 JSON 模板。`,
    action: '选择模板核对 resolved 内容',
  }
}

export function buildPromptDecisionRailModel({
  filters = {},
  listMetaState,
  listError = null,
  listLoading = false,
  listRefreshing = false,
  items = [],
  summary = {},
  selected = null,
  detailError = null,
  detailLoading = false,
  detailRefreshing = false,
  runtimeSnapshot = null,
} = {}) {
  const itemCount = Array.isArray(items) ? items.length : 0
  const filtered = hasActivePromptFilters(filters)
  const selectedReady = Boolean(selected)
  const detailReady = selectedReady && !detailError && !detailLoading && !detailRefreshing

  return {
    mission: buildPromptDecisionMission({
      filters,
      listMetaState,
      listError,
      listLoading,
      listRefreshing,
      items,
      summary,
      selected,
      detailError,
      detailLoading,
      detailRefreshing,
    }),
    gateCards: [
      {
        key: 'catalog',
        label: OPERATIONS_PROMPT_COPY.catalogTitle,
        value: `${itemCount} templates`,
        detail: listError ? '目录请求失败，当前不能作为线上判断依据。' : 'PromptSpec 目录来自 Host Main 只读接口。',
        tone: listError ? 'red' : itemCount ? 'green' : 'gray',
        targetId: 'prompt-template-catalog',
      },
      {
        key: 'filter',
        label: 'Filter Scope',
        value: filtered ? '已筛选' : '全量',
        detail: filtered ? '当前 keyword/scope/category/source 会改变左侧样本。' : '当前未应用筛选，列表代表全量查询结果。',
        tone: filtered ? 'amber' : 'green',
        targetId: 'prompt-template-catalog',
      },
      {
        key: 'selection',
        label: OPERATIONS_PROMPT_COPY.selectionTitle,
        value: selected?.template_id || '未选择',
        detail: selectedReady ? `${selected.scope} / ${selected.category} / ${selected.source}` : '选择模板后才会加载 default/resolved 内容。',
        tone: selected?.has_override ? 'amber' : selectedReady ? 'blue' : 'gray',
        targetId: 'prompt-template-detail',
      },
      {
        key: 'source',
        label: OPERATIONS_PROMPT_COPY.sourceTitle,
        value: selected?.has_override ? 'override' : 'code-side',
        detail: selected?.has_override ? '当前模板有 runtime override，需对比生效内容。' : 'PromptSpec/YAML 是唯一真源，前端只读展示。',
        tone: selected?.has_override ? 'amber' : detailReady ? 'green' : 'gray',
        targetId: 'prompt-template-detail',
      },
      {
        key: 'runtime',
        label: 'Runtime',
        value: runtimeSnapshot ? '已接入' : '待同步',
        detail: runtimeSnapshot ? '运行态和 prompt builder stats 可用于判断线上影响。' : '运行态缺失不阻断模板目录核对。',
        tone: runtimeSnapshot ? 'blue' : 'gray',
        targetId: 'prompt-runtime-context',
      },
    ],
  }
}

export function getNextPromptTemplateSelection(items = [], selectedId = '') {
  const safeItems = Array.isArray(items) ? items : []
  if (!safeItems.length) return ''
  const hasCurrent = safeItems.some((item) => item?.template_id === selectedId)
  return selectedId && hasCurrent ? selectedId : safeItems[0]?.template_id || ''
}

export function getPromptSourceBadgeView(source, { resolveLabel = false } = {}) {
  return {
    className: resolveStatusClass(PROMPT_SOURCE_BADGE_CLASSES, source, PROMPT_SOURCE_BADGE_CLASSES.default),
    label: resolveLabel ? resolveStatusLabel(PROMPT_SOURCE_LABELS, source, '默认') : source,
  }
}

export function getPromptContentTypeBadgeView(value, { resolveLabel = false } = {}) {
  return {
    className: resolveStatusClass(PROMPT_CONTENT_TYPE_BADGE_CLASSES, value, PROMPT_CONTENT_TYPE_BADGE_CLASSES.text),
    label: resolveLabel ? resolveStatusLabel(PROMPT_CONTENT_TYPE_LABELS, value, '文本') : value,
  }
}

export function flattenPersonaPromptEntries(bundles = []) {
  return (Array.isArray(bundles) ? bundles : []).flatMap((bundle) => bundle?.entries || [])
}

export function getNextPersonaPromptSelection(bundles = [], selectedId = '') {
  const entryIds = flattenPersonaPromptEntries(bundles).map((entry) => entry?.template_id).filter(Boolean)
  if (!entryIds.length) return ''
  return selectedId && entryIds.includes(selectedId) ? selectedId : entryIds[0]
}

export function findSelectedPersonaPromptEntry(bundles = [], selectedId = '') {
  for (const bundle of Array.isArray(bundles) ? bundles : []) {
    const entry = (bundle?.entries || []).find((item) => item?.template_id === selectedId)
    if (entry) return { bundle, entry }
  }
  return null
}

export function buildPersonaPromptSummary({ bundles = [], entries = [] } = {}) {
  const safeBundles = Array.isArray(bundles) ? bundles : []
  const safeEntries = Array.isArray(entries) ? entries : []
  return {
    bundles: safeBundles.length,
    entries: safeEntries.length,
    override: safeEntries.filter((entry) => entry?.item?.source === 'override').length,
    json: safeEntries.filter((entry) => entry?.item?.content_type === 'json').length,
  }
}

export function buildPersonaPromptCatalogLoadState({
  catalogData,
  catalogError,
  catalogLoading = false,
  catalogRefreshing = false,
  catalogStale = false,
  summary,
} = {}) {
  return resolveLoadState({
    loading: catalogLoading || catalogRefreshing,
    error: catalogError,
    hasData: Boolean(catalogData),
    hasSamples: Number(summary?.entries) > 0,
    available: Boolean(catalogData),
    stale: catalogStale,
  })
}

export function buildPersonaPromptSampleMeta({
  catalogLoadState,
  summary,
  catalogUpdatedAt,
} = {}) {
  return {
    loadState: catalogLoadState,
    sampleSize: summary?.entries ?? 0,
    sampleLabel: '模板',
    updatedAt: catalogUpdatedAt,
    source: 'persona-first-batch',
    definition: '人格文案页当前是只读观测台：展示专题包、YAML 默认值、当前生效值与现存 override，不再提供前端编辑入口。',
  }
}

export function formatPromptTemplateUpdatedAt(value) {
  return formatDateTime(value, { placeholder: '-' })
}
