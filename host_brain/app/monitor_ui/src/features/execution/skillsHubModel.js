import { SKILL_SOURCE_STYLES } from '../../shared/statusMeta.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'

export const SKILL_SOURCE_FILTER_ALL = 'ALL'

const DEFAULT_SKILL_SOURCE = 'builtin'
const SKILL_SOURCE_ORDER = ['builtin', 'hub', 'local', 'github', 'external']

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

export function buildSkillsHubSummaryItems(data, filteredCount) {
  return [
    {
      label: 'Hub 状态',
      value: data?.hub_enabled ? '开启' : '关闭',
      tone: data?.hub_enabled ? 'text-green-600' : 'text-gray-400',
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

export function buildSkillsHubHeroMetaItems(data, filteredCount) {
  return [
    `技能总数：${data?.total_skills ?? 0}`,
    `已加载：${data?.loaded_skills ?? 0}`,
    `加载失败：${data?.failed_skills ?? 0}`,
    `过滤结果：${filteredCount}`,
  ]
}

export function buildSkillsHubHeroTitle(data) {
  return data?.hub_enabled ? 'Skills Hub 已启用' : 'Skills Hub 当前未启用'
}

export function buildSkillsHubEmptyState({ filter = '', sourceFilter = SKILL_SOURCE_FILTER_ALL } = {}) {
  const filtered = Boolean(filter) || sourceFilter !== SKILL_SOURCE_FILTER_ALL
  return {
    title: filtered ? '没有匹配的技能' : '暂无已加载技能',
    hint: filtered ? '请尝试更换关键字或来源筛选。' : '等待 SkillsHub 拉取后将展示在此。',
  }
}

export function buildSkillsHubLoadState({
  data,
  hubError,
  loading = false,
  refreshing = false,
  stale = false,
  skills = [],
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: hubError && !data ? new Error(hubError) : null,
    hasData: Boolean(data),
    hasSamples: skills.length > 0,
    available: Boolean(data),
    stale,
  })
}

export function buildSkillsHubSampleMeta({
  skillsHubLoadState,
  skills = [],
  lastUpdatedAt,
} = {}) {
  return {
    loadState: skillsHubLoadState,
    sampleSize: skills.length,
    sampleLabel: '技能',
    updatedAt: lastUpdatedAt,
    source: 'skills hub',
    definition: '技能中心页聚合技能总览、来源分布和过滤结果；搜索为空结果不会被误判为系统无样本。',
  }
}

export function buildSkillsHubNoticeView({
  hubError,
  data,
  loading = false,
  refreshing = false,
  skillsHubLoadState,
} = {}) {
  return {
    hubError,
    hubErrorVariant: data ? 'warning' : 'error',
    showNoSample: Boolean(!hubError && !loading && !refreshing && skillsHubLoadState === LOAD_STATES.NO_SAMPLE),
  }
}

export function buildSkillsHubExternalDirsView(data) {
  const rows = Array.isArray(data?.external_dirs_scanned) ? data.external_dirs_scanned : []
  return {
    hasExternalDirs: rows.length > 0,
    count: rows.length,
    rows: rows.map((path, index) => ({
      key: `${index}-${path}`,
      path,
    })),
  }
}

export function buildSkillsHubViewModel({
  data,
  filter,
  sourceFilter,
  hubError,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt,
} = {}) {
  const skills = Array.isArray(data?.skills) ? data.skills : []
  const filtered = filterSkills(skills, { keyword: filter, sourceFilter })
  const skillsHubLoadState = buildSkillsHubLoadState({
    data,
    hubError,
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
    summaryItems: buildSkillsHubSummaryItems(data, filtered.length),
    heroTitle: buildSkillsHubHeroTitle(data),
    heroMetaItems: buildSkillsHubHeroMetaItems(data, filtered.length),
    emptyState: buildSkillsHubEmptyState({ filter, sourceFilter }),
    skillsHubLoadState,
    sampleMeta: buildSkillsHubSampleMeta({
      skillsHubLoadState,
      skills,
      lastUpdatedAt,
    }),
    noticeView: buildSkillsHubNoticeView({
      hubError,
      data,
      loading,
      refreshing,
      skillsHubLoadState,
    }),
    externalDirsView: buildSkillsHubExternalDirsView(data),
  }
}
