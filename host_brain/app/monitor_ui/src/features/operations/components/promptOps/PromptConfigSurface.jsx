import React from 'react'
import { Link } from 'react-router-dom'
import {
  Database,
  FileText,
  RefreshCw,
  Search,
  ShieldCheck,
  Wand2,
} from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'
import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import { OPERATIONS_PROMPT_COPY } from '../../../../shared/content/terminology.js'
import {
  formatPromptTemplateUpdatedAt,
  getPromptContentTypeBadgeView,
  getPromptSourceBadgeView,
} from '../../promptOpsModel.js'
import PromptDecisionRail from './PromptDecisionRail.jsx'

const PromptTemplateEditorPanel = React.lazy(() => import('./PromptTemplateEditorPanel.jsx'))

function SourceBadge({ source }) {
  const view = getPromptSourceBadgeView(source, { resolveLabel: true })
  return <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${view.className}`}>{view.label}</span>
}

function TypeBadge({ value }) {
  const view = getPromptContentTypeBadgeView(value, { resolveLabel: true })
  return <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${view.className}`}>{view.label}</span>
}

function PromptConfigHeader({ refreshingView, onRefreshView }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">{OPERATIONS_PROMPT_COPY.heroTitle}</h1>
        <p className="text-sm text-gray-500">查看主链路提示词、守卫兜底与版本化 PromptSpec，正式变更统一回到代码目录。</p>
      </div>
      <div className="flex items-center gap-2">
        <Link to="/operations/persona" className="btn-secondary">
          <Wand2 size={14} />
          人格运营
        </Link>
        <button className="btn-secondary" onClick={onRefreshView} disabled={refreshingView}>
          <RefreshCw size={14} className={refreshingView ? 'animate-spin' : ''} />
          刷新视图
        </button>
      </div>
    </div>
  )
}

function PromptConfigHero({ summary, sampleMeta }) {
  return (
    <PageHero
      icon={FileText}
      eyebrow={OPERATIONS_PROMPT_COPY.heroEyebrow}
      title={summary.total}
      description={OPERATIONS_PROMPT_COPY.heroDescription}
      gradientClass="from-primary-50 via-white to-indigo-50"
      showPulseRail={false}
      aside={(
        <div className="space-y-3">
          <SampleMeta
            className="max-w-xl"
            loadState={sampleMeta.loadState}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
          <div className="max-w-xl rounded-xl border border-orange-100 bg-orange-50 px-4 py-3 text-xs leading-6 text-orange-800">
            PromptSpec 是当前真源。这里适合做线上核对和差异排查，不再承担模板编辑职责。
          </div>
        </div>
      )}
    />
  )
}

function PromptSummaryCards({ summary, scopes, categories }) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <MetricCard icon={Database} label="模板总数" value={summary.total} hint={`${scopes.length} 个 scope`} tone="text-blue-600" />
      <MetricCard icon={Wand2} label="覆盖模板" value={summary.override} hint="当前落地 override" tone="text-green-600" />
      <MetricCard icon={FileText} label="JSON 模板" value={summary.json} hint={`${categories.length} 个 category`} tone="text-purple-600" />
      <MetricCard icon={ShieldCheck} label="Guard / Fallback" value={summary.guarded} hint="守卫与兜底相关项" tone="text-orange-500" />
    </div>
  )
}

function PromptFilterPanel({
  filters,
  scopes,
  categories,
  listLoading,
  listRefreshing,
  items,
  onFilterChange,
  onRefreshList,
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
            placeholder="搜 template_id / 描述"
            value={filters.keyword}
            onChange={(event) => onFilterChange('keyword', event.target.value)}
          />
        </div>
        <div className="grid grid-cols-3 gap-2">
          <select
            className="rounded border border-gray-300 px-2 py-2 text-xs"
            aria-label="Prompt scope filter"
            value={filters.scope}
            onChange={(event) => onFilterChange('scope', event.target.value)}
          >
            <option value="">全部 Scope</option>
            {scopes.map((scope) => <option key={scope} value={scope}>{scope}</option>)}
          </select>
          <select
            className="rounded border border-gray-300 px-2 py-2 text-xs"
            aria-label="Prompt category filter"
            value={filters.category}
            onChange={(event) => onFilterChange('category', event.target.value)}
          >
            <option value="">全部 Category</option>
            {categories.map((category) => <option key={category} value={category}>{category}</option>)}
          </select>
          <select
            className="rounded border border-gray-300 px-2 py-2 text-xs"
            aria-label="Prompt source filter"
            value={filters.source}
            onChange={(event) => onFilterChange('source', event.target.value)}
          >
            <option value="">全部 Source</option>
            <option value="default">default</option>
            <option value="override">override</option>
          </select>
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{listLoading ? '同步中...' : listRefreshing ? '刷新中...' : `共 ${items.length} 项`}</span>
        <button className="text-primary hover:underline" onClick={() => void onRefreshList()}>刷新列表</button>
      </div>
    </div>
  )
}

function PromptTemplateList({
  items,
  selectedId,
  listLoading,
  onSelectTemplate,
}) {
  return (
    <div className="max-h-[70vh] space-y-2 overflow-y-auto">
      {items.map((item) => {
        const active = item.template_id === selectedId
        return (
          <button
            key={item.template_id}
            className={`w-full rounded-lg border px-3 py-3 text-left transition-colors ${
              active ? 'border-primary bg-primary-50' : 'border-gray-200 hover:bg-gray-50'
            }`}
            onClick={() => onSelectTemplate(item.template_id)}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="break-all font-mono text-xs text-gray-700">{item.template_id}</div>
                <div className="mt-1 text-xs text-gray-500">{item.description}</div>
              </div>
              <SourceBadge source={item.source} />
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-gray-400">
              <span>{item.scope}</span>
              <span>·</span>
              <span>{item.category}</span>
              <span>·</span>
              <TypeBadge value={item.content_type} />
            </div>
          </button>
        )
      })}
      {!listLoading && items.length === 0 ? (
        <EmptyStateCard title="没有匹配模板" hint="可调整关键词或筛选条件重试。" className="p-4" />
      ) : null}
    </div>
  )
}

function PromptTemplateDetail({
  selected,
  detailLoading,
  detailRefreshing,
  detailError,
  detailUpdatedAt,
  detailStale,
}) {
  if (!selected) {
    return (
      <EmptyStateCard
        title="请选择模板"
        hint="从左侧列表选择一个模板查看 YAML 默认值与当前生效值。"
        className="flex min-h-[420px] flex-col items-center justify-center"
      />
    )
  }

  return (
    <React.Suspense fallback={<LoadingNotice>正在装配模板详情只读区...</LoadingNotice>}>
      <PromptTemplateEditorPanel
        selected={selected}
        headerEyebrow={`${selected.scope} / ${selected.category}`}
        title={selected.template_id}
        description={selected.description}
        badges={(
          <>
            <SourceBadge source={selected.source} />
            <TypeBadge value={selected.content_type} />
            {selected.has_override ? (
              <span className="inline-flex items-center rounded border border-orange-200 bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
                v{selected.version}
              </span>
            ) : null}
          </>
        )}
        detailLoading={detailLoading}
        detailRefreshing={detailRefreshing}
        detailError={detailError}
        detailUpdatedAt={detailUpdatedAt}
        detailStale={detailStale}
        detailMeta={(
          <div className="space-y-1 rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-500">
            <div>locale：{selected.locale}</div>
            <div>source：{selected.source}</div>
            <div>override：{selected.has_override ? 'yes' : 'no'}</div>
            <div>enabled：{selected.enabled ? 'yes' : 'no'}</div>
            <div>updated：{formatPromptTemplateUpdatedAt(selected.updated_at_ts_ms)}</div>
            {selected.content_type === 'json' ? (
              <div className="text-orange-500">JSON 类型模板当前只读展示；正式修改请直接更新 YAML。</div>
            ) : null}
          </div>
        )}
      />
    </React.Suspense>
  )
}

export default function PromptConfigSurface({
  DigitalLifeRuntimeCard,
  filters,
  selectedId,
  runtimeSnapshot,
  promptStats,
  items,
  listError,
  listLoading,
  listRefreshing,
  scopes,
  categories,
  summary,
  sampleMeta,
  decisionRail,
  selected,
  detailLoading,
  detailRefreshing,
  detailError,
  detailUpdatedAt,
  detailStale,
  refreshingView,
  onFilterChange,
  onSelectTemplate,
  onRefreshList,
  onRefreshView,
}) {
  const busy = refreshingView || listLoading || listRefreshing || detailLoading || detailRefreshing

  return (
    <div className="space-y-6">
      <PromptConfigHeader refreshingView={refreshingView} onRefreshView={onRefreshView} />
      <div id="prompt-runtime-context" className="scroll-mt-24">
        {DigitalLifeRuntimeCard ? (
          <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
        ) : null}
      </div>
      <PromptConfigHero summary={summary} sampleMeta={sampleMeta} />

      <PromptDecisionRail
        decisionRail={decisionRail}
        onRefreshView={onRefreshView}
        busy={busy}
      />

      <NoticeBanner variant="info" title="前端已切换为只读模式">
        默认模板来自 code-side PromptSpec。当前页面只展示 default / resolved 内容，前端不再提供保存、恢复默认或启用覆盖入口。
      </NoticeBanner>

      {listError ? (
        <NoticeBanner variant="error" title="模板列表加载失败">
          {listError}
        </NoticeBanner>
      ) : null}

      <div id="prompt-summary-cards" className="scroll-mt-24">
        <PromptSummaryCards summary={summary} scopes={scopes} categories={categories} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[360px,1fr]">
        <div id="prompt-template-catalog" className="card scroll-mt-24 space-y-4 p-4">
          <PromptFilterPanel
            filters={filters}
            scopes={scopes}
            categories={categories}
            listLoading={listLoading}
            listRefreshing={listRefreshing}
            items={items}
            onFilterChange={onFilterChange}
            onRefreshList={onRefreshList}
          />
          <PromptTemplateList
            items={items}
            selectedId={selectedId}
            listLoading={listLoading}
            onSelectTemplate={onSelectTemplate}
          />
        </div>

        <div id="prompt-template-detail" className="card scroll-mt-24 p-5">
          <PromptTemplateDetail
            selected={selected}
            detailLoading={detailLoading}
            detailRefreshing={detailRefreshing}
            detailError={detailError}
            detailUpdatedAt={detailUpdatedAt}
            detailStale={detailStale}
          />
        </div>
      </div>
    </div>
  )
}
