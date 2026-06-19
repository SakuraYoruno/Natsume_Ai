import React, { useMemo, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LifeBodyPathologyPanel from '../components/lifeBody/LifeBodyPathologyPanel.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import { resolveLoadState } from '../models/loadState.js'
import { usePathologiesQuery } from '../features/life/queries.js'
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
  const allPathologies = pathologyQuery.data?.items || []
  const hasPathologyPayload = Boolean(pathologyQuery.data)
  const pathologyLoadError = hasPathologyPayload ? null : pathologyQuery.error
  const pathologyRefreshWarning = hasPathologyPayload ? pathologyQuery.error : null
  const organSystems = pathologyQuery.data?.organSystems || []
  const primaryPathology = pathologyQuery.data?.primaryPathology || null
  const overviewTrackedSections = hasPathologyPayload ? 1 : 0
  const overviewUpdatedAt = pathologyQuery.lastUpdatedAt || pathologyQuery.data?.timestamp || null
  const overviewLoadState = resolveLoadState({
    loading: pathologyQuery.loading || pathologyQuery.refreshing,
    error: pathologyLoadError,
    hasData: hasPathologyPayload,
    hasSamples: allPathologies.length > 0,
    available: true,
    stale: pathologyQuery.stale,
  })
  const selectedOrgan = String(searchParams.get('organ') || 'ALL')
  const selectedSeverity = String(searchParams.get('severity') || 'ALL')
  const hasAnyPathologies = allPathologies.length > 0
  const hasPathologyError = !hasPathologyPayload && Boolean(pathologyQuery.error)

  const organOptions = useMemo(() => {
    const counts = new Map()
    organSystems.forEach((item) => {
      counts.set(item.id, { key: item.id, label: item.name, count: 0 })
    })
    allPathologies.forEach((item) => {
      const current = counts.get(item.organId) || { key: item.organId, label: item.organLabel || item.organId, count: 0 }
      current.count += 1
      counts.set(item.organId, current)
    })
    const options = Array.from(counts.values()).filter((item) => item.count > 0 || item.key === selectedOrgan)
    return [{ key: 'ALL', label: '全部器官', count: allPathologies.length }, ...options]
  }, [allPathologies, organSystems, selectedOrgan])

  const severityOptions = useMemo(() => {
    const counts = new Map([
      ['critical', { key: 'critical', label: '高风险', count: 0 }],
      ['warning', { key: 'warning', label: '波动', count: 0 }],
      ['info', { key: 'info', label: '提示', count: 0 }],
    ])
    allPathologies.forEach((item) => {
      const current = counts.get(item.severity) || { key: item.severity, label: item.severity || '未分类', count: 0 }
      current.count += 1
      counts.set(item.severity, current)
    })
    return [{ key: 'ALL', label: '全部级别', count: allPathologies.length }, ...Array.from(counts.values())]
  }, [allPathologies])

  const filteredPathologies = useMemo(
    () => allPathologies.filter((item) => {
      if (selectedOrgan !== 'ALL' && item.organId !== selectedOrgan) return false
      if (selectedSeverity !== 'ALL' && item.severity !== selectedSeverity) return false
      return true
    }),
    [allPathologies, selectedOrgan, selectedSeverity]
  )
  const hasPathologies = filteredPathologies.length > 0

  const updateFilters = (nextOrgan, nextSeverity) => {
    const params = new URLSearchParams(searchParams)
    if (nextOrgan && nextOrgan !== 'ALL') {
      params.set('organ', nextOrgan)
    } else {
      params.delete('organ')
    }
    if (nextSeverity && nextSeverity !== 'ALL') {
      params.set('severity', nextSeverity)
    } else {
      params.delete('severity')
    }
    setSearchParams(params, { replace: true })
  }

  const severitySummary = useMemo(
    () => filteredPathologies.reduce((acc, item) => {
      acc[item.severity] = (acc[item.severity] || 0) + 1
      acc[item.organLabel] = (acc[item.organLabel] || 0) + 0
      return acc
    }, {}),
    [filteredPathologies]
  )

  const organSummary = useMemo(
    () => filteredPathologies.reduce((acc, item) => {
      acc[item.organLabel] = (acc[item.organLabel] || 0) + 1
      return acc
    }, {}),
    [filteredPathologies]
  )

  const hottestOrgan = Object.entries(organSummary).sort((left, right) => right[1] - left[1])[0]?.[0] || '暂无'

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
        title={hasPathologyError ? '病灶视图暂不可用' : hasPathologies ? `${filteredPathologies.length} 个当前病灶` : hasAnyPathologies ? '当前过滤条件下没有病灶' : '当前没有形成稳定病灶'}
        description="病灶中心优先回答：哪个器官最异常、异常正在影响什么行为，以及应该先沿哪个页面或哪条 request 往下追。"
        gradientClass="from-rose-50 via-white to-orange-50"
        metaItems={[
          `高风险：${severitySummary.critical || 0}`,
          `波动：${severitySummary.warning || 0}`,
          `最热器官：${hottestOrgan}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={overviewLoadState}
            loadStateDetail={pathologyLoadError || pathologyRefreshWarning}
            windowMinutes={windowMinutes}
            sampleSize={overviewTrackedSections}
            sampleLabel="病灶聚合"
            updatedAt={overviewUpdatedAt}
            source="/obs/pathologies"
            definition="病灶中心页现在直接消费 obs sidecar 的专用病灶接口，由后端统一汇总 runtime alerts、Gateway hotspots 与 Forge runtime 风险。"
          />
        )}
      />

      {pathologyLoadError ? <NoticeBanner variant="error" title="病灶统计加载失败">{pathologyLoadError.message}</NoticeBanner> : null}
      {pathologyRefreshWarning ? (
        <NoticeBanner variant="warning" title="病灶统计刷新失败，当前展示旧聚合">
          {pathologyRefreshWarning.message}
        </NoticeBanner>
      ) : null}

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
