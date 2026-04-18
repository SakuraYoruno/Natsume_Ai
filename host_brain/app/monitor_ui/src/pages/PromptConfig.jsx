import React, { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  reloadPromptTemplates,
  resetPromptTemplate,
  updatePromptTemplate,
} from '../api/promptOps.js'
import {
  RefreshCw,
  Save,
  RotateCcw,
  Search,
  FileText,
  Database,
  Wand2,
  ShieldCheck,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { resolveLoadState } from '../models/loadState.js'
import {
  usePromptTemplateDetailQuery,
  usePromptTemplateListQuery,
} from '../queries/promptQueries.js'
import useAppStore from '../store/useAppStore.js'

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
  const [draftContent, setDraftContent] = useState('')
  const [draftEnabled, setDraftEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)
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

  useEffect(() => {
    if (!selected) {
      setDraftContent('')
      setDraftEnabled(true)
      return
    }
    setDraftContent(selected.override_content ?? selected.resolved_content ?? '')
    setDraftEnabled(selected.has_override ? Boolean(selected.enabled) : true)
  }, [selected])

  const isDirty = useMemo(() => {
    if (!selected) return false
    const baseContent = selected.override_content ?? selected.resolved_content ?? ''
    const baseEnabled = selected.has_override ? Boolean(selected.enabled) : true
    return draftContent !== baseContent || draftEnabled !== baseEnabled
  }, [draftContent, draftEnabled, selected])

  const listMetaState = resolveLoadState({
    loading: listLoading || listRefreshing,
    error: listError,
    hasData: Boolean(templatesData),
    hasSamples: true,
    available: Boolean(templatesData),
    stale: listStale,
  })

  const handleSave = async () => {
    if (!selected) return
    setSaving(true)
    try {
      await updatePromptTemplate(selected.template_id, {
        content: draftContent,
        enabled: draftEnabled,
      })
      await Promise.allSettled([refetchList(), refetchDetail()])
      toast.success('模板已保存')
    } catch (error) {
      toast.error(`保存失败：${error.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!selected) return
    if (!window.confirm(`确认恢复 ${selected.template_id} 的代码默认值？`)) return
    setSaving(true)
    try {
      await resetPromptTemplate(selected.template_id)
      await Promise.allSettled([refetchList(), refetchDetail()])
      toast.success('已恢复默认值')
    } catch (error) {
      toast.error(`恢复默认失败：${error.message}`)
    } finally {
      setSaving(false)
    }
  }

  const handleReload = async () => {
    setReloading(true)
    try {
      await reloadPromptTemplates()
      await Promise.allSettled([refetchList(), selectedId ? refetchDetail() : Promise.resolve(null)])
      toast.success('模板缓存已刷新')
    } catch (error) {
      toast.error(`刷新失败：${error.message}`)
    } finally {
      setReloading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">提示与兜底运营</h1>
          <p className="text-sm text-gray-500">将主链路提示词、守卫兜底和运营文案统一收口到可视化配置。</p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/persona-config" className="btn-secondary">
            <Wand2 size={14} /> 人格运营
          </Link>
          <button className="btn-secondary" onClick={handleReload} disabled={reloading}>
            <RefreshCw size={14} className={reloading ? 'animate-spin' : ''} /> 刷新缓存
          </button>
        </div>
      </div>

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <PageHero
        icon={FileText}
        eyebrow="数字生命体提示运营台"
        title={summary.total}
        description="当前模板池覆盖主链路提示词、守卫型兜底与部分可运营文案。"
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
              definition="提示运营页把模板列表与模板详情拆成独立查询；筛选为空结果不会再和系统无样本、接口失败混淆。"
            />
            <div className="max-w-xl rounded-xl border border-orange-100 bg-orange-50 px-4 py-3 text-xs leading-6 text-orange-800">
              建议优先调整语气、关系感与表达节奏；不要修改 JSON 键名，也不要把运营文案写成系统指令。
            </div>
          </div>
        )}
      />

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
            <span>{listLoading ? '加载中…' : listRefreshing ? '刷新中…' : `共 ${items.length} 项`}</span>
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
          {!selected && (
            <EmptyStateCard title="请选择模板" hint="从左侧列表选择一个模板开始编辑。" className="min-h-[420px] flex flex-col items-center justify-center" />
          )}

          {selected && (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="min-w-0">
                  <div className="text-xs text-gray-400 mb-1">{selected.scope} / {selected.category}</div>
                  <h2 className="text-base font-semibold text-gray-900 break-all">{selected.template_id}</h2>
                  <p className="text-sm text-gray-500 mt-1">{selected.description}</p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <SourceBadge source={selected.source} />
                  <TypeBadge value={selected.content_type} />
                  {selected.has_override && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium bg-orange-100 text-orange-700 border-orange-200">
                      v{selected.version}
                    </span>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-[1fr,320px] gap-4">
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
                      <FileText size={14} /> 覆盖内容
                    </label>
                    <label className="text-xs text-gray-600 flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={draftEnabled}
                        onChange={(e) => setDraftEnabled(e.target.checked)}
                      />
                      启用覆盖
                    </label>
                  </div>
                  <textarea
                    className={`w-full min-h-[340px] border border-gray-300 rounded-lg px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200 resize-y ${
                      selected.content_type === 'json' ? 'font-mono' : ''
                    }`}
                    value={draftContent}
                    onChange={(e) => setDraftContent(e.target.value)}
                    spellCheck={false}
                  />
                  <div className="flex items-center gap-2">
                    <button className="btn-primary" onClick={handleSave} disabled={!isDirty || saving || detailLoading || detailRefreshing}>
                      <Save size={14} /> 保存
                    </button>
                    <button className="btn-secondary" onClick={handleReset} disabled={!selected.has_override || saving}>
                      <RotateCcw size={14} /> 恢复默认
                    </button>
                    {isDirty && <span className="text-xs text-orange-500">有未保存修改</span>}
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                    <div className="text-xs font-medium text-gray-700 flex items-center gap-2 mb-2">
                      <Database size={14} /> 代码默认内容
                    </div>
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words max-h-[220px] overflow-y-auto">{selected.default_content}</pre>
                  </div>

                  <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                    <div className="text-xs font-medium text-gray-700 flex items-center gap-2 mb-2">
                      <Wand2 size={14} /> 当前生效内容
                    </div>
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words max-h-[220px] overflow-y-auto">{selected.resolved_content}</pre>
                  </div>

                  <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-500 space-y-1">
                    <div>locale：{selected.locale}</div>
                    <div>source：{selected.source}</div>
                    <div>override：{selected.has_override ? 'yes' : 'no'}</div>
                    <div>updated：{selected.updated_at_ts_ms ? new Date(selected.updated_at_ts_ms).toLocaleString('zh-CN') : '-'}</div>
                    {selected.content_type === 'json' && (
                      <div className="text-orange-500">JSON 类型模板请提交合法 JSON 文本。</div>
                    )}
                  </div>
                </div>
              </div>

              {detailError ? (
                <NoticeBanner variant="warning" title="模板详情暂不可用">
                  {detailError}
                  {detailUpdatedAt ? ` · 最近更新时间 ${new Date(detailUpdatedAt).toLocaleString('zh-CN')}` : ''}
                </NoticeBanner>
              ) : null}

              {detailLoading || detailRefreshing || detailStale ? (
                <NoticeBanner variant="info" title="模板刷新中">
                  {detailLoading || detailRefreshing ? '正在同步模板详情…' : '模板详情已超过建议新鲜度阈值，可手动刷新。'}
                </NoticeBanner>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
