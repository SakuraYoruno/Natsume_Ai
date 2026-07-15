import { SKILL_SOURCE_STYLES } from '../../shared/statusMeta.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'

export const SKILL_SOURCE_FILTER_ALL = 'ALL'

const DEFAULT_SKILL_SOURCE = 'builtin'
const SKILL_SOURCE_ORDER = ['builtin']

export function getSkillSource(skill) {
  return skill?.source || DEFAULT_SKILL_SOURCE
}

export function getSkillSourceLabel(source) {
  if (source === SKILL_SOURCE_FILTER_ALL) return '全部'
  return SKILL_SOURCE_STYLES[source]?.label || source
}

function compareSkillSource(left, right) {
  const leftIndex = SKILL_SOURCE_ORDER.indexOf(left)
  const rightIndex = SKILL_SOURCE_ORDER.indexOf(right)
  if (leftIndex !== -1 || rightIndex !== -1) {
    return (leftIndex === -1 ? SKILL_SOURCE_ORDER.length : leftIndex)
      - (rightIndex === -1 ? SKILL_SOURCE_ORDER.length : rightIndex)
  }
  return left.localeCompare(right)
}

export function buildSkillSourceOptions(skills) {
  const rows = Array.isArray(skills) ? skills : []
  const counts = new Map()
  rows.forEach((skill) => {
    const source = getSkillSource(skill)
    counts.set(source, (counts.get(source) || 0) + 1)
  })
  const sourceOptions = Array.from(counts.keys()).sort().map((source) => ({
    source,
    label: getSkillSourceLabel(source),
    count: counts.get(source) || 0,
  }))
  return [
    { source: SKILL_SOURCE_FILTER_ALL, label: getSkillSourceLabel(SKILL_SOURCE_FILTER_ALL), count: rows.length },
    ...sourceOptions,
  ]
}

export function filterSkills(skills, { keyword = '', sourceFilter = SKILL_SOURCE_FILTER_ALL } = {}) {
  const rows = Array.isArray(skills) ? skills : []
  const normalizedKeyword = String(keyword || '').trim().toLowerCase()
  return rows.filter((skill) => {
    const source = getSkillSource(skill)
    const sourceMatched = sourceFilter === SKILL_SOURCE_FILTER_ALL || source === sourceFilter
    if (!sourceMatched) return false
    if (!normalizedKeyword) return true
    return [
      skill?.name,
      source,
      skill?.author,
      skill?.status,
      skill?.clearance,
    ].some((value) => String(value || '').toLowerCase().includes(normalizedKeyword))
  })
}

export function buildSkillSourceGroups(skills) {
  const groups = new Map()
  const rows = Array.isArray(skills) ? skills : []
  rows.forEach((skill) => {
    const source = getSkillSource(skill)
    if (!groups.has(source)) groups.set(source, [])
    groups.get(source).push(skill)
  })
  return Array.from(groups.entries())
    .sort(([left], [right]) => compareSkillSource(left, right))
    .map(([source, groupRows]) => ({
      source,
      title: source === DEFAULT_SKILL_SOURCE ? '内置技能' : `${getSkillSourceLabel(source)} 技能`,
      rows: groupRows,
    }))
}

export function buildSkillCatalogSummaryItems(data, filteredCount) {
  return [
    {
      label: '目录状态',
      value: data?.catalog_available ? '可用' : '不可用',
      tone: data?.catalog_available ? 'text-green-600' : 'text-gray-400',
    },
    { label: '总技能数', value: data?.total_skills ?? '—', tone: 'text-blue-600' },
    { label: '已加载', value: data?.loaded_skills ?? '—', tone: 'text-green-600' },
    {
      label: '加载失败',
      value: data?.failed_skills ?? 0,
      tone: Number(data?.failed_skills || 0) > 0 ? 'text-red-500' : 'text-gray-400',
    },
    { label: '当前结果', value: filteredCount, tone: 'text-gray-800' },
  ]
}

export function buildSkillCatalogHeroMetaItems(data, filteredCount) {
  return [
    `技能总数：${data?.total_skills ?? 0}`,
    `已加载：${data?.loaded_skills ?? 0}`,
    `加载失败：${data?.failed_skills ?? 0}`,
    `过滤结果：${filteredCount}`,
  ]
}

export function buildSkillCatalogHeroTitle(data) {
  return data?.catalog_available ? 'Skill Catalog 可用' : 'Skill Catalog 暂不可用'
}

export function buildSkillCatalogEmptyState({ filter = '', sourceFilter = SKILL_SOURCE_FILTER_ALL } = {}) {
  const filtered = Boolean(filter) || sourceFilter !== SKILL_SOURCE_FILTER_ALL
  return {
    title: filtered ? '没有匹配的技能' : '暂无已加载技能',
    hint: filtered ? '请尝试更换关键字或来源筛选。' : '等待内建技能目录投影后将展示在此。',
  }
}

export function buildSkillCatalogLoadState({
  data,
  catalogError,
  loading = false,
  refreshing = false,
  stale = false,
  skills = [],
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: catalogError && !data ? new Error(catalogError) : null,
    hasData: Boolean(data),
    hasSamples: skills.length > 0,
    available: Boolean(data),
    stale,
  })
}

export function buildSkillCatalogSampleMeta({
  skillCatalogLoadState,
  skills = [],
  lastUpdatedAt,
} = {}) {
  return {
    loadState: skillCatalogLoadState,
    sampleSize: skills.length,
    sampleLabel: '技能',
    updatedAt: lastUpdatedAt,
    source: 'skill catalog',
    definition: '技能目录页展示当前已注册的内建技能和 Domain Pack；搜索为空结果不会被误判为系统无样本。',
  }
}

export function buildSkillCatalogNoticeView({
  catalogError,
  data,
  loading = false,
  refreshing = false,
  skillCatalogLoadState,
} = {}) {
  return {
    catalogError,
    catalogErrorVariant: data ? 'warning' : 'error',
    showNoSample: Boolean(!catalogError && !loading && !refreshing && skillCatalogLoadState === LOAD_STATES.NO_SAMPLE),
  }
}

export function buildSkillCatalogViewModel({
  data,
  filter,
  sourceFilter,
  catalogError,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt,
} = {}) {
  const skills = Array.isArray(data?.skills) ? data.skills : []
  const filtered = filterSkills(skills, { keyword: filter, sourceFilter })
  const skillCatalogLoadState = buildSkillCatalogLoadState({
    data,
    catalogError,
    loading,
    refreshing,
    stale,
    skills,
  })
  return {
    skills,
    filtered,
    sourceOptions: buildSkillSourceOptions(skills),
    sourceGroups: buildSkillSourceGroups(filtered),
    summaryItems: buildSkillCatalogSummaryItems(data, filtered.length),
    heroTitle: buildSkillCatalogHeroTitle(data),
    heroMetaItems: buildSkillCatalogHeroMetaItems(data, filtered.length),
    emptyState: buildSkillCatalogEmptyState({ filter, sourceFilter }),
    skillCatalogLoadState,
    sampleMeta: buildSkillCatalogSampleMeta({
      skillCatalogLoadState,
      skills,
      lastUpdatedAt,
    }),
    noticeView: buildSkillCatalogNoticeView({
      catalogError,
      data,
      loading,
      refreshing,
      skillCatalogLoadState,
    }),
  }
}
