import { formatDateTime } from '../../shared/formatters.js'
import { resolveLoadState } from '../../models/loadState.js'
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
