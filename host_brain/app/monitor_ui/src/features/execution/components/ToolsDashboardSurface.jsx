import React from 'react'
import { RefreshCw, Wrench } from 'lucide-react'
import { LazyLoadingNotice, LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import ViewportLazySection from '../../../shared/ui/observability/ViewportLazySection.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import { EXECUTION_TOOLS_COPY } from '../../../shared/content/terminology.js'
import ToolDetailPanel from './ToolDetailPanel.jsx'
import ToolsCatalogBrowser from './ToolsCatalogBrowser.jsx'
import ToolsExecutionCommandRail from './ToolsExecutionCommandRail.jsx'
import ToolsPentestMetadataSection from './ToolsPentestMetadataSection.jsx'

const ToolsOperationsSection = React.lazy(() => import('./ToolsOperationsSection.jsx'))

function ToolsHeader({ loading, refreshing, runtimeLoading, onRefresh }) {
  const busy = loading || refreshing || runtimeLoading

  return (
    <div className="flex items-center justify-between">
      <div>
        <h1 className="text-xl font-bold text-gray-800">{EXECUTION_TOOLS_COPY.heroTitle}</h1>
        <p className="mt-0.5 text-sm text-gray-500">统一查看工具目录、权限等级、健康状态与受治理执行归属。</p>
      </div>
      <button onClick={onRefresh} disabled={busy} className="btn-secondary">
        <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
        {busy ? '刷新中...' : '刷新'}
      </button>
    </div>
  )
}

function ToolsHero({ heroView, sampleMeta }) {
  return (
    <PageHero
      icon={Wrench}
      eyebrow={EXECUTION_TOOLS_COPY.heroEyebrow}
      title={heroView.title}
      description={EXECUTION_TOOLS_COPY.heroDescription}
      gradientClass="from-emerald-50 via-white to-cyan-50"
      metaItems={heroView.metaItems}
      showPulseRail={false}
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

function ToolsNotices({ noticeView, executionAvailability }) {
  return (
    <>
      {noticeView.catalogError ? <NoticeBanner variant="error">{noticeView.catalogError}</NoticeBanner> : null}
      {noticeView.showUnavailable ? (
        <NoticeBanner variant="warning">工具目录接口当前未返回可用快照，页面会保留中性空态。</NoticeBanner>
      ) : null}
      {noticeView.showNoSample ? (
        <NoticeBanner variant="info">当前系统还没有可观测的工具目录样本。</NoticeBanner>
      ) : null}
      {!executionAvailability?.available ? (
        <NoticeBanner variant="warning" title="人工工具直执行已迁移">
          工具目录继续只读展示；旧命令 {executionAvailability.commandId || 'tool.execute'} 不再可点击，实际执行请从受治理 TaskGraph 进入。
        </NoticeBanner>
      ) : null}
    </>
  )
}

function ToolsRuntimeContext({
  DigitalLifeRuntimeCard,
  OrganSystemOverview,
  showRuntimeContext,
  onRuntimeContextToggle,
  toolsOrganModel,
  runtimeSnapshot,
  promptStats,
}) {
  return (
    <details
      id="tools-runtime-context"
      className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
      onToggle={(event) => onRuntimeContextToggle(event.currentTarget.open)}
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
          {OrganSystemOverview ? <OrganSystemOverview model={toolsOrganModel} /> : null}
          {DigitalLifeRuntimeCard ? (
            <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
          ) : null}
        </div>
      ) : null}
    </details>
  )
}

function ToolsHealthMetricCards({ cards }) {
  if (!cards.length) return null

  return (
    <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
      <div className="grid grid-cols-2 divide-x divide-y divide-gray-100 md:grid-cols-4 md:divide-y-0">
        {cards.map(({ label, value, color }) => (
          <div key={label} className="px-4 py-3">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="mt-0.5 text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ToolsCatalogWorkspace({
  filter,
  filteredTools,
  loading,
  onFilterChange,
  onQueryChange,
  onSelectTool,
  query,
  readCount,
  selectedTool,
  tools,
  writeCount,
  executeTarget,
  onCloseExecute,
  onToggleExecute,
}) {
  return (
    <div id="tools-catalog-workspace" className="grid scroll-mt-24 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
      <div className="space-y-3">
        <ToolsCatalogBrowser
          filter={filter}
          filteredTools={filteredTools}
          loading={loading}
          onFilterChange={onFilterChange}
          onQueryChange={onQueryChange}
          onSelectTool={onSelectTool}
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
          onCloseExecute={onCloseExecute}
          onToggleExecute={onToggleExecute}
          selectedTool={selectedTool}
        />
      </div>
    </div>
  )
}

function ToolsOperationsLazySection({ pollingEnabled, pollingInterval, pollingTick }) {
  return (
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
            正在装配工具运维深层分区...
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
  )
}

export default function ToolsDashboardSurface({
  DigitalLifeRuntimeCard,
  OrganSystemOverview,
  pollingEnabled,
  pollingInterval,
  pollingTick,
  runtimeSnapshot,
  promptStats,
  runtimeLoading,
  selectedTool,
  executeTarget,
  showRuntimeContext,
  filter,
  query,
  pentestCapabilities,
  pentestLoading,
  pentestError,
  loading,
  refreshing,
  tools,
  view,
  onRefresh,
  onPentestRefresh,
  onRuntimeContextToggle,
  onFilterChange,
  onQueryChange,
  onSelectTool,
  onCloseExecute,
  onToggleExecute,
}) {
  const {
    filteredTools,
    readCount,
    writeCount,
    toolsOrganModel,
    heroView,
    sampleMeta,
    noticeView,
    healthMetricCards,
    commandRailView,
  } = view
  const busy = loading || refreshing || runtimeLoading || pentestLoading

  return (
    <div className="space-y-6">
      <ToolsHeader
        loading={loading}
        refreshing={refreshing}
        runtimeLoading={runtimeLoading}
        onRefresh={onRefresh}
      />

      <ToolsHero heroView={heroView} sampleMeta={sampleMeta} />
      <ToolsNotices
        noticeView={noticeView}
        executionAvailability={commandRailView.executionAvailability}
      />

      <ToolsExecutionCommandRail
        mission={commandRailView.mission}
        gateCards={commandRailView.gateCards}
        onRefresh={onRefresh}
        busy={busy}
      />

      <ToolsPentestMetadataSection
        capabilities={pentestCapabilities}
        loading={pentestLoading}
        error={pentestError}
        onRefresh={onPentestRefresh}
      />

      <ToolsRuntimeContext
        DigitalLifeRuntimeCard={DigitalLifeRuntimeCard}
        OrganSystemOverview={OrganSystemOverview}
        showRuntimeContext={showRuntimeContext}
        onRuntimeContextToggle={onRuntimeContextToggle}
        toolsOrganModel={toolsOrganModel}
        runtimeSnapshot={runtimeSnapshot}
        promptStats={promptStats}
      />

      <ToolsHealthMetricCards cards={healthMetricCards} />

      <ToolsCatalogWorkspace
        filter={filter}
        filteredTools={filteredTools}
        loading={loading}
        onFilterChange={onFilterChange}
        onQueryChange={onQueryChange}
        onSelectTool={onSelectTool}
        query={query}
        readCount={readCount}
        selectedTool={selectedTool}
        tools={tools}
        writeCount={writeCount}
        executeTarget={executeTarget}
        onCloseExecute={onCloseExecute}
        onToggleExecute={onToggleExecute}
      />

      <ToolsOperationsLazySection
        pollingEnabled={pollingEnabled}
        pollingInterval={pollingInterval}
        pollingTick={pollingTick}
      />
    </div>
  )
}
