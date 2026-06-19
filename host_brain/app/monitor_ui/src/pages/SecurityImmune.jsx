import React, { useEffect, useMemo, useState } from 'react'
import { Bug, RefreshCw, ShieldCheck } from 'lucide-react'
import PageHero from '../components/PageHero.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import {
  fetchSecurityAssets,
  fetchSecurityIncidents,
  fetchSecurityRuntime,
  runSecurityHuntOnce,
} from '../features/security/api.js'

export default function SecurityImmune() {
  const [runtime, setRuntime] = useState(null)
  const [assets, setAssets] = useState(null)
  const [incidents, setIncidents] = useState(null)
  const [minRisk, setMinRisk] = useState('low')
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState([])
  const [huntResult, setHuntResult] = useState(null)
  const [runningHunt, setRunningHunt] = useState(false)

  const riskCounts = useMemo(() => incidents?.risk_counts || runtime?.risk_counts || {}, [incidents, runtime])

  const load = async () => {
    setLoading(true)
    const results = await Promise.allSettled([
      fetchSecurityRuntime(),
      fetchSecurityAssets(),
      fetchSecurityIncidents(minRisk),
    ])
    setRuntime(results[0].status === 'fulfilled' ? results[0].value : null)
    setAssets(results[1].status === 'fulfilled' ? results[1].value : null)
    setIncidents(results[2].status === 'fulfilled' ? results[2].value : null)
    setErrors(results.filter((item) => item.status === 'rejected').map((item) => item.reason?.message || String(item.reason)))
    setLoading(false)
  }

  useEffect(() => {
    void load()
  }, [minRisk])

  const runHunt = async () => {
    setRunningHunt(true)
    setHuntResult(null)
    try {
      const result = await runSecurityHuntOnce('manual_web_security_immune')
      setHuntResult({ ok: true, data: result })
      await load()
    } catch (error) {
      setHuntResult({ ok: false, error: error.message || String(error) })
    } finally {
      setRunningHunt(false)
    }
  }

  return (
    <div className="space-y-5">
      <PageHero
        icon={ShieldCheck}
        eyebrow="Security Lab"
        title="Security Immune"
        description="内网资产、风险事件和免疫巡检运行态。页面接入 /obs/security/*，手动 hunt 走 internal gated route。"
        gradientClass="from-emerald-50 via-white to-rose-50"
        metaItems={[
          `${assets?.asset_count ?? 0} assets`,
          `${incidents?.incident_count ?? 0} incidents`,
          loading ? 'loading' : 'ready',
        ]}
      />

      {errors.length > 0 ? (
        <NoticeBanner variant="warning" title="部分接口降级">
          {errors.join(' | ')}
        </NoticeBanner>
      ) : null}

      {huntResult ? (
        <NoticeBanner variant={huntResult.ok ? 'success' : 'warning'} title={huntResult.ok ? 'Hunt 已完成' : 'Hunt 失败'}>
          {huntResult.ok ? <JsonBlock data={huntResult.data} className="mt-2" /> : huntResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {['critical', 'high', 'medium', 'low'].map((risk) => (
          <div key={risk} className="rounded-lg border border-white/80 bg-white/86 px-3 py-2 shadow-sm">
            <div className="text-lg font-bold text-gray-800">{riskCounts[risk] ?? 0}</div>
            <div className="text-xs capitalize text-gray-500">{risk}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-gray-800">Incidents</h2>
            <div className="flex items-center gap-2">
              <select
                className="rounded border border-gray-300 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={minRisk}
                onChange={(event) => setMinRisk(event.target.value)}
              >
                {['low', 'medium', 'high', 'critical'].map((risk) => (
                  <option key={risk} value={risk}>{risk}</option>
                ))}
              </select>
              <button type="button" onClick={load} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50">
                <RefreshCw size={12} />
                刷新
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {(incidents?.incidents || []).length === 0 ? (
              <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-sm text-gray-500">当前风险阈值下没有 incident。</div>
            ) : (
              incidents.incidents.map((incident, index) => (
                <div key={incident.incident_id || index} className="rounded-lg border border-gray-100 bg-white p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-800">{incident.title || incident.kind || `Incident ${index + 1}`}</span>
                    <span className="rounded bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">{incident.risk || 'unknown'}</span>
                  </div>
                  <div className="mt-1 text-xs text-gray-500">{incident.detail || incident.reason || '无详情'}</div>
                </div>
              ))
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold text-gray-800">Runtime</h2>
              <StatusBadge status={runtime ? 'healthy' : 'degraded'} label={runtime ? 'connected' : 'degraded'} />
            </div>
            <JsonBlock data={runtime || { loading }} />
          </section>

          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <Bug size={16} className="text-primary" />
              <h2 className="text-base font-semibold text-gray-800">Manual Hunt</h2>
            </div>
            <button
              type="button"
              disabled={runningHunt}
              onClick={runHunt}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {runningHunt ? '运行中...' : 'Run Once'}
            </button>
          </section>

          <section className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">Assets</h2>
            <JsonBlock data={assets || {}} />
          </section>
        </aside>
      </div>
    </div>
  )
}
