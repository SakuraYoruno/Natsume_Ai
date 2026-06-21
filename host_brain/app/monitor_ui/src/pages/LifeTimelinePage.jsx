import React, { useEffect, useMemo, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import LifeBodyTimeline from '../features/life/components/lifeBody/LifeBodyTimeline.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import { useLifeTimelineQuery } from '../features/life/lifeBodyQueries.js'
import {
  LIFE_TIMELINE_ALL_FILTER,
  LIFE_TIMELINE_WINDOW_OPTIONS,
  buildLifeTimelineFilterParams,
  buildLifeTimelineFocusNotice,
  buildLifeTimelineHeroMetaItems,
  buildLifeTimelineHeroTitle,
  buildLifeTimelineLatestEventHint,
  buildLifeTimelineOrganOptions,
  buildLifeTimelineQueryView,
  buildLifeTimelineQueryFilters,
  buildLifeTimelineTypeOptions,
  countLifeTimelineEventTypes,
  filterLifeTimelineEvents,
  getLifeTimelineFilterSelection,
  getLifeTimelineSelectedOrganCount,
} from '../features/life/lifeTimelineModel.js'
import useAppStore from '../store/useAppStore.js'

export default function LifeTimelinePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialSelection = getLifeTimelineFilterSelection(searchParams)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [selectedType, setSelectedType] = useState(initialSelection.selectedType)
  const [selectedOrgan, setSelectedOrgan] = useState(initialSelection.selectedOrgan)
  const { selectedRequestId, selectedSessionId, selectedTickId } = initialSelection
  const queryFilters = buildLifeTimelineQueryFilters(initialSelection)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const timelineQuery = useLifeTimelineQuery({
    windowMinutes,
    ...queryFilters,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    timelineItems,
    hasTimelinePayload,
    timelineLoadError,
    timelineRefreshWarning,
    primaryPathology,
    primaryOrgan,
    sampleMeta,
  } = useMemo(() => buildLifeTimelineQueryView({
    timelineData: timelineQuery.data,
    timelineError: timelineQuery.error,
    loading: timelineQuery.loading,
    refreshing: timelineQuery.refreshing,
    stale: timelineQuery.stale,
    lastUpdatedAt: timelineQuery.lastUpdatedAt,
    windowMinutes,
  }), [
    timelineQuery.data,
    timelineQuery.error,
    timelineQuery.lastUpdatedAt,
    timelineQuery.loading,
    timelineQuery.refreshing,
    timelineQuery.stale,
    windowMinutes,
  ])

  const eventTypes = useMemo(
    () => countLifeTimelineEventTypes(timelineItems),
    [timelineItems]
  )
  const typeOptions = useMemo(() => buildLifeTimelineTypeOptions(timelineItems), [timelineItems])
  const organOptions = useMemo(() => buildLifeTimelineOrganOptions(timelineItems), [timelineItems])
  const filteredEvents = useMemo(
    () => filterLifeTimelineEvents({ items: timelineItems, selectedType, selectedOrgan }),
    [timelineItems, selectedOrgan, selectedType]
  )
  const latestEvent = filteredEvents[0] || null
  const hasTimelineEvents = timelineItems.length > 0
  const hasTimelineError = !hasTimelinePayload && Boolean(timelineQuery.error)
  const requestFocusNotice = buildLifeTimelineFocusNotice('request', selectedRequestId)
  const sessionFocusNotice = buildLifeTimelineFocusNotice('session', selectedSessionId)
  const tickFocusNotice = buildLifeTimelineFocusNotice('tick', selectedTickId)
  const latestEventHint = buildLifeTimelineLatestEventHint({
    selectedRequestId,
    selectedSessionId,
    selectedTickId,
    latestEvent,
  })

  useEffect(() => {
    const selection = getLifeTimelineFilterSelection(searchParams)
    setSelectedType(selection.selectedType)
    setSelectedOrgan(selection.selectedOrgan)
  }, [searchParams])

  const updateFilters = (nextType, nextOrgan) => {
    setSearchParams(buildLifeTimelineFilterParams(searchParams, nextType, nextOrgan), { replace: true })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">生命时间轴</h1>
          <p className="text-sm text-gray-500">把生命体最近经历过的状态变化、节律推进和执行闭环重新拉回同一条时间线上。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={LIFE_TIMELINE_WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={timelineQuery.refetch} disabled={timelineQuery.loading || timelineQuery.refreshing}>
            <RefreshCw size={14} className={timelineQuery.loading || timelineQuery.refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Activity}
        eyebrow="Life Timeline"
        title={buildLifeTimelineHeroTitle({
          hasTimelineError,
          hasTimelineEvents,
          itemCount: timelineItems.length,
        })}
        description="这一页优先回答：这个数字生命刚刚经历了什么、哪些事件正在影响它当前的器官状态，以及下一步该沿哪条链继续追。"
        gradientClass="from-cyan-50 via-white to-primary-50"
        metaItems={buildLifeTimelineHeroMetaItems({
          eventTypeCount: eventTypes,
          primaryPathology,
          primaryOrgan,
        })}
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

      {timelineLoadError ? <NoticeBanner variant="error" title="时间轴统计加载失败">{timelineLoadError.message}</NoticeBanner> : null}
      {timelineRefreshWarning ? (
        <NoticeBanner variant="warning" title="时间轴刷新失败，当前展示旧事件">
          {timelineRefreshWarning.message}
        </NoticeBanner>
      ) : null}
      {requestFocusNotice ? (
        <NoticeBanner variant="info" title={requestFocusNotice.title}>
          {requestFocusNotice.message}
        </NoticeBanner>
      ) : null}
      {sessionFocusNotice ? (
        <NoticeBanner variant="info" title={sessionFocusNotice.title}>
          {sessionFocusNotice.message}
        </NoticeBanner>
      ) : null}
      {tickFocusNotice ? (
        <NoticeBanner variant="info" title={tickFocusNotice.title}>
          {tickFocusNotice.message}
        </NoticeBanner>
      ) : null}

      {hasTimelineEvents ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="当前事件数" value={filteredEvents.length} hint="过滤后的结果" tone="text-primary" />
            <MetricCard label="事件类型" value={eventTypes} hint={`已选 ${selectedType === LIFE_TIMELINE_ALL_FILTER ? '全部' : selectedType}`} tone="text-blue-600" />
            <MetricCard
              label="器官范围"
              value={getLifeTimelineSelectedOrganCount(selectedOrgan, organOptions)}
              hint={selectedOrgan === LIFE_TIMELINE_ALL_FILTER ? '全部器官' : selectedOrgan}
              tone="text-indigo-600"
            />
            <MetricCard
              label="最近事件"
              value={latestEvent?.typeLabel || '—'}
              hint={latestEventHint}
              tone="text-emerald-600"
            />
          </div>

          <div className="card space-y-4 p-5">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-primary/75">事件过滤器</div>
              <div className="mt-1 text-sm text-gray-600">先按事件类型或所属器官收窄范围，再沿代表性请求和病灶详情继续追。</div>
            </div>

            <div className="space-y-3">
              <div>
                <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">按事件类型</div>
                <div className="flex flex-wrap gap-2">
                  {typeOptions.map((item) => (
                    <button
                      key={item.key}
                      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                        selectedType === item.key
                          ? 'border-primary-200 bg-primary-50 text-primary'
                          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                      }`}
                      onClick={() => updateFilters(item.key, selectedOrgan)}
                    >
                      {item.label} · {item.count}
                    </button>
                  ))}
                </div>
              </div>

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
                      onClick={() => updateFilters(selectedType, item.key)}
                    >
                      {item.label} · {item.count}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {filteredEvents.length > 0 ? (
            <LifeBodyTimeline
              events={filteredEvents}
              title="统一生命事件链"
              description="这里已经可以按事件类型和器官系统缩小范围，用于快速判断“刚才到底发生了哪一类状态变化”。"
            />
          ) : (
            <EmptyStateCard
              title="当前过滤条件下没有事件"
              hint="你可以清掉类型或器官过滤，回到更宽的生命事件窗口。"
              className="p-6"
            />
          )}
        </>
      ) : (
        <EmptyStateCard
          title={hasTimelineError ? '生命事件当前不可读' : '当前窗口没有足够的生命事件'}
          hint={hasTimelineError
            ? '当前更像是上游观测接口失败，而不是生命体真的没有事件。建议先恢复 runtime / memory / gateway 接口后再看时间轴。'
            : '这通常意味着 runtime snapshot、记忆样本或执行闭环样本还没形成稳定事件流。'}
          className="p-6"
        />
      )}
    </div>
  )
}
