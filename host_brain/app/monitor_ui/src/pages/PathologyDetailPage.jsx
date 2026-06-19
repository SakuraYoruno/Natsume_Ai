import React, { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Activity, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import LifeBodyTimeline from '../components/lifeBody/LifeBodyTimeline.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import { buildLifeTimelinePath, buildPathologyCenterPath } from '../models/lifeBodyRoutes.js'
import { resolveLoadState } from '../models/loadState.js'
import { usePathologiesQuery } from '../features/life/queries.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const SEVERITY_META = {
  critical: { label: '高风险', tone: 'text-red-600' },
  warning: { label: '波动', tone: 'text-orange-500' },
  info: { label: '提示', tone: 'text-blue-600' },
}

export default function PathologyDetailPage() {
  const params = useParams()
  const [windowMinutes, setWindowMinutes] = useState(360)
  const pathologyId = decodeURIComponent(String(params.pathologyId || '').trim())
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pathologyQuery = usePathologiesQuery({
    windowMinutes,
    pathologyId,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const availablePathologies = pathologyQuery.data?.items || []
  const hasPathologyPayload = Boolean(pathologyQuery.data)
  const pathologyLoadError = hasPathologyPayload ? null : pathologyQuery.error
  const pathologyRefreshWarning = hasPathologyPayload ? pathologyQuery.error : null
  const pathology = pathologyQuery.data?.focusedItem || null
  const relatedOrgan = pathologyQuery.data?.focusedOrgan || null
  const relatedEvents = pathologyQuery.data?.relatedEvents || []
  const overviewTrackedSections = hasPathologyPayload ? 1 : 0
  const overviewUpdatedAt = pathologyQuery.lastUpdatedAt || pathologyQuery.data?.timestamp || null
  const overviewLoadState = resolveLoadState({
    loading: pathologyQuery.loading || pathologyQuery.refreshing,
    error: pathologyLoadError,
    hasData: hasPathologyPayload,
    hasSamples: Boolean(pathology),
    available: true,
    stale: pathologyQuery.stale,
  })
  const severityMeta = SEVERITY_META[pathology?.severity] || SEVERITY_META.info
  const hasPathologyError = !pathology && Boolean(pathologyLoadError)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">病灶详情</h1>
          <p className="text-sm text-gray-500">沿当前病灶查看所属器官、影响范围、代表性请求和相关生命事件。</p>
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
        eyebrow="Pathology Detail"
        title={hasPathologyError ? '病灶详情暂不可用' : pathology ? `${pathology.organLabel} · ${pathology.title}` : '当前窗口内未找到该病灶'}
        description={hasPathologyError
          ? '当前病灶详情依赖的上游观测接口未返回可用结果，因此这里不能把错误误报成“病灶不存在”。'
          : pathology ? pathology.symptom : '这通常表示病灶已经从当前时间窗口消退，或当前 URL 指向了一个旧病灶。'}
        gradientClass="from-rose-50 via-white to-orange-50"
        metaItems={pathology ? [
          `级别：${severityMeta.label}`,
          `持续：${pathology.durationLabel}`,
          `来源：${pathology.source}`,
          `器官：${pathology.organLabel}`,
        ] : [
          `当前窗口：${windowMinutes} 分钟`,
          `现存病灶：${availablePathologies.length}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={overviewLoadState}
            loadStateDetail={pathologyLoadError || pathologyRefreshWarning}
            windowMinutes={windowMinutes}
            sampleSize={overviewTrackedSections}
            sampleLabel="病灶详情"
            updatedAt={overviewUpdatedAt}
            source="/obs/pathologies"
            definition="病灶详情页现在直接消费 obs sidecar 的专用病灶接口，并由后端返回聚焦病灶、所属器官和相关生命事件。"
          />
        )}
      />

      {pathologyLoadError ? <NoticeBanner variant="error" title="病灶详情加载失败">{pathologyLoadError.message}</NoticeBanner> : null}
      {pathologyRefreshWarning ? (
        <NoticeBanner variant="warning" title="病灶详情刷新失败，当前展示旧快照">
          {pathologyRefreshWarning.message}
        </NoticeBanner>
      ) : null}

      {pathology ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="病灶级别" value={severityMeta.label} hint={pathology.source} tone={severityMeta.tone} />
            <MetricCard label="影响器官" value={pathology.organLabel} hint={pathology.durationLabel} tone="text-primary" />
            <MetricCard label="代表请求" value={pathology.representativeRefs?.length || 0} hint="可直接进入 Trace" tone="text-blue-600" />
            <MetricCard label="相关事件" value={relatedEvents.length} hint="来自生命时间轴" tone="text-indigo-600" />
          </div>

          <div className="card space-y-4 p-5">
            <div className="grid gap-4 xl:grid-cols-2">
              <div className="rounded-2xl border border-white/80 bg-white/82 p-4 shadow-sm">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">核心症状</div>
                <div className="mt-2 text-sm leading-6 text-gray-700">{pathology.symptom}</div>
              </div>
              <div className="rounded-2xl border border-white/80 bg-white/82 p-4 shadow-sm">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">行为影响</div>
                <div className="mt-2 text-sm leading-6 text-gray-700">{pathology.impactLabel}</div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Link
                to={pathology ? buildPathologyCenterPath({ organId: pathology.organId }) : '/life/pathologies'}
                className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
              >
                返回病灶中心
              </Link>
              <Link
                to={buildLifeTimelinePath({ organId: pathology.organId, type: 'pathology' })}
                className="inline-flex items-center rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50"
              >
                查看病灶事件链
              </Link>
              <Link
                to={pathology.recommendedPath}
                className="inline-flex items-center rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
              >
                {pathology.recommendedLabel}
              </Link>
            </div>
          </div>

          {pathology.representativeRefs?.length > 0 ? (
            <div className="card p-5">
              <div className="text-sm font-semibold text-gray-800">代表性请求</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {pathology.representativeRefs.map((ref) => (
                  <Link
                    key={`${pathology.id}-${ref.id}`}
                    to={ref.to}
                    className="rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                  >
                    {ref.subtitle}
                  </Link>
                ))}
              </div>
            </div>
          ) : (
            <NoticeBanner variant="info" title="当前没有稳定的代表性请求">
              这类病灶更多来自运行快照或聚合告警，暂时还没有可回放的 request 入口。
            </NoticeBanner>
          )}

          {relatedOrgan ? <OrganSystemOverview model={relatedOrgan} /> : null}

          {relatedEvents.length > 0 ? (
            <LifeBodyTimeline
              events={relatedEvents}
              title="病灶相关事件链"
              description="这里优先展示与该病灶所属器官相关的生命事件，帮助你理解它是如何升温、影响到什么行为的。"
            />
          ) : (
            <EmptyStateCard
              title="当前还没有关联事件"
              hint="当时间轴里还没有同器官事件时，说明该病灶更多来自单点告警或刚刚生成的聚合结果。"
              className="p-6"
            />
          )}
        </>
      ) : (
        <div className="space-y-4">
          <EmptyStateCard
            title={hasPathologyError ? '病灶详情当前不可读' : '当前窗口内未找到该病灶'}
            hint={hasPathologyError
              ? '你当前看到的更像是接口错误，而不是病灶真的不存在；建议先恢复病灶聚合依赖后再进入详情。'
              : '你可以返回病灶中心重新选择，或者扩大窗口观察该病灶是否已经从更长时间尺度上消退。'}
            className="p-6"
          />

          {availablePathologies.length > 0 ? (
            <div className="card p-5">
              <div className="text-sm font-semibold text-gray-800">当前仍可查看的病灶</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {availablePathologies.map((item) => (
                  <Link
                    key={item.id}
                    to={item.detailPath || `/life/pathologies/${encodeURIComponent(item.id)}`}
                    className="rounded-full border border-primary-200 bg-primary-50 px-3 py-1 text-xs font-medium text-primary-700 hover:border-primary-300 hover:bg-primary-100"
                  >
                    {item.organLabel} · {item.title}
                  </Link>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
