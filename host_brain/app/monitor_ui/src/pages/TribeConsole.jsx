import React, { useEffect, useState } from 'react'
import { Network, RefreshCw } from 'lucide-react'
import PageHero from '../components/PageHero.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import { fetchTribeBundle } from '../features/execution/api.js'

export default function TribeConsole() {
  const [bundle, setBundle] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    setBundle(await fetchTribeBundle())
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [])

  const events = bundle?.events?.items || []

  return (
    <div className="space-y-5">
      <PageHero
        icon={Network}
        eyebrow="Execution"
        title="Tribe"
        description="Tribe state、event bus 和订阅诊断面，连接 internal tribe routes。"
        gradientClass="from-indigo-50 via-white to-emerald-50"
        metaItems={[
          loading ? 'loading' : 'ready',
          `${events.length} events`,
          `${bundle?.subscribers?.total_handlers ?? 0} handlers`,
        ]}
      />

      {bundle?.errors?.length ? (
        <NoticeBanner variant="warning" title="Tribe 接口降级">
          {bundle.errors.map((item) => item.message).join(' | ')}
        </NoticeBanner>
      ) : null}

      <div className="flex justify-end">
        <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50">
          <RefreshCw size={12} />
          刷新
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <h2 className="mb-3 text-base font-semibold text-gray-800">Recent Events</h2>
          <div className="space-y-2">
            {events.length === 0 ? (
              <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-sm text-gray-500">暂无 Tribe event。</div>
            ) : (
              events.map((event) => (
                <div key={event.event_id} className="rounded-lg border border-gray-100 bg-white p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-800">{event.event_type}</span>
                    <span className="font-mono text-[11px] text-gray-400">{event.ts_ms}</span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">
                    {event.source_agent} {event.target_agent ? `-> ${event.target_agent}` : ''}
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">State Summary</h2>
            <JsonBlock data={bundle?.summary || { loading }} />
          </section>
          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">Subscribers</h2>
            <JsonBlock data={bundle?.subscribers || {}} />
          </section>
        </aside>
      </div>
    </div>
  )
}
