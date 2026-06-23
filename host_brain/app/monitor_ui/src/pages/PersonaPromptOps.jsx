import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Database,
  FileText,
  RefreshCw,
  Wand2,
  Heart,
  Sparkles,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../shared/ui/LoadingNotice.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { usePersonaPromptCatalogQuery } from '../features/operations/personaQueries.js'
import { usePromptTemplateDetailQuery } from '../features/operations/promptQueries.js'
import {
  buildPersonaPromptCatalogLoadState,
  buildPersonaPromptSampleMeta,
  buildPersonaPromptSummary,
  findSelectedPersonaPromptEntry,
  flattenPersonaPromptEntries,
  formatPromptTemplateUpdatedAt,
  getNextPersonaPromptSelection,
} from '../features/operations/promptOpsModel.js'
import PersonaPromptCatalogPanel, { SourceBadge, TypeBadge } from '../features/operations/components/promptOps/PersonaPromptCatalogPanel.jsx'
import useAppStore from '../store/useAppStore.js'

const PromptTemplateEditorPanel = React.lazy(() => import('../features/operations/components/promptOps/PromptTemplateEditorPanel.jsx'))

export default function PersonaPromptOps() {
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [selectedId, setSelectedId] = useState('')
  const [refreshingView, setRefreshingView] = useState(false)
  const {
    catalogData,
    bundles,
    catalogError,
    loading: catalogLoading,
    refreshing: catalogRefreshing,
    stale: catalogStale,
    lastUpdatedAt: catalogUpdatedAt,
    refetch: refetchCatalog,
  } = usePersonaPromptCatalogQuery()
  const {
    item: selected,
    detailError,
    loading: detailLoading,
    refreshing: detailRefreshing,
    stale: detailStale,
    lastUpdatedAt: detailUpdatedAt,
    refetch: refetchDetail,
  } = usePromptTemplateDetailQuery({ templateId: selectedId })

  useEffect(() => {
    setSelectedId((current) => {
      const nextSelectedId = getNextPersonaPromptSelection(bundles, current)
      return nextSelectedId === current ? current : nextSelectedId
    })
  }, [bundles])

  const selectedEntry = useMemo(
    () => findSelectedPersonaPromptEntry(bundles, selectedId),
    [bundles, selectedId],
  )

  const entries = useMemo(() => flattenPersonaPromptEntries(bundles), [bundles])
  const summary = useMemo(() => buildPersonaPromptSummary({ bundles, entries }), [bundles, entries])

  const catalogLoadState = useMemo(() => buildPersonaPromptCatalogLoadState({
    catalogData,
    catalogError,
    catalogLoading,
    catalogRefreshing,
    catalogStale,
    summary,
  }), [catalogData, catalogError, catalogLoading, catalogRefreshing, catalogStale, summary])
  const sampleMeta = useMemo(() => buildPersonaPromptSampleMeta({
    catalogLoadState,
    summary,
    catalogUpdatedAt,
  }), [catalogLoadState, catalogUpdatedAt, summary])

  const handleRefreshView = async () => {
    setRefreshingView(true)
    try {
      await Promise.allSettled([refetchCatalog(), selectedId ? refetchDetail() : Promise.resolve(null)])
    } finally {
      setRefreshingView(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">语言系统文案总览</h1>
          <p className="text-sm text-gray-500">按人格语义查看 YAML 默认文案、当前生效值与现存 runtime override。</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button className="btn-secondary" onClick={() => navigate('/operations/prompt')}>
            <FileText size={14} /> 打开通用模板页
          </button>
          <button className="btn-secondary" onClick={handleRefreshView} disabled={refreshingView}>
            <RefreshCw size={14} className={refreshingView ? 'animate-spin' : ''} /> 刷新视图
          </button>
        </div>
      </div>

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />

      <PageHero
        icon={Heart}
        eyebrow="语言系统文案观测台"
        title={summary.entries}
        description="按人格专题查看 YAML 默认文案、当前生效值与已有 override，用于核对现在的表达风格是否偏离预期。"
        gradientClass="from-primary-50 via-white to-pink-50"
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
            <div className="max-w-xl rounded-xl border border-pink-100 bg-pink-50 px-4 py-3 text-xs leading-6 text-pink-800">
              YAML 已经是唯一真源。这里更适合做人格表达核对和 drift 排查，而不是在线改文案。
            </div>
          </div>
        )}
      />

      <NoticeBanner variant="info" title="前端已切换为只读模式">
        人格文案模板的正式变更统一通过 YAML 完成。当前页面只读展示 `default / resolved / override` 三层结果，用于判断表达风格是否发生漂移。
      </NoticeBanner>

      {catalogError ? (
        <NoticeBanner variant="error" title="人格文案目录加载失败">
          {catalogError}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard icon={Database} label="专题包" value={summary.bundles} hint="按人格域分组" tone="text-blue-600" />
        <MetricCard icon={Wand2} label="模板数" value={summary.entries} hint="首批人格文案" tone="text-purple-600" />
        <MetricCard icon={Sparkles} label="已覆盖" value={summary.override} hint="当前覆盖项" tone="text-green-600" />
        <MetricCard icon={FileText} label="JSON 模板" value={summary.json} hint="结构化文案模板" tone="text-orange-500" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[380px,1fr] gap-4">
        <PersonaPromptCatalogPanel
          bundles={bundles}
          catalogLoading={catalogLoading}
          catalogRefreshing={catalogRefreshing}
          entries={entries}
          onRefreshCatalog={refetchCatalog}
          onSelect={setSelectedId}
          selectedId={selectedId}
        />
        <div className="card p-5">
          {selected && selectedEntry ? (
            <React.Suspense
              fallback={(
                <LoadingNotice>
                  正在装配人格模板详情只读区…
                </LoadingNotice>
              )}
            >
              <PromptTemplateEditorPanel
                selected={selected}
                headerEyebrow={`${selectedEntry.bundle.title} / ${selected.scope}`}
                title={selectedEntry.entry.title}
                description={selectedEntry.entry.description}
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
                inspectorTop={(
                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-3 space-y-2">
                    <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
                      <Database size={14} />
                      运营关注点
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {(selectedEntry.entry.focus_keys || []).map((key) => (
                        <span
                          key={key}
                          className="inline-flex items-center rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-600"
                        >
                          {key}
                        </span>
                      ))}
                    </div>
                    {(selectedEntry.entry.operator_notes || []).length > 0 ? (
                      <div className="space-y-1 pt-1">
                        {(selectedEntry.entry.operator_notes || []).map((note) => (
                          <div key={note} className="text-xs text-gray-500 leading-5">- {note}</div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )}
                detailLoading={detailLoading}
                detailRefreshing={detailRefreshing}
                detailError={detailError}
                detailUpdatedAt={detailUpdatedAt}
                detailStale={detailStale}
                detailMeta={(
                  <div className="rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-500 space-y-1">
                    <div>template_id：{selected.template_id}</div>
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
          ) : (
            <EmptyStateCard title="请选择人格模板" hint="从左侧列表选择模板后查看 YAML 默认文案与当前生效值。" className="min-h-[420px] flex flex-col items-center justify-center" />
          )}
        </div>
      </div>
    </div>
  )
}
