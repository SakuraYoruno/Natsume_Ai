import React, { useEffect, useMemo, useState } from 'react'
import { RefreshCw, Wrench } from 'lucide-react'
import ToolDetailPanel from '../features/execution/components/ToolDetailPanel.jsx'
import ToolsCatalogBrowser from '../features/execution/components/ToolsCatalogBrowser.jsx'
import ToolsPentestMetadataSection from '../features/execution/components/ToolsPentestMetadataSection.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import { LazyLoadingNotice, LoadingNotice } from '../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../shared/ui/observability/ViewportLazySection.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import {
  refreshActionPlanStatsQuery,
  refreshPermissionStatsQuery,
  useToolsCatalogQuery,
} from '../features/execution/toolsQueries.js'
import {
  reconcileSelectedTool,
} from '../features/execution/toolsCatalogModel.js'
import { buildToolsDashboardViewModel } from '../features/execution/toolsDashboardModel.js'
import { fetchPentestCapabilities } from '../features/security/api.js'
import useAppStore from '../store/useAppStore.js'

const ToolsOperationsSection = React.lazy(() => import('../features/execution/components/ToolsOperationsSection.jsx'))

export default function Tools() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [selectedTool, setSelectedTool] = useState(null)
  const [executeTarget, setExecuteTarget] = useState(null)
  const [showRuntimeContext, setShowRuntimeContext] = useState(false)
  const [filter, setFilter] = useState('ALL')
  const [query, setQuery] = useState('')
  const [pentestCapabilities, setPentestCapabilities] = useState(null)
  const [pentestLoading, setPentestLoading] = useState(true)
  const [pentestError, setPentestError] = useState('')
  const {
    health,
    tools,
    catalogError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch: refetchCatalog,
  } = useToolsCatalogQuery({
    query,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const loadPentestCapabilities = async () => {
    setPentestLoading(true)
    setPentestError('')
    try {
      const data = await fetchPentestCapabilities()
      setPentestCapabilities(data)
    } catch (error) {
      setPentestError(error.message || String(error))
    } finally {
      setPentestLoading(false)
    }
  }

  useEffect(() => {
    void loadPentestCapabilities()
  }, [])

  useEffect(() => {
    if (!selectedTool) return
    const nextSelection = reconcileSelectedTool({ selectedTool, executeTarget, tools })
    if (!nextSelection.selectedTool) {
      setSelectedTool(null)
      setExecuteTarget(null)
      return
    }
    if (nextSelection.selectedTool !== selectedTool) {
      setSelectedTool(nextSelection.selectedTool)
    }
    if (nextSelection.executeTarget !== executeTarget) {
      setExecuteTarget(nextSelection.executeTarget)
    }
  }, [executeTarget, selectedTool, tools])

  const {
    filteredTools,
    readCount,
    writeCount,
    toolsOrganModel,
    heroView,
    sampleMeta,
    noticeView,
    healthMetricCards,
  } = useMemo(() => buildToolsDashboardViewModel({
    health,
    tools,
    catalogError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    filter,
    query,
    runtimeSnapshot,
  }), [
    catalogError,
    filter,
    health,
    lastUpdatedAt,
    loading,
    query,
    refreshing,
    runtimeSnapshot,
    stale,
    tools,
  ])

  const handleRefresh = async () => {
    await Promise.allSettled([
      refetchCatalog(),
      refreshPermissionStatsQuery({ windowMinutes: 1440 }),
      refreshActionPlanStatsQuery({ windowMinutes: 1440 }),
      reloadRuntime(),
      loadPentestCapabilities(),
    ])
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">工具管理</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看工具目录、权限等级、健康状态与手动执行入口。</p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={loading || refreshing || runtimeLoading}
          className="btn-secondary"
        >
          <RefreshCw size={14} className={loading || refreshing || runtimeLoading ? 'animate-spin' : ''} />
          {loading || refreshing || runtimeLoading
            ? '刷新中…'
            : '刷新'}
        </button>
      </div>

      <PageHero
        icon={Wrench}
        eyebrow="工具与权限"
        title={heroView.title}
        description="可在这里检查工具 catalog、读写权限分布、自定义 provider 状态，以及人工执行入口。"
        gradientClass="from-emerald-50 via-white to-cyan-50"
        metaItems={heroView.metaItems}
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

      {noticeView.catalogError ? <NoticeBanner variant="error">{noticeView.catalogError}</NoticeBanner> : null}

      {noticeView.showUnavailable ? (
        <NoticeBanner variant="warning">工具目录接口当前未返回可用快照，页面会保留中性空态。</NoticeBanner>
      ) : null}

      {noticeView.showNoSample ? (
        <NoticeBanner variant="info">当前系统还没有可观测的工具目录样本。</NoticeBanner>
      ) : null}

      <ToolsPentestMetadataSection
        capabilities={pentestCapabilities}
        loading={pentestLoading}
        error={pentestError}
        onRefresh={loadPentestCapabilities}
      />

      <details
        className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
        onToggle={(event) => setShowRuntimeContext(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
          <span className="inline-flex min-w-0 items-center gap-2">
            <Wrench size={14} className="text-gray-400" />
            工具器官与运行时快照
          </span>
          <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
            {showRuntimeContext ? '已展开' : '展开'}
          </span>
        </summary>
        {showRuntimeContext ? (
          <div className="space-y-4 border-t border-gray-100 p-4">
            <OrganSystemOverview model={toolsOrganModel} />
            <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
          </div>
        ) : null}
      </details>

      {healthMetricCards.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
          <div className="grid grid-cols-2 divide-x divide-y divide-gray-100 md:grid-cols-4 md:divide-y-0">
          {healthMetricCards.map(({ label, value, color }) => (
            <div key={label} className="px-4 py-3">
              <div className={`text-xl font-bold ${color}`}>{value}</div>
              <div className="mt-0.5 text-xs text-gray-500">{label}</div>
            </div>
          ))}
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
        <div className="space-y-3">
          <ToolsCatalogBrowser
            filter={filter}
            filteredTools={filteredTools}
            loading={loading}
            onFilterChange={setFilter}
            onQueryChange={setQuery}
            onSelectTool={(value) => {
              setSelectedTool(value)
              setExecuteTarget(null)
            }}
            query={query}
            readCount={readCount}
            selectedTool={selectedTool}
            tools={tools}
            writeCount={writeCount}
          />
        </div>

        <div className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <ToolDetailPanel
            executeTarget={executeTarget}
            onCloseExecute={() => setExecuteTarget(null)}
            onToggleExecute={(tool) => setExecuteTarget(executeTarget?.id === tool.id ? null : tool)}
            selectedTool={selectedTool}
          />
        </div>
      </div>

      <ViewportLazySection
        rootMargin="1200px 0px"
        fallback={(
          <LazyLoadingNotice>
            自定义 provider 与工具侧深层统计会在滚动接近后再展开。
          </LazyLoadingNotice>
        )}
      >
        <React.Suspense
          fallback={(
            <LoadingNotice>
              正在装配工具运维深层分区…
            </LoadingNotice>
          )}
        >
          <ToolsOperationsSection
            pollingEnabled={pollingEnabled}
            pollingInterval={pollingInterval}
            pollingTick={pollingTick}
            permissionWindowMinutes={1440}
            actionPlanWindowMinutes={1440}
          />
        </React.Suspense>
      </ViewportLazySection>
    </div>
  )
}
