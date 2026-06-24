import React, { useMemo, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import LifeSystemsCommandRail from '../features/life/components/LifeSystemsCommandRail.jsx'
import LifeBodyPathologyPanel from '../features/life/components/lifeBody/LifeBodyPathologyPanel.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import {
  buildPathologyCenterCommandRailModel,
  buildPathologyCenterHeroMetaItems,
  buildPathologyCenterHeroTitle,
  buildPathologyCenterQueryView,
  buildPathologyFilterParams,
  buildPathologyOrganOptions,
  buildPathologySeverityOptions,
  filterPathologiesBySelection,
  getHottestPathologyOrgan,
  getPathologyFilterSelection,
  summarizePathologyOrgans,
  summarizePathologySeverity,
} from '../features/life/pathologyCenterModel.js'
import { usePathologiesQuery } from '../features/life/lifeBodyQueries.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function PathologyCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [windowMinutes, setWindowMinutes] = useState(360)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pathologyQuery = usePathologiesQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    allPathologies,
    hasPathologyPayload,
    pathologyLoadError,
    pathologyRefreshWarning,
    organSystems,
    primaryPathology,
    sampleMeta,
  } = useMemo(() => buildPathologyCenterQueryView({
    pathologyData: pathologyQuery.data,
    pathologyError: pathologyQuery.error,
    loading: pathologyQuery.loading,
    refreshing: pathologyQuery.refreshing,
    stale: pathologyQuery.stale,
    lastUpdatedAt: pathologyQuery.lastUpdatedAt,
    windowMinutes,
  }), [
    pathologyQuery.data,
    pathologyQuery.error,
    pathologyQuery.lastUpdatedAt,
    pathologyQuery.loading,
    pathologyQuery.refreshing,
    pathologyQuery.stale,
    windowMinutes,
  ])
  const { selectedOrgan, selectedSeverity } = useMemo(
    () => getPathologyFilterSelection(searchParams),
    [searchParams]
  )
  const hasAnyPathologies = allPathologies.length > 0
  const hasPathologyError = !hasPathologyPayload && Boolean(pathologyQuery.error)

  const organOptions = useMemo(() => buildPathologyOrganOptions({
    allPathologies,
    organSystems,
    selectedOrgan,
  }), [allPathologies, organSystems, selectedOrgan])

  const severityOptions = useMemo(() => buildPathologySeverityOptions(allPathologies), [allPathologies])

  const filteredPathologies = useMemo(() => filterPathologiesBySelection({
    allPathologies,
    selectedOrgan,
    selectedSeverity,
  }), [allPathologies, selectedOrgan, selectedSeverity])
  const hasPathologies = filteredPathologies.length > 0

  const updateFilters = (nextOrgan, nextSeverity) => {
    setSearchParams(buildPathologyFilterParams(searchParams, nextOrgan, nextSeverity), { replace: true })
  }

  const severitySummary = useMemo(() => summarizePathologySeverity(filteredPathologies), [filteredPathologies])
  const organSummary = useMemo(() => summarizePathologyOrgans(filteredPathologies), [filteredPathologies])
  const hottestOrgan = getHottestPathologyOrgan(organSummary)
  const heroTitle = buildPathologyCenterHeroTitle({
    hasPathologyError,
    hasPathologies,
    filteredCount: filteredPathologies.length,
    hasAnyPathologies,
  })
  const heroMetaItems = useMemo(
    () => buildPathologyCenterHeroMetaItems(severitySummary, hottestOrgan),
    [hottestOrgan, severitySummary]
  )
  const commandRailModel = useMemo(() => buildPathologyCenterCommandRailModel({
    hasPathologyPayload,
    hasPathologyError,
    pathologyRefreshWarning,
    allPathologies,
    filteredPathologies,
    selectedOrgan,
    selectedSeverity,
    primaryPathology,
    severitySummary,
    hottestOrgan,
  }), [
    allPathologies,
    filteredPathologies,
    hasPathologyError,
    hasPathologyPayload,
    hottestOrgan,
    pathologyRefreshWarning,
    primaryPathology,
    selectedOrgan,
    selectedSeverity,
    severitySummary,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">病灶中心</h1>
          <p className="text-sm text-gray-500">把当前窗口内真正值得追的病灶集中起来，统一呈现器官、症状、影响范围和 drill-down 入口。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={pathologyQuery.refetch} disabled={pathologyQuery.loading || pathologyQuery.refreshing}>
            <RefreshCw size={14} className={pathologyQuery.loading || pathologyQuery.refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Activity}
        eyebrow="Pathology Center"
        title={heroTitle}
        description="病灶中心优先回答：哪个器官最异常、异常正在影响什么行为，以及应该先沿哪个页面或哪条 request 往下追。"
        gradientClass="from-rose-50 via-white to-orange-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            loadStateDetail={sampleMeta.loadStateDetail}
            windowMinutes={sampleMeta.windowMinutes}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {pathologyLoadError ? <NoticeBanner variant="error" title="病灶统计加载失败">{pathologyLoadError.message}</NoticeBanner> : null}
      {pathologyRefreshWarning ? (
        <NoticeBanner variant="warning" title="病灶统计刷新失败，当前展示旧聚合">
          {pathologyRefreshWarning.message}
        </NoticeBanner>
      ) : null}

      <LifeSystemsCommandRail
        ariaLabel="Pathology Command Rail"
        model={commandRailModel}
        onRefresh={pathologyQuery.refetch}
        refreshing={pathologyQuery.loading || pathologyQuery.refreshing}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="高风险病灶" value={severitySummary.critical || 0} hint="需要优先处理" tone="text-red-600" />
        <MetricCard label="波动病灶" value={severitySummary.warning || 0} hint="建议继续观察" tone="text-orange-500" />
        <MetricCard label="最热器官" value={hottestOrgan} hint={primaryPathology?.title || '当前暂无主病灶'} tone="text-primary" />
      </div>

      <div className="card space-y-4 p-5">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-rose-600/80">病灶过滤器</div>
          <div className="mt-1 text-sm text-gray-600">可以按器官系统或病灶级别缩小范围，适合从器官网页或外部链接直接带上下文进入。</div>
        </div>

        <div className="space-y-3">
          <div>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">按器官系统</div>
            <div className="flex flex-wrap gap-2">
              {organOptions.map((item) => (
                <button
                  key={item.key}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                    selectedOrgan === item.key
                      ? 'border-primary-200 bg-primary-50 text-primary'
                      : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                  }`}
                  onClick={() => updateFilters(item.key, selectedSeverity)}
                >
                  {item.label} · {item.count}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">按病灶级别</div>
            <div className="flex flex-wrap gap-2">
              {severityOptions.map((item) => (
                <button
                  key={item.key}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                    selectedSeverity === item.key
                      ? 'border-primary-200 bg-primary-50 text-primary'
                      : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                  }`}
                  onClick={() => updateFilters(selectedOrgan, item.key)}
                >
                  {item.label} · {item.count}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {hasPathologies ? (
        <LifeBodyPathologyPanel pathologies={filteredPathologies} />
      ) : hasAnyPathologies && (selectedOrgan !== 'ALL' || selectedSeverity !== 'ALL') ? (
        <EmptyStateCard
          title="当前过滤条件下没有病灶"
          hint="可以清掉器官系统或病灶级别过滤，回到更宽的病灶窗口继续观察。"
          className="p-6"
        />
      ) : (
        <EmptyStateCard
          title={hasPathologyError ? '病灶统计当前不可读' : '当前还没有稳定病灶'}
          hint={hasPathologyError
            ? '这更像是病灶聚合依赖的上游观测接口失败，而不是系统真的没有病灶。'
            : '这通常表示当前窗口里没有足够的异常聚类；如果你刚做完修复，可以继续观察一轮真实请求和后台推进。'}
          className="p-6"
        />
      )}
    </div>
  )
}
