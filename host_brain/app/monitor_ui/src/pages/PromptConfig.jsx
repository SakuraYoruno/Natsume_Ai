import React, { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  RefreshCw,
  Search,
  FileText,
  Database,
  Wand2,
  ShieldCheck,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LoadingNotice from '../components/LoadingNotice.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import {
  usePromptTemplateDetailQuery,
  usePromptTemplateListQuery,
} from '../features/operations/queries.js'
import useAppStore from '../store/useAppStore.js'

const PromptTemplateEditorPanel = React.lazy(() => import('../components/promptOps/PromptTemplateEditorPanel.jsx'))

function SourceBadge({ source }) {
  const cls = source === 'override'
    ? 'bg-green-100 text-green-700 border-green-200'
    : 'bg-gray-100 text-gray-600 border-gray-200'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>{source}</span>
}

function TypeBadge({ value }) {
  const cls = value === 'json'
    ? 'bg-purple-100 text-purple-700 border-purple-200'
    : 'bg-blue-100 text-blue-700 border-blue-200'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>{value}</span>
}

export default function PromptConfig() {
  const [searchParams] = useSearchParams()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [filters, setFilters] = useState(() => ({
    keyword: String(searchParams.get('keyword') || ''),
    scope: String(searchParams.get('scope') || ''),
    category: String(searchParams.get('category') || ''),
    source: String(searchParams.get('source') || ''),
  }))
  const [selectedId, setSelectedId] = useState('')
  const [refreshingView, setRefreshingView] = useState(false)
  const {
    templatesData,
    items,
    listError,
    loading: listLoading,
    refreshing: listRefreshing,
    stale: listStale,
    lastUpdatedAt: listUpdatedAt,
    refetch: refetchList,
  } = usePromptTemplateListQuery({ filters })
  const {
    item: selected,
    detailError,
    loading: detailLoading,
    refreshing: detailRefreshing,
    stale: detailStale,
    lastUpdatedAt: detailUpdatedAt,
    refetch: refetchDetail,
  } = usePromptTemplateDetailQuery({ templateId: selectedId })

  const scopes = useMemo(() => [...new Set(items.map((item) => item.scope).filter(Boolean))], [items])
  const categories = useMemo(() => [...new Set(items.map((item) => item.category).filter(Boolean))], [items])

  const summary = useMemo(() => ({
    total: items.length,
    override: items.filter((item) => item.source === 'override').length,
    json: items.filter((item) => item.content_type === 'json').length,
    guarded: items.filter((item) => String(item.category || '').includes('fallback') || String(item.category || '').includes('guard')).length,
  }), [items])

  useEffect(() => {
    if (!items.length) {
      setSelectedId('')
      return
    }
    const hasCurrent = items.some((item) => item.template_id === selectedId)
    if (!selectedId || !hasCurrent) {
      setSelectedId(items[0].template_id)
    }
  }, [items, selectedId])

  const listMetaState = resolveLoadState({
    loading: listLoading || listRefreshing,
    error: listError,
    hasData: Boolean(templatesData),
    hasSamples: true,
    available: Boolean(templatesData),
    stale: listStale,
  })

  const handleRefreshView = async () => {
    setRefreshingView(true)
    try {
      await Promise.allSettled([refetchList(), selectedId ? refetchDetail() : Promise.resolve(null)])
    } finally {
      setRefreshingView(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">提示与兜底模板总览</h1>
          <p className="text-sm text-gray-500">查看主链路提示词、守卫兜底与版本化 PromptSpec，正式变更统一回到代码目录。</p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/operations/persona" className="btn-secondary">
            <Wand2 size={14} /> 人格运营
          </Link>
          <button className="btn-secondary" onClick={handleRefreshView} disabled={refreshingView}>
            <RefreshCw size={14} className={refreshingView ? 'animate-spin' : ''} /> 刷新视图
          </button>
        </div>
      </div>

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <PageHero
        icon={FileText}
        eyebrow="数字生命体提示模板观测台"
        title={summary.total}
        description="这里只读展示 code-side PromptSpec 默认值与当前生效值，帮助判断线上到底吃了哪套模板。"
        gradientClass="from-primary-50 via-white to-indigo-50"
        aside={(
          <div className="space-y-3">
            <SampleMeta
              className="max-w-xl"
              loadState={listMetaState}
              sampleSize={summary.total}
              sampleLabel="模板"
              updatedAt={listUpdatedAt}
              source="prompt-config"
              definition="提示模板页当前是只读观测台：展示版本化 PromptSpec 与当前生效值；不再提供前端编辑入口。"
            />
            <div className="max-w-xl rounded-xl border border-orange-100 bg-orange-50 px-4 py-3 text-xs leading-6 text-orange-800">
              PromptSpec 是当前真源。这里适合做线上核对和差异排查，不再承担模板编辑职责。
            </div>
          </div>
        )}
      />

      <NoticeBanner variant="info" title="前端已切换为只读模式">
        默认模板来自 code-side PromptSpec。当前页面只展示 default / resolved 内容，前端不再提供保存、恢复默认或启用覆盖入口。
      </NoticeBanner>

      {listError ? (
        <NoticeBanner variant="error" title="模板列表加载失败">
          {listError}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard icon={Database} label="模板总数" value={summary.total} hint={`${scopes.length} 个 scope`} tone="text-blue-600" />
        <MetricCard icon={Wand2} label="覆盖模板" value={summary.override} hint="当前落地 override" tone="text-green-600" />
        <MetricCard icon={FileText} label="JSON 模板" value={summary.json} hint={`${categories.length} 个 category`} tone="text-purple-600" />
        <MetricCard icon={ShieldCheck} label="Guard / Fallback" value={summary.guarded} hint="守卫与兜底相关项" tone="text-orange-500" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[360px,1fr] gap-4">
        <div className="card p-4 space-y-4">
          <div className="grid grid-cols-1 gap-2">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-200"
                placeholder="搜 template_id / 描述"
                value={filters.keyword}
                onChange={(e) => setFilters((prev) => ({ ...prev, keyword: e.target.value }))}
              />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <select
                className="text-xs border border-gray-300 rounded px-2 py-2"
                value={filters.scope}
                onChange={(e) => setFilters((prev) => ({ ...prev, scope: e.target.value }))}
              >
                <option value="">全部 Scope</option>
                {scopes.map((scope) => <option key={scope} value={scope}>{scope}</option>)}
              </select>
              <select
                className="text-xs border border-gray-300 rounded px-2 py-2"
                value={filters.category}
                onChange={(e) => setFilters((prev) => ({ ...prev, category: e.target.value }))}
              >
                <option value="">全部 Category</option>
                {categories.map((category) => <option key={category} value={category}>{category}</option>)}
              </select>
              <select
                className="text-xs border border-gray-300 rounded px-2 py-2"
                value={filters.source}
                onChange={(e) => setFilters((prev) => ({ ...prev, source: e.target.value }))}
              >
                <option value="">全部 Source</option>
                <option value="default">default</option>
                <option value="override">override</option>
              </select>
            </div>
          </div>

          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>{listLoading ? '同步中…' : listRefreshing ? '刷新中…' : `共 ${items.length} 项`}</span>
            <button className="text-primary hover:underline" onClick={() => void refetchList()}>刷新列表</button>
          </div>

          <div className="space-y-2 max-h-[70vh] overflow-y-auto">
            {items.map((item) => {
              const active = item.template_id === selectedId
              return (
                <button
                  key={item.template_id}
                  className={`w-full text-left border rounded-lg px-3 py-3 transition-colors ${
                    active ? 'border-primary bg-primary-50' : 'border-gray-200 hover:bg-gray-50'
                  }`}
                  onClick={() => setSelectedId(item.template_id)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-xs font-mono text-gray-700 break-all">{item.template_id}</div>
                      <div className="text-xs text-gray-500 mt-1">{item.description}</div>
                    </div>
                    <SourceBadge source={item.source} />
                  </div>
                  <div className="flex items-center gap-2 mt-2 text-xs text-gray-400">
                    <span>{item.scope}</span>
                    <span>·</span>
                    <span>{item.category}</span>
                    <span>·</span>
                    <TypeBadge value={item.content_type} />
                  </div>
                </button>
              )
            })}
            {!listLoading && items.length === 0 && (
              <EmptyStateCard title="没有匹配模板" hint="可调整关键词或筛选条件重试。" className="p-4" />
            )}
          </div>
        </div>

        <div className="card p-5">
          {selected ? (
            <React.Suspense
              fallback={(
                <LoadingNotice>
                  正在装配模板详情只读区…
                </LoadingNotice>
              )}
            >
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
                  <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-500 space-y-1">
                    <div>locale：{selected.locale}</div>
                    <div>source：{selected.source}</div>
                    <div>override：{selected.has_override ? 'yes' : 'no'}</div>
                    <div>enabled：{selected.enabled ? 'yes' : 'no'}</div>
                    <div>updated：{selected.updated_at_ts_ms ? new Date(selected.updated_at_ts_ms).toLocaleString('zh-CN') : '-'}</div>
                    {selected.content_type === 'json' ? (
                      <div className="text-orange-500">JSON 类型模板当前只读展示；正式修改请直接更新 YAML。</div>
                    ) : null}
                  </div>
                )}
              />
            </React.Suspense>
          ) : (
            <EmptyStateCard title="请选择模板" hint="从左侧列表选择一个模板查看 YAML 默认值与当前生效值。" className="min-h-[420px] flex flex-col items-center justify-center" />
          )}
        </div>
      </div>
    </div>
  )
}
