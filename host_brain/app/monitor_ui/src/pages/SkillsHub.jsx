import React, { useMemo, useState } from 'react'
import {
  RefreshCw, Package, CheckCircle, XCircle,
  Shield, Search, FolderOpen, Globe, Zap,
} from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { useSkillsHubQuery } from '../features/execution/queries.js'
import useAppStore from '../store/useAppStore.js'

const CLEARANCE_STYLES = {
  LOW:    { bg: 'bg-green-100',  text: 'text-green-700',  label: 'LOW' },
  MEDIUM: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'MEDIUM' },
  HIGH:   { bg: 'bg-red-100',    text: 'text-red-700',    label: 'HIGH' },
}

const SOURCE_STYLES = {
  builtin:  { bg: 'bg-blue-100',   text: 'text-blue-700',   label: '内置', icon: Package },
  local:    { bg: 'bg-green-100',  text: 'text-green-700',  label: '本地', icon: FolderOpen },
  github:   { bg: 'bg-purple-100', text: 'text-purple-700', label: 'GitHub', icon: Globe },
  external: { bg: 'bg-orange-100', text: 'text-orange-700', label: '外部', icon: FolderOpen },
  hub:      { bg: 'bg-indigo-100', text: 'text-indigo-700', label: 'Hub', icon: Zap },
}

function SourcePill({ source }) {
  const src = SOURCE_STYLES[source] || SOURCE_STYLES.builtin
  const SrcIcon = src.icon
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${src.bg} ${src.text}`}>
      <SrcIcon size={10} />
      {src.label}
    </span>
  )
}

function RiskDots({ value }) {
  const riskLevel = Number(value || 0)
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((index) => (
        <span
          key={index}
          className={`h-2 w-2 rounded-full ${
            index <= riskLevel
              ? riskLevel <= 2
                ? 'bg-green-400'
                : riskLevel <= 3
                  ? 'bg-yellow-400'
                  : 'bg-red-400'
              : 'bg-gray-200'
          }`}
        />
      ))}
    </div>
  )
}

function SummaryStrip({ data, filteredCount }) {
  const items = [
    { label: 'Hub 状态', value: data?.hub_enabled ? '开启' : '关闭', tone: data?.hub_enabled ? 'text-green-600' : 'text-gray-400' },
    { label: '总技能数', value: data?.total_skills ?? '—', tone: 'text-blue-600' },
    { label: '已加载', value: data?.loaded_skills ?? '—', tone: 'text-green-600' },
    { label: '加载失败', value: data?.failed_skills ?? 0, tone: Number(data?.failed_skills || 0) > 0 ? 'text-red-500' : 'text-gray-400' },
    { label: '当前结果', value: filteredCount, tone: 'text-gray-800' },
  ]
  return (
    <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
      <div className="grid grid-cols-2 divide-x divide-y divide-gray-100 sm:grid-cols-5 sm:divide-y-0">
        {items.map((item) => (
          <div key={item.label} className="px-4 py-3">
            <div className={`text-xl font-bold ${item.tone}`}>{item.value}</div>
            <div className="mt-0.5 text-xs text-gray-500">{item.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function SkillDirectory({ title, icon: Icon, skills }) {
  const rows = Array.isArray(skills) ? skills : []
  if (!rows.length) return null
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold text-gray-700">
          <Icon size={14} />
          {title}
        </h2>
        <span className="text-xs font-medium text-gray-400">{rows.length}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
        <div className="hidden grid-cols-[minmax(0,1.3fr)_0.5fr_0.55fr_0.55fr_0.45fr] gap-3 border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-semibold text-gray-500 md:grid">
          <span>技能</span>
          <span>来源</span>
          <span>权限</span>
          <span>风险</span>
          <span>状态</span>
        </div>
        <div className="max-h-[680px] divide-y divide-gray-100 overflow-y-auto">
          {rows.map((skill) => {
            const clearance = CLEARANCE_STYLES[skill.clearance] || CLEARANCE_STYLES.LOW
            const loaded = skill.status === 'loaded'
            return (
              <div
                key={skill.name}
                className="grid grid-cols-1 gap-2 px-4 py-3 text-sm md:grid-cols-[minmax(0,1.3fr)_0.5fr_0.55fr_0.55fr_0.45fr] md:items-center md:gap-3"
              >
                <div className="min-w-0">
                  <div className="truncate font-semibold text-gray-800">{skill.name}</div>
                  <div className="mt-0.5 truncate text-xs text-gray-500">
                    {[skill.version ? `v${skill.version}` : null, skill.author ? `作者: ${skill.author}` : null].filter(Boolean).join(' · ') || '未提供版本信息'}
                  </div>
                </div>
                <div><SourcePill source={skill.source} /></div>
                <div>
                  <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${clearance.bg} ${clearance.text}`}>
                    <Shield size={10} />
                    {clearance.label}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <RiskDots value={skill.risk_level} />
                  <span>{skill.risk_level ?? 0}</span>
                </div>
                <div className="inline-flex items-center gap-1 text-xs">
                  {loaded ? (
                    <>
                      <CheckCircle size={11} className="text-green-500" />
                      <span className="text-green-600">已加载</span>
                    </>
                  ) : (
                    <>
                      <XCircle size={11} className="text-red-500" />
                      <span className="truncate text-red-600">{skill.status}</span>
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

export default function SkillsHub() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)

  const [filter, setFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState('ALL')
  const {
    hubData: data,
    hubError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useSkillsHubQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const skills = data?.skills ?? []
  const sourceOptions = useMemo(() => {
    const values = Array.from(new Set(skills.map((skill) => skill.source || 'builtin'))).sort()
    return ['ALL', ...values]
  }, [skills])
  const filtered = useMemo(() => {
    const keyword = filter.trim().toLowerCase()
    return skills.filter((skill) => {
      const source = skill.source || 'builtin'
      const sourceMatched = sourceFilter === 'ALL' || source === sourceFilter
      if (!sourceMatched) return false
      if (!keyword) return true
      return [
        skill.name,
        source,
        skill.author,
        skill.status,
        skill.clearance,
      ].some((value) => String(value || '').toLowerCase().includes(keyword))
    })
  }, [filter, skills, sourceFilter])
  const sourceGroups = useMemo(() => {
    const groups = new Map()
    filtered.forEach((skill) => {
      const source = skill.source || 'builtin'
      if (!groups.has(source)) groups.set(source, [])
      groups.get(source).push(skill)
    })
    const order = ['builtin', 'hub', 'local', 'github', 'external']
    return Array.from(groups.entries()).sort(([left], [right]) => {
      const leftIndex = order.indexOf(left)
      const rightIndex = order.indexOf(right)
      if (leftIndex !== -1 || rightIndex !== -1) {
        return (leftIndex === -1 ? order.length : leftIndex) - (rightIndex === -1 ? order.length : rightIndex)
      }
      return left.localeCompare(right)
    })
  }, [filtered])
  const skillsHubLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: hubError && !data ? new Error(hubError) : null,
    hasData: Boolean(data),
    hasSamples: skills.length > 0,
    available: Boolean(data),
    stale,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">技能中心</h1>
          <p className="text-sm text-gray-500 mt-0.5">技能管理 · 热加载 · 外部目录扫描</p>
        </div>
        <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      <PageHero
        icon={Package}
        eyebrow="技能与能力扩展"
        title={data?.hub_enabled ? 'Skills Hub 已启用' : 'Skills Hub 当前未启用'}
        description="集中查看内置技能、Hub / 外部技能来源、加载状态以及风险等级，方便统一收口技能侧能力。"
        gradientClass="from-emerald-50 via-white to-indigo-50"
        metaItems={[
          `技能总数：${data?.total_skills ?? 0}`,
          `已加载：${data?.loaded_skills ?? 0}`,
          `加载失败：${data?.failed_skills ?? 0}`,
          `过滤结果：${filtered.length}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={skillsHubLoadState}
            sampleSize={skills.length}
            sampleLabel="技能"
            updatedAt={lastUpdatedAt}
            source="skills hub"
            definition="技能中心页聚合技能总览、来源分布和过滤结果；搜索为空结果不会被误判为系统无样本。"
          />
        )}
      />

      {hubError && <NoticeBanner variant={data ? 'warning' : 'error'}>{hubError}</NoticeBanner>}

      {!hubError && !loading && !refreshing && skillsHubLoadState === LOAD_STATES.NO_SAMPLE && (
        <NoticeBanner variant="info">当前还没有已加载技能样本，页面会保留中性空态。</NoticeBanner>
      )}

      <SummaryStrip data={data} filteredCount={filtered.length} />

      {data?.external_dirs_scanned && data.external_dirs_scanned.length > 0 && (
        <details className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
            <span className="inline-flex min-w-0 items-center gap-2">
              <FolderOpen size={14} className="text-gray-400" />
              已扫描外部目录
            </span>
            <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
              {data.external_dirs_scanned.length}
            </span>
          </summary>
          <div className="flex flex-wrap gap-2 border-t border-gray-100 px-4 py-3">
            {data.external_dirs_scanned.map((d, i) => (
              <span key={i} className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-1 text-xs text-gray-600">
                <FolderOpen size={11} />{d}
              </span>
            ))}
          </div>
        </details>
      )}

      <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="搜索技能名称、作者、状态或来源..."
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          <div className="flex shrink-0 gap-1 overflow-x-auto rounded-lg border border-gray-200 bg-gray-50 p-1">
            {sourceOptions.map((source) => {
              const active = sourceFilter === source
              const sourceCount = source === 'ALL'
                ? skills.length
                : skills.filter((skill) => (skill.source || 'builtin') === source).length
              const label = source === 'ALL' ? '全部' : (SOURCE_STYLES[source]?.label || source)
              return (
                <button
                  type="button"
                  key={source}
                  className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
                    active ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                  }`}
                  onClick={() => setSourceFilter(source)}
                >
                  {label} {sourceCount}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {sourceGroups.map(([source, rows]) => (
        <SkillDirectory
          key={source}
          title={source === 'builtin' ? '内置技能' : `${SOURCE_STYLES[source]?.label || source} 技能`}
          icon={source === 'builtin' ? Package : SOURCE_STYLES[source]?.icon || Globe}
          skills={rows}
        />
      ))}

      {!loading && filtered.length === 0 && (
        <EmptyStateCard
          title={filter || sourceFilter !== 'ALL' ? '没有匹配的技能' : '暂无已加载技能'}
          hint={filter || sourceFilter !== 'ALL' ? '请尝试更换关键字或来源筛选。' : '等待 SkillsHub 拉取后将展示在此。'}
          className="py-12"
        />
      )}
    </div>
  )
}
