import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  fetchPersonaFirstBatchCatalog,
  fetchPromptTemplate,
  updatePromptTemplate,
  resetPromptTemplate,
  reloadPromptTemplates,
} from '../api/obs.js'
import {
  Database,
  FileText,
  RefreshCw,
  RotateCcw,
  Save,
  Wand2,
  Heart,
  Sparkles,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'

function SourceBadge({ source }) {
  const cls = source === 'override'
    ? 'bg-green-100 text-green-700 border-green-200'
    : 'bg-gray-100 text-gray-600 border-gray-200'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>{source === 'override' ? '已覆盖' : '默认'}</span>
}

function TypeBadge({ value }) {
  const cls = value === 'json'
    ? 'bg-purple-100 text-purple-700 border-purple-200'
    : 'bg-blue-100 text-blue-700 border-blue-200'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>{value === 'json' ? '结构化' : '文本'}</span>
}

function flattenEntries(bundles) {
  return bundles.flatMap((bundle) => bundle.entries || [])
}

export default function PersonaPromptOps() {
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [bundles, setBundles] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [selected, setSelected] = useState(null)
  const [draftContent, setDraftContent] = useState('')
  const [draftEnabled, setDraftEnabled] = useState(true)
  const [loadingCatalog, setLoadingCatalog] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true)
    try {
      const data = await fetchPersonaFirstBatchCatalog()
      const nextBundles = data.bundles || []
      setBundles(nextBundles)
      const entryIds = flattenEntries(nextBundles).map((entry) => entry.template_id)
      if (!entryIds.length) {
        setSelected(null)
        setDraftContent('')
        setDraftEnabled(true)
      }
      setSelectedId((current) => {
        if (!entryIds.length) return ''
        if (!current || !entryIds.includes(current)) return entryIds[0]
        return current
      })
    } catch (error) {
      toast.error(`加载人格文案目录失败：${error.message}`)
    } finally {
      setLoadingCatalog(false)
    }
  }, [])

  const loadDetail = useCallback(async (templateId) => {
    if (!templateId) return
    setLoadingDetail(true)
    try {
      const data = await fetchPromptTemplate(templateId)
      const item = data.item
      setSelected(item)
      setDraftContent(item.override_content ?? item.resolved_content ?? '')
      setDraftEnabled(item.has_override ? Boolean(item.enabled) : true)
    } catch (error) {
      toast.error(`加载模板详情失败：${error.message}`)
    } finally {
      setLoadingDetail(false)
    }
  }, [])

  useEffect(() => {
    void loadCatalog()
  }, [loadCatalog])

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId)
    }
  }, [selectedId, loadDetail])

  const selectedEntry = useMemo(() => {
    for (const bundle of bundles) {
      const entry = (bundle.entries || []).find((item) => item.template_id === selectedId)
      if (entry) {
        return { bundle, entry }
      }
    }
    return null
  }, [bundles, selectedId])

  const entries = useMemo(() => flattenEntries(bundles), [bundles])
  const summary = useMemo(() => ({
    bundles: bundles.length,
    entries: entries.length,
    override: entries.filter((entry) => entry.item?.source === 'override').length,
    json: entries.filter((entry) => entry.item?.content_type === 'json').length,
  }), [bundles, entries])

  const isDirty = useMemo(() => {
    if (!selected) return false
    const baseContent = selected.override_content ?? selected.resolved_content ?? ''
    const baseEnabled = selected.has_override ? Boolean(selected.enabled) : true
    return draftContent !== baseContent || draftEnabled !== baseEnabled
  }, [draftContent, draftEnabled, selected])

  const handleSave = async () => {
    if (!selected) return
    setSaving(true)
    try {
      const data = await updatePromptTemplate(selected.template_id, {
        content: draftContent,
        enabled: draftEnabled,
      })
      setSelected(data.item)
      setDraftContent(data.item.override_content ?? data.item.resolved_content ?? '')
      setDraftEnabled(Boolean(data.item.enabled))
      await loadCatalog()
      toast.success('人格文案已保存')
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
      const data = await resetPromptTemplate(selected.template_id)
      setSelected(data.item)
      setDraftContent(data.item.resolved_content ?? '')
      setDraftEnabled(true)
      await loadCatalog()
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
      await loadCatalog()
      if (selectedId) await loadDetail(selectedId)
      toast.success('模板缓存已刷新')
    } catch (error) {
      toast.error(`刷新失败：${error.message}`)
    } finally {
      setReloading(false)
    }
  }

  const handleFormatJson = () => {
    if (selected?.content_type !== 'json') return
    try {
      const formatted = JSON.stringify(JSON.parse(draftContent), null, 2)
      setDraftContent(formatted)
      toast.success('JSON 已格式化')
    } catch (error) {
      toast.error(`JSON 格式不合法：${error.message}`)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">人格文案运营</h1>
          <p className="text-sm text-gray-500">首批面向人格体验的核心文案模板：自我叙事、表达风格与关系感。</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button className="btn-secondary" onClick={() => navigate('/prompt-config')}>
            <FileText size={14} /> 打开通用模板页
          </button>
          <button className="btn-secondary" onClick={handleReload} disabled={reloading}>
            <RefreshCw size={14} className={reloading ? 'animate-spin' : ''} /> 刷新缓存
          </button>
        </div>
      </div>

      <DigitalLifeNav />
      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <PageHero
        icon={Heart}
        eyebrow="人格文案运营台"
        title={summary.entries}
        description="聚焦首批最影响人格体验的模板，直接调整语气、温度和关系感。"
        gradientClass="from-primary-50 via-white to-pink-50"
        aside={(
          <div className="max-w-xl rounded-xl border border-pink-100 bg-pink-50 px-4 py-3 text-xs leading-6 text-pink-800">
            这里适合调“像不像夏目蓝”的表达风格；不适合改业务逻辑、状态机规则或 JSON 结构。
          </div>
        )}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard icon={Database} label="专题包" value={summary.bundles} hint="按人格域分组" tone="text-blue-600" />
        <MetricCard icon={Wand2} label="模板数" value={summary.entries} hint="首批人格文案" tone="text-purple-600" />
        <MetricCard icon={Sparkles} label="已覆盖" value={summary.override} hint="当前覆盖项" tone="text-green-600" />
        <MetricCard icon={FileText} label="JSON 模板" value={summary.json} hint="结构化文案模板" tone="text-orange-500" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[380px,1fr] gap-4">
        <div className="card p-4 space-y-4">
          <div className="rounded-xl border border-primary-100 bg-primary-50 p-3 text-sm text-primary-900">
            <div className="flex items-center gap-2 font-medium">
              <Wand2 size={15} />
              当前页聚焦首批高价值可运营模板
            </div>
            <div className="mt-2 text-xs leading-6 text-primary-800">
              优先调语气、温度、关系感和节奏感；不要改 JSON 键名，也不要把提示词写成系统命令。
            </div>
          </div>

          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>{loadingCatalog ? '正在加载目录…' : `共 ${entries.length} 个模板`}</span>
            <button className="text-primary hover:underline" onClick={loadCatalog}>刷新目录</button>
          </div>

          <div className="space-y-3 max-h-[72vh] overflow-y-auto">
            {bundles.map((bundle) => (
              <div key={bundle.bundle_id} className="rounded-xl border border-gray-200 bg-white">
                <div className="px-3 py-3 border-b border-gray-100">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-semibold text-gray-800">{bundle.title}</div>
                    <span className="text-[11px] text-gray-400">{bundle.template_count} 项</span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500 leading-5">{bundle.description}</div>
                </div>
                <div className="p-2 space-y-2">
                  {(bundle.entries || []).map((entry) => {
                    const active = entry.template_id === selectedId
                    return (
                      <button
                        key={entry.template_id}
                        className={`w-full text-left rounded-lg border px-3 py-3 transition-colors ${
                          active ? 'border-primary bg-primary-50' : 'border-gray-200 hover:bg-gray-50'
                        }`}
                        onClick={() => setSelectedId(entry.template_id)}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-sm font-medium text-gray-800">{entry.title}</div>
                            <div className="mt-1 text-[11px] font-mono text-gray-500 break-all">
                              {entry.template_id}
                            </div>
                          </div>
                          <SourceBadge source={entry.item?.source || 'default'} />
                        </div>
                        <div className="mt-2 text-xs text-gray-500 leading-5">{entry.description}</div>
                        <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-400">
                          <span>{entry.item?.scope}</span>
                          <span>·</span>
                          <span>{entry.item?.category}</span>
                          <span>·</span>
                          <TypeBadge value={entry.item?.content_type || 'text'} />
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
            {!loadingCatalog && bundles.length === 0 && (
              <EmptyStateCard title="暂无人格模板" hint="等待目录同步完成后重试。" className="p-4" />
            )}
          </div>
        </div>

        <div className="card p-5">
          {!selected || !selectedEntry ? (
            <EmptyStateCard title="请选择人格模板" hint="从左侧列表选择模板后开始编辑。" className="min-h-[420px] flex flex-col items-center justify-center" />
          ) : (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="min-w-0">
                  <div className="text-xs text-gray-400 mb-1">
                    {selectedEntry.bundle.title} / {selected.scope}
                  </div>
                  <h2 className="text-base font-semibold text-gray-900">{selectedEntry.entry.title}</h2>
                  <p className="text-sm text-gray-500 mt-1 leading-6">{selectedEntry.entry.description}</p>
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
                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-3 space-y-2">
                    <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
                      <Database size={14} />
                      运营关注点
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {(selectedEntry.entry.focus_keys || []).map((key) => (
                        <span
                          key={key}
                          className="inline-flex items-center rounded-full bg-white border border-gray-200 px-2.5 py-1 text-xs text-gray-600"
                        >
                          {key}
                        </span>
                      ))}
                    </div>
                    {(selectedEntry.entry.operator_notes || []).length > 0 && (
                      <div className="space-y-1 pt-1">
                        {(selectedEntry.entry.operator_notes || []).map((note) => (
                          <div key={note} className="text-xs text-gray-500 leading-5">- {note}</div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-gray-700 flex items-center gap-2">
                      <FileText size={14} />
                      覆盖内容
                    </label>
                    <div className="flex items-center gap-3">
                      {selected.content_type === 'json' && (
                        <button className="text-xs text-primary hover:underline" onClick={handleFormatJson}>
                          格式化 JSON
                        </button>
                      )}
                      <label className="text-xs text-gray-600 flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={draftEnabled}
                          onChange={(e) => setDraftEnabled(e.target.checked)}
                        />
                        启用覆盖
                      </label>
                    </div>
                  </div>

                  <textarea
                    className={`w-full min-h-[360px] border border-gray-300 rounded-lg px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200 resize-y ${
                      selected.content_type === 'json' ? 'font-mono' : ''
                    }`}
                    value={draftContent}
                    onChange={(e) => setDraftContent(e.target.value)}
                    spellCheck={false}
                  />

                  <div className="flex items-center gap-2 flex-wrap">
                    <button className="btn-primary" onClick={handleSave} disabled={!isDirty || saving || loadingDetail}>
                      <Save size={14} /> 保存
                    </button>
                    <button className="btn-secondary" onClick={handleReset} disabled={!selected.has_override || saving}>
                      <RotateCcw size={14} /> 恢复默认
                    </button>
                    {isDirty && <span className="text-xs text-orange-500">有未保存修改</span>}
                    {loadingDetail && <span className="text-xs text-gray-400">正在同步模板详情…</span>}
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                    <div className="text-xs font-medium text-gray-700 mb-2">代码默认值</div>
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words max-h-[220px] overflow-y-auto">
                      {selected.default_content}
                    </pre>
                  </div>

                  <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                    <div className="text-xs font-medium text-gray-700 mb-2">当前生效值</div>
                    <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words max-h-[220px] overflow-y-auto">
                      {selected.resolved_content}
                    </pre>
                  </div>

                  <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-500 space-y-1">
                    <div>template_id：{selected.template_id}</div>
                    <div>locale：{selected.locale}</div>
                    <div>source：{selected.source}</div>
                    <div>override：{selected.has_override ? 'yes' : 'no'}</div>
                    <div>updated：{selected.updated_at_ts_ms ? new Date(selected.updated_at_ts_ms).toLocaleString('zh-CN') : '-'}</div>
                    {selected.content_type === 'json' && (
                      <div className="text-orange-500">JSON 类型模板需提交合法 JSON。</div>
                    )}
                  </div>
                </div>
              </div>

              {loadingDetail && (
                <NoticeBanner variant="info" title="模板刷新中">
                  正在更新当前模板详情…
                </NoticeBanner>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
