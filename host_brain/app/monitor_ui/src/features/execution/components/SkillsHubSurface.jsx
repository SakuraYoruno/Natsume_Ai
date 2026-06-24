import React from 'react'
import {
  CheckCircle,
  FolderOpen,
  Globe,
  Package,
  RefreshCw,
  Search,
  Shield,
  XCircle,
  Zap,
} from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import { SKILL_CLEARANCE_STYLES, SKILL_SOURCE_STYLES } from '../../../shared/statusMeta.js'

const SOURCE_ICONS = {
  builtin: Package,
  local: FolderOpen,
  github: Globe,
  external: FolderOpen,
  hub: Zap,
}

function SourcePill({ source }) {
  const src = SKILL_SOURCE_STYLES[source] || SKILL_SOURCE_STYLES.builtin
  const SrcIcon = SOURCE_ICONS[source] || SOURCE_ICONS.builtin
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

function SummaryStrip({ items }) {
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
            const clearance = SKILL_CLEARANCE_STYLES[skill.clearance] || SKILL_CLEARANCE_STYLES.LOW
            const loaded = skill.status === 'loaded'
            return (
              <div
                key={skill.name}
                className="grid grid-cols-1 gap-2 px-4 py-3 text-sm md:grid-cols-[minmax(0,1.3fr)_0.5fr_0.55fr_0.55fr_0.45fr] md:items-center md:gap-3"
              >
                <div className="min-w-0">
                  <div className="truncate font-semibold text-gray-800">{skill.name}</div>
                  <div className="mt-0.5 truncate text-xs text-gray-500">
                    {[skill.version ? `v${skill.version}` : null, skill.author ? `作者: ${skill.author}` : null]
                      .filter(Boolean)
                      .join(' · ') || '未提供版本信息'}
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

function SkillsHubHeader({ loading, refreshing, onRefresh }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">技能中心</h1>
        <p className="mt-0.5 text-sm text-gray-500">技能管理 · 热加载 · 外部目录扫描</p>
      </div>
      <button className="btn-secondary" onClick={() => onRefresh()} disabled={loading || refreshing}>
        <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
        刷新
      </button>
    </div>
  )
}

function SkillsHubHero({ heroTitle, heroMetaItems, sampleMeta }) {
  return (
    <PageHero
      icon={Package}
      eyebrow="技能与能力扩展"
      title={heroTitle}
      description="集中查看内置技能、Hub / 外部技能来源、加载状态以及风险等级，方便统一收口技能侧能力。"
      gradientClass="from-emerald-50 via-white to-indigo-50"
      metaItems={heroMetaItems}
      aside={(
        <SampleMeta
          className="max-w-md"
          loadState={sampleMeta.loadState}
          sampleSize={sampleMeta.sampleSize}
          sampleLabel={sampleMeta.sampleLabel}
          updatedAt={sampleMeta.updatedAt}
          source={sampleMeta.source}
          definition={sampleMeta.definition}
        />
      )}
    />
  )
}

function ExternalDirsPanel({ externalDirsView }) {
  if (!externalDirsView.hasExternalDirs) return null

  return (
    <details className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
        <span className="inline-flex min-w-0 items-center gap-2">
          <FolderOpen size={14} className="text-gray-400" />
          已扫描外部目录
        </span>
        <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
          {externalDirsView.count}
        </span>
      </summary>
      <div className="flex flex-wrap gap-2 border-t border-gray-100 px-4 py-3">
        {externalDirsView.rows.map((item) => (
          <span key={item.key} className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-1 text-xs text-gray-600">
            <FolderOpen size={11} />{item.path}
          </span>
        ))}
      </div>
    </details>
  )
}

function SkillsHubFilters({
  filter,
  sourceFilter,
  sourceOptions,
  onFilterChange,
  onSourceFilterChange,
}) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
            placeholder="搜索技能名称、作者、状态或来源..."
            value={filter}
            onChange={(event) => onFilterChange(event.target.value)}
          />
        </div>
        <div className="flex shrink-0 gap-1 overflow-x-auto rounded-lg border border-gray-200 bg-gray-50 p-1">
          {sourceOptions.map(({ source, label, count }) => {
            const active = sourceFilter === source
            return (
              <button
                type="button"
                key={source}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
                  active ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
                onClick={() => onSourceFilterChange(source)}
              >
                {label} {count}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function SkillsHubSurface({
  filter,
  sourceFilter,
  loading,
  refreshing,
  view,
  onFilterChange,
  onSourceFilterChange,
  onRefresh,
}) {
  const {
    sourceOptions,
    filtered,
    sourceGroups,
    summaryItems,
    heroTitle,
    heroMetaItems,
    emptyState,
    sampleMeta,
    noticeView,
    externalDirsView,
  } = view

  return (
    <div className="space-y-6">
      <SkillsHubHeader loading={loading} refreshing={refreshing} onRefresh={onRefresh} />
      <SkillsHubHero heroTitle={heroTitle} heroMetaItems={heroMetaItems} sampleMeta={sampleMeta} />

      {noticeView.hubError ? <NoticeBanner variant={noticeView.hubErrorVariant}>{noticeView.hubError}</NoticeBanner> : null}
      {noticeView.showNoSample ? (
        <NoticeBanner variant="info">当前还没有已加载技能样本，页面会保留中性空态。</NoticeBanner>
      ) : null}

      <SummaryStrip items={summaryItems} />
      <ExternalDirsPanel externalDirsView={externalDirsView} />

      <SkillsHubFilters
        filter={filter}
        sourceFilter={sourceFilter}
        sourceOptions={sourceOptions}
        onFilterChange={onFilterChange}
        onSourceFilterChange={onSourceFilterChange}
      />

      {sourceGroups.map(({ source, title, rows }) => (
        <SkillDirectory
          key={source}
          title={title}
          icon={source === 'builtin' ? Package : SOURCE_ICONS[source] || Globe}
          skills={rows}
        />
      ))}

      {!loading && filtered.length === 0 ? (
        <EmptyStateCard title={emptyState.title} hint={emptyState.hint} className="py-12" />
      ) : null}
    </div>
  )
}
