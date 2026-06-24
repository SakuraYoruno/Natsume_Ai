import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, ListFilter, Network, RefreshCw, Search, Users } from 'lucide-react'
import PageHero from '../shared/ui/PageHero.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import { TRIBE_COPY } from '../shared/content/terminology.js'
import TribeCommandRail from '../features/execution/components/TribeCommandRail.jsx'
import { fetchTribeBundle } from '../features/execution/api.js'

function getHandlerCount(subscribers) {
  if (Number.isFinite(Number(subscribers?.total_handlers))) return Number(subscribers.total_handlers)
  if (Array.isArray(subscribers?.handlers)) return subscribers.handlers.length
  if (Array.isArray(subscribers?.items)) return subscribers.items.length
  return 0
}

function getSummaryCount(summary) {
  if (Number.isFinite(Number(summary?.total_agents))) return Number(summary.total_agents)
  if (Array.isArray(summary?.agents)) return summary.agents.length
  if (Array.isArray(summary?.items)) return summary.items.length
  return 0
}

function getSubscriberRows(subscribers) {
  const rows = Array.isArray(subscribers?.handlers)
    ? subscribers.handlers
    : Array.isArray(subscribers?.items)
      ? subscribers.items
      : []
  return rows.map((item, index) => ({
    id: item?.handler || item?.id || item?.name || `handler-${index + 1}`,
    eventType: item?.event_type || item?.event || item?.type || item?.topic || 'unknown_event',
    agent: item?.agent || item?.target_agent || item?.source_agent || '',
    status: item?.status || 'registered',
    raw: item,
  }))
}

function formatEventTime(value) {
  if (!value) return 'no timestamp'
  const numeric = Number(value)
  if (Number.isFinite(numeric)) {
    const millis = numeric > 10_000_000_000 ? numeric : numeric * 1000
    return new Date(millis).toLocaleString()
  }
  return String(value)
}

function getEventSearchText(event) {
  return [
    event?.event_id,
    event?.event_type,
    event?.source_agent,
    event?.target_agent,
    event?.topic,
    event?.status,
  ].filter(Boolean).join(' ').toLowerCase()
}

function buildSubscriberCoverage(subscriberRows, scopedEvents) {
  const eventTypes = new Set(scopedEvents.map((event) => String(event?.event_type || '').trim()).filter(Boolean))
  const rows = subscriberRows.map((row) => ({
    ...row,
    covered: eventTypes.has(row.eventType),
  }))
  return {
    rows,
    coveredCount: rows.filter((row) => row.covered).length,
  }
}

export default function TribeConsole() {
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const eventsRef = useRef(null)
  const subscribersRef = useRef(null)

  const load = async () => {
    setLoading(true)
    setLoadError('')
    try {
      setBundle(await fetchTribeBundle())
    } catch (error) {
      setBundle(null)
      setLoadError(error.message || String(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const errors = useMemo(() => {
    const bundleErrors = Array.isArray(bundle?.errors) ? bundle.errors : []
    return loadError ? [...bundleErrors, { message: loadError }] : bundleErrors
  }, [bundle?.errors, loadError])
  const events = Array.isArray(bundle?.events?.items) ? bundle.events.items : []
  const subscriberRows = useMemo(() => getSubscriberRows(bundle?.subscribers), [bundle?.subscribers])
  const handlerCount = getHandlerCount(bundle?.subscribers)
  const agentCount = getSummaryCount(bundle?.summary)
  const eventTypes = useMemo(() => (
    Array.from(new Set(events.map((event) => String(event?.event_type || '').trim()).filter(Boolean))).sort()
  ), [events])
  const filteredEvents = useMemo(() => {
    const query = keyword.trim().toLowerCase()
    return events.filter((event) => {
      const type = String(event?.event_type || '').trim()
      if (typeFilter !== 'all' && type !== typeFilter) return false
      if (!query) return true
      return getEventSearchText(event).includes(query)
    })
  }, [events, keyword, typeFilter])
  const filtersActive = keyword.trim() || typeFilter !== 'all'
  const scopedEvents = filtersActive ? filteredEvents : events
  const latestEvent = scopedEvents[0] || null
  const subscriberCoverage = useMemo(
    () => buildSubscriberCoverage(subscriberRows, scopedEvents),
    [scopedEvents, subscriberRows],
  )
  const clearFilters = () => {
    setKeyword('')
    setTypeFilter('all')
  }

  return (
    <div className="space-y-5">
      <PageHero
        icon={Network}
        eyebrow={TRIBE_COPY.heroEyebrow}
        title={TRIBE_COPY.heroTitle}
        description={TRIBE_COPY.heroDescription}
        gradientClass="from-indigo-50 via-white to-emerald-50"
        metaItems={[
          loading ? 'loading' : 'ready',
          `${events.length} events`,
          `${handlerCount} handlers`,
          agentCount ? `${agentCount} agents` : 'agent summary pending',
        ]}
      />

      <TribeCommandRail
        agentCount={agentCount}
        errorCount={errors.length}
        eventCount={events.length}
        filteredEventCount={filteredEvents.length}
        filtersActive={Boolean(filtersActive)}
        handlerCount={handlerCount}
        latestEvent={latestEvent}
        loading={loading}
        onClearFilters={clearFilters}
        onFocusEvents={() => eventsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        onFocusSubscribers={() => subscribersRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
        subscriberCoverageCount={subscriberCoverage.coveredCount}
        summaryReady={Boolean(bundle?.summary)}
      />

      {errors.length ? (
        <NoticeBanner variant="warning" title="Tribe 接口降级">
          {errors.map((item) => item.message).join(' | ')}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="text-xs font-semibold text-gray-500">Events</div>
          <div className="mt-1 text-2xl font-semibold text-gray-900">{events.length}</div>
          <div className="mt-1 text-xs text-gray-500">{filteredEvents.length} shown after filters</div>
        </div>
        <div className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="text-xs font-semibold text-gray-500">Subscribers</div>
          <div className="mt-1 text-2xl font-semibold text-gray-900">{handlerCount}</div>
          <div className="mt-1 text-xs text-gray-500">registered handlers</div>
        </div>
        <div className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="text-xs font-semibold text-gray-500">State Summary</div>
          <div className="mt-1 text-2xl font-semibold text-gray-900">{agentCount || '--'}</div>
          <div className="mt-1 text-xs text-gray-500">known agents / items</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_390px]">
        <section ref={eventsRef} className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-gray-800">Recent Events</h2>
              <p className="mt-1 text-xs text-gray-500">按 event_type、source_agent、target_agent 或 event_id 筛选协作事件。</p>
            </div>
            <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50">
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>

          <div className="mb-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_220px_auto]">
            <label className="relative block">
              <span className="sr-only">搜索 Tribe 事件</span>
              <Search size={14} className="pointer-events-none absolute left-3 top-2.5 text-gray-400" />
              <input
                aria-label="搜索 Tribe 事件"
                className="w-full rounded-lg border border-gray-200 bg-white py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索 event、agent、topic..."
              />
            </label>
            <label className="block">
              <span className="sr-only">事件类型</span>
              <select
                aria-label="事件类型"
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={typeFilter}
                onChange={(event) => setTypeFilter(event.target.value)}
              >
                <option value="all">全部事件类型</option>
                {eventTypes.map((type) => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!filtersActive}
              onClick={clearFilters}
            >
              <ListFilter size={14} />
              清除
            </button>
          </div>

          {eventTypes.length ? (
            <div aria-label="Tribe event type quick filters" className="mb-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setTypeFilter('all')}
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${typeFilter === 'all' ? 'border-primary-200 bg-primary-50 text-primary-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                全部
              </button>
              {eventTypes.map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setTypeFilter(type)}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${typeFilter === type ? 'border-primary-200 bg-primary-50 text-primary-700' : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'}`}
                >
                  {type}
                </button>
              ))}
            </div>
          ) : null}

          {filtersActive ? (
            <div className="mb-3 rounded-lg border border-primary-100 bg-primary-50/60 px-3 py-2 text-xs text-primary-700">
              已应用事件筛选：{filteredEvents.length}/{events.length}
            </div>
          ) : null}

          <div className="space-y-2">
            {loading && events.length === 0 ? (
              <EmptyStateCard title="正在同步 Tribe event" hint="事件返回后会在这里按时间显示。" className="p-6" />
            ) : filteredEvents.length === 0 ? (
              <EmptyStateCard
                title={events.length === 0 ? '暂无 Tribe event' : '没有匹配事件'}
                hint={events.length === 0 ? '事件总线有样本后会显示最近事件。' : '调整 event_type 或搜索关键词。'}
                className="p-6"
              />
            ) : (
              filteredEvents.map((event, index) => (
                <article key={event.event_id || `${event.event_type}-${event.ts_ms}-${index}`} className="rounded-lg border border-gray-100 bg-white p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-gray-800">{event.event_type || 'unknown_event'}</div>
                      <div className="mt-1 text-xs text-gray-500">
                        {event.source_agent || 'unknown source'} {event.target_agent ? `-> ${event.target_agent}` : ''}
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full border border-gray-100 bg-gray-50 px-2 py-1 font-mono text-[11px] text-gray-500">
                      {formatEventTime(event.ts_ms || event.ts)}
                    </span>
                  </div>
                  {event.event_id ? <div className="mt-2 font-mono text-[11px] text-gray-400">{event.event_id}</div> : null}
                  {event.topic ? <div className="mt-1 text-xs text-gray-500">topic: {event.topic}</div> : null}
                </article>
              ))
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <section aria-label="Tribe Collaboration Snapshot" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-gray-800">Collaboration Snapshot</h2>
                <p className="mt-1 text-xs leading-5 text-gray-500">当前筛选范围内最应该继续追的协作事件和下游入口。</p>
              </div>
              <GitBranch size={18} className="text-primary" />
            </div>
            {latestEvent ? (
              <div className="space-y-3">
                <div className="rounded-lg border border-indigo-100 bg-indigo-50/70 px-3 py-3">
                  <div className="text-xs font-semibold text-indigo-700">Active Event</div>
                  <div className="mt-1 break-all text-sm font-bold text-gray-950">{latestEvent.event_type || 'unknown_event'}</div>
                  <div className="mt-1 text-xs leading-5 text-gray-600">
                    {latestEvent.source_agent || 'unknown source'} {latestEvent.target_agent ? `-> ${latestEvent.target_agent}` : ''}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Link to="/execution/agent-loop" className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:border-primary-200 hover:bg-primary-50">
                    Agent Loop
                  </Link>
                  <Link to="/observability/trace" className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:border-primary-200 hover:bg-primary-50">
                    Trace
                  </Link>
                </div>
              </div>
            ) : (
              <EmptyStateCard title="暂无可追协作事件" hint="清除筛选或等待 event bus 产生样本后再继续。" className="p-4" />
            )}
          </section>

          <section aria-label="Tribe Subscriber Coverage" ref={subscribersRef} className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-gray-800">Subscriber Coverage</h2>
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-semibold text-gray-600">
                <Users size={12} />
                {subscriberCoverage.coveredCount}/{subscriberRows.length || handlerCount} covered
              </span>
            </div>
            {subscriberRows.length === 0 ? (
              <EmptyStateCard title="暂无 subscriber handler" hint="事件总线注册 handler 后会显示覆盖关系。" className="p-4" />
            ) : (
              <div className="space-y-2">
                {subscriberCoverage.rows.map((row) => (
                  <div key={row.id} className={`rounded-lg border px-3 py-2 ${row.covered ? 'border-emerald-100 bg-emerald-50/60' : 'border-orange-100 bg-orange-50/70'}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-gray-800">{row.id}</div>
                        <div className="mt-0.5 truncate text-xs text-gray-500">{row.eventType}</div>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${row.covered ? 'border-emerald-200 bg-white text-emerald-700' : 'border-orange-200 bg-white text-orange-700'}`}>
                        {row.covered ? 'covered' : 'no event'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">State Summary</h2>
            <JsonBlock data={bundle?.summary || { loading }} />
          </section>
          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-gray-800">Raw Subscribers</h2>
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-semibold text-gray-600">
                <Users size={12} />
                {handlerCount} handlers
              </span>
            </div>
            <JsonBlock data={bundle?.subscribers || {}} />
          </section>
        </aside>
      </div>
    </div>
  )
}
