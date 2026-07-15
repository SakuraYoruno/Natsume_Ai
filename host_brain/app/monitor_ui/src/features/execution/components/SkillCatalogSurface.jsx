import React from 'react'
import {
  CheckCircle,
  Package,
  RefreshCw,
  Search,
  Shield,
  XCircle,
} from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import { SKILL_CLEARANCE_STYLES, SKILL_SOURCE_STYLES } from '../../../shared/statusMeta.js'

const SOURCE_ICONS = {
  builtin: Package,
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

function SkillCatalogHeader({ loading, refreshing, onRefresh }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">技能目录</h1>
        <p className="mt-0.5 text-sm text-gray-500">内建技能 · Domain Pack · 治理状态</p>
      </div>
      <button className="btn-secondary" onClick={() => onRefresh()} disabled={loading || refreshing}>
        <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
        刷新
      </button>
    </div>
  )
}

function SkillCatalogHero({ heroTitle, heroMetaItems, sampleMeta }) {
  return (
    <PageHero
      icon={Package}
      eyebrow="内建技能与能力目录"
      title={heroTitle}
      description="集中查看当前注册的内建技能、Domain Pack、加载状态和风险等级。外部 bundle 仅通过签名 Intake 进入隔离验证，不在 Host 内动态安装。"
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

function SkillCatalogFilters({
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

export default function SkillCatalogSurface({
  CapabilityGovernanceSection,
  filter,
  sourceFilter,
  loading,
  refreshing,
  view,
  governance,
  governanceError,
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
  } = view

  return (
    <div className="space-y-6">
      <SkillCatalogHeader loading={loading} refreshing={refreshing} onRefresh={onRefresh} />
      <SkillCatalogHero heroTitle={heroTitle} heroMetaItems={heroMetaItems} sampleMeta={sampleMeta} />

      {noticeView.catalogError ? <NoticeBanner variant={noticeView.catalogErrorVariant}>{noticeView.catalogError}</NoticeBanner> : null}
      {noticeView.showNoSample ? (
        <NoticeBanner variant="info">当前还没有已加载技能样本，页面会保留中性空态。</NoticeBanner>
      ) : null}

      <SummaryStrip items={summaryItems} />
      {CapabilityGovernanceSection ? (
        <CapabilityGovernanceSection model={governance} queryError={governanceError} />
      ) : null}
      <SkillCatalogFilters
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
          icon={SOURCE_ICONS[source] || Package}
          skills={rows}
        />
      ))}

      {!loading && filtered.length === 0 ? (
        <EmptyStateCard title={emptyState.title} hint={emptyState.hint} className="py-12" />
      ) : null}
    </div>
  )
}
