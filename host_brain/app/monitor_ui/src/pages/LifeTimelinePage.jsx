import React, { useEffect, useMemo, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LifeBodyTimeline from '../components/lifeBody/LifeBodyTimeline.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import { resolveLoadState } from '../models/loadState.js'
import { useLifeTimelineQuery } from '../queries/lifeBodyQueries.js'
import useAppStore from '../store/useAppStore.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function LifeTimelinePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [selectedType, setSelectedType] = useState(String(searchParams.get('type') || 'ALL'))
  const [selectedOrgan, setSelectedOrgan] = useState(String(searchParams.get('organ') || 'ALL'))
  const selectedRequestId = String(searchParams.get('request_id') || '').trim()
  const selectedSessionId = String(searchParams.get('session_id') || '').trim()
  const selectedTickId = String(searchParams.get('tick_id') || '').trim()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const timelineQuery = useLifeTimelineQuery({
    windowMinutes,
    eventType: selectedType !== 'ALL' ? selectedType : '',
    organ: selectedOrgan !== 'ALL' ? selectedOrgan : '',
    requestId: selectedRequestId,
    sessionId: selectedSessionId,
    tickId: selectedTickId,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const timelineItems = timelineQuery.data?.items || []
  const hasTimelinePayload = Boolean(timelineQuery.data)
  const timelineLoadError = hasTimelinePayload ? null : timelineQuery.error
  const timelineRefreshWarning = hasTimelinePayload ? timelineQuery.error : null
  const primaryPathology = timelineQuery.data?.primaryPathology || null
  const primaryOrgan = timelineQuery.data?.primaryOrgan || null
  const overviewTrackedSections = hasTimelinePayload ? 1 : 0
  const overviewUpdatedAt = timelineQuery.lastUpdatedAt || timelineQuery.data?.timestamp || null
  const overviewLoadState = resolveLoadState({
    loading: timelineQuery.loading || timelineQuery.refreshing,
    error: timelineLoadError,
    hasData: hasTimelinePayload,
    hasSamples: timelineItems.length > 0,
    available: true,
    stale: timelineQuery.stale,
  })

  const eventTypes = useMemo(
    () => new Set(timelineItems.map((item) => item.type)).size,
    [timelineItems]
  )
  const typeOptions = useMemo(() => {
    const counts = new Map()
    timelineItems.forEach((item) => {
      const key = item.type || 'unknown'
      const label = item.typeLabel || key
      const current = counts.get(key) || { key, label, count: 0 }
      current.count += 1
      counts.set(key, current)
    })
    return [{ key: 'ALL', label: '全部事件', count: timelineItems.length }, ...Array.from(counts.values())]
  }, [timelineItems])
  const organOptions = useMemo(() => {
    const counts = new Map()
    timelineItems.forEach((item) => {
      const key = item.organId || 'unknown'
      const label = item.organLabel || key
      const current = counts.get(key) || { key, label, count: 0 }
      current.count += 1
      counts.set(key, current)
    })
    return [{ key: 'ALL', label: '全部器官', count: timelineItems.length }, ...Array.from(counts.values())]
  }, [timelineItems])
  const filteredEvents = useMemo(
    () => timelineItems.filter((item) => {
      if (selectedType !== 'ALL' && item.type !== selectedType) return false
      if (selectedOrgan !== 'ALL' && item.organId !== selectedOrgan) return false
      return true
    }),
    [timelineItems, selectedOrgan, selectedType]
  )
  const latestEvent = filteredEvents[0] || null
  const hasTimelineEvents = timelineItems.length > 0
  const hasTimelineError = !hasTimelinePayload && Boolean(timelineQuery.error)

  useEffect(() => {
    const nextType = String(searchParams.get('type') || 'ALL')
    const nextOrgan = String(searchParams.get('organ') || 'ALL')
    setSelectedType(nextType)
    setSelectedOrgan(nextOrgan)
  }, [searchParams])

  const updateFilters = (nextType, nextOrgan) => {
    const params = new URLSearchParams(searchParams)
    if (nextType && nextType !== 'ALL') {
      params.set('type', nextType)
    } else {
      params.delete('type')
    }
    if (nextOrgan && nextOrgan !== 'ALL') {
      params.set('organ', nextOrgan)
    } else {
      params.delete('organ')
    }
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">生命时间轴</h1>
          <p className="text-sm text-gray-500">把生命体最近经历过的状态变化、节律推进和执行闭环重新拉回同一条时间线上。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={timelineQuery.refetch} disabled={timelineQuery.loading || timelineQuery.refreshing}>
            <RefreshCw size={14} className={timelineQuery.loading || timelineQuery.refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Activity}
        eyebrow="Life Timeline"
        title={hasTimelineError ? '生命事件暂不可用' : hasTimelineEvents ? `最近 ${timelineItems.length} 个生命事件` : '当前还没有可读生命事件'}
        description="这一页优先回答：这个数字生命刚刚经历了什么、哪些事件正在影响它当前的器官状态，以及下一步该沿哪条链继续追。"
        gradientClass="from-cyan-50 via-white to-primary-50"
        metaItems={[
          `事件类型：${eventTypes}`,
          `主病灶：${primaryPathology?.title || '暂无'}`,
          `最紧张器官：${primaryOrgan?.name || '暂无'}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={overviewLoadState}
            loadStateDetail={timelineLoadError || timelineRefreshWarning}
            windowMinutes={windowMinutes}
            sampleSize={overviewTrackedSections}
            sampleLabel="聚合投影"
            updatedAt={overviewUpdatedAt}
            source="/obs/life-timeline"
            definition="生命时间轴页现在直接消费 obs sidecar 的专用时间轴接口；当前已统一投影 request / bond / memory / tick / immune，但还不是最终统一事件总线。"
          />
        )}
      />

      {timelineLoadError ? <NoticeBanner variant="error" title="时间轴统计加载失败">{timelineLoadError.message}</NoticeBanner> : null}
      {timelineRefreshWarning ? (
        <NoticeBanner variant="warning" title="时间轴刷新失败，当前展示旧事件">
          {timelineRefreshWarning.message}
        </NoticeBanner>
      ) : null}
      {selectedRequestId ? (
        <NoticeBanner variant="info" title="当前已按 request_id 聚焦">
          {`正在只看 request ${selectedRequestId.slice(0, 18)}${selectedRequestId.length > 18 ? '…' : ''} 的生命事件链。`}
        </NoticeBanner>
      ) : null}
      {selectedSessionId ? (
        <NoticeBanner variant="info" title="当前已按 session_id 聚焦">
          {`正在只看 session ${selectedSessionId.slice(0, 18)}${selectedSessionId.length > 18 ? '…' : ''} 的生命事件链。`}
        </NoticeBanner>
      ) : null}
      {selectedTickId ? (
        <NoticeBanner variant="info" title="当前已按 tick_id 聚焦">
          {`正在只看 tick ${selectedTickId.slice(0, 18)}${selectedTickId.length > 18 ? '…' : ''} 的生命事件链。`}
        </NoticeBanner>
      ) : null}

      {hasTimelineEvents ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="当前事件数" value={filteredEvents.length} hint="过滤后的结果" tone="text-primary" />
            <MetricCard label="事件类型" value={eventTypes} hint={`已选 ${selectedType === 'ALL' ? '全部' : selectedType}`} tone="text-blue-600" />
            <MetricCard label="器官范围" value={selectedOrgan === 'ALL' ? organOptions.length - 1 : 1} hint={selectedOrgan === 'ALL' ? '全部器官' : selectedOrgan} tone="text-indigo-600" />
            <MetricCard
              label="最近事件"
              value={latestEvent?.typeLabel || '—'}
              hint={
                selectedRequestId
                  ? `request ${selectedRequestId.slice(0, 12)}…`
                  : selectedSessionId
                    ? `session ${selectedSessionId.slice(0, 12)}…`
                    : selectedTickId
                      ? `tick ${selectedTickId.slice(0, 12)}…`
                      : latestEvent?.title || '暂无'
              }
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
