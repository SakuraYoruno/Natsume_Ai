import React, { useMemo, useState } from 'react'
import { Bug, Filter, RefreshCw, Search, ShieldCheck, X } from 'lucide-react'
import PageHero from '../shared/ui/PageHero.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import StatusBadge from '../shared/ui/StatusBadge.jsx'
import JsonBlock from '../shared/ui/JsonBlock.jsx'
import DataState from '../shared/ui/DataState.jsx'
import { SECURITY_IMMUNE_COPY } from '../shared/content/terminology.js'
import { runSecurityHuntOnce } from '../features/security/api.js'
import SecurityImmuneCommandRail from '../features/security/components/SecurityImmuneCommandRail.jsx'
import { useSecurityImmuneQuery } from '../features/security/securityImmuneQueries.js'
import {
  SECURITY_INCIDENT_FOCUS_OPTIONS,
  SECURITY_MIN_RISK_OPTIONS,
  buildSecurityImmuneViewModel,
  filterSecurityIncidentRows,
} from '../features/security/securityImmuneModel.js'
import useAppStore from '../store/useAppStore.js'

const INCIDENT_FOCUS_LABELS = {
  all: '全部',
  high_plus: 'High+',
  needs_evidence: '缺证据',
}

function toneClass(tone) {
  return {
    green: 'border-green-100 bg-green-50 text-green-700',
    blue: 'border-blue-100 bg-blue-50 text-blue-700',
    amber: 'border-amber-100 bg-amber-50 text-amber-700',
    orange: 'border-orange-100 bg-orange-50 text-orange-700',
    red: 'border-red-100 bg-red-50 text-red-700',
  }[tone] || 'border-gray-100 bg-gray-50 text-gray-600'
}

function IncidentTriageSummary({ triage, visibleCount, totalCount, onRunHunt, runningHunt }) {
  return (
    <section aria-label="Security Immune Triage" className={`rounded-lg border p-3 ${toneClass(triage.tone)}`}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wide opacity-80">
            <ShieldCheck size={13} />
            Incident Triage
          </div>
          <div className="mt-1 text-base font-bold text-gray-950">{triage.title}</div>
          <div className="mt-1 text-xs leading-5 text-gray-600">{triage.detail}</div>
          <div className="mt-2 text-xs font-semibold text-gray-800">下一步：{triage.action}</div>
        </div>
        <div className="grid min-w-[220px] grid-cols-3 gap-2 text-center text-xs">
          <div className="rounded border border-white/70 bg-white/70 px-2 py-2">
            <div className="text-base font-bold text-gray-900">{visibleCount}</div>
            <div className="text-gray-500">visible</div>
          </div>
          <div className="rounded border border-white/70 bg-white/70 px-2 py-2">
            <div className="text-base font-bold text-gray-900">{triage.highPlusCount}</div>
            <div className="text-gray-500">High+</div>
          </div>
          <div className="rounded border border-white/70 bg-white/70 px-2 py-2">
            <div className="text-base font-bold text-gray-900">{triage.needsEvidenceCount}</div>
            <div className="text-gray-500">缺证据</div>
          </div>
        </div>
      </div>
      {totalCount === 0 ? (
        <button
          type="button"
          onClick={onRunHunt}
          disabled={runningHunt}
          className="mt-3 inline-flex items-center gap-1.5 rounded border border-primary-200 bg-white/80 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary-50 disabled:opacity-50"
        >
          <Bug size={13} />
          {runningHunt ? 'Hunt 运行中' : '运行一次 Hunt'}
        </button>
      ) : null}
    </section>
  )
}

function IncidentCard({ incident }) {
  return (
    <article className="rounded-lg border border-gray-100 bg-white p-3 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">{incident.title}</div>
          <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] font-semibold">
            <span className={`rounded-full border px-2 py-0.5 uppercase ${incident.className}`}>{incident.risk}</span>
            <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-slate-600">{incident.status}</span>
            <span className="rounded-full border border-cyan-200 bg-cyan-50 px-2 py-0.5 text-cyan-700">{incident.evidenceLabel}</span>
          </div>
        </div>
        <span className="max-w-[12rem] truncate rounded border border-gray-100 bg-gray-50 px-2 py-0.5 text-[11px] font-semibold text-gray-500">
          {incident.asset}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 text-gray-500">{incident.detail}</p>
      <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50/60 p-2 text-xs leading-5 text-emerald-800">
        <span className="font-semibold">建议：</span>{incident.recommendation}
      </div>
      <div className="mt-2 text-[11px] text-gray-400">source: {incident.source}</div>
    </article>
  )
}

export default function SecurityImmune() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const [minRisk, setMinRisk] = useState('low')
  const [incidentQuery, setIncidentQuery] = useState('')
  const [incidentFocus, setIncidentFocus] = useState('all')
  const [huntResult, setHuntResult] = useState(null)
  const [runningHunt, setRunningHunt] = useState(false)
  const {
    runtime,
    assets,
    incidents,
    partialErrors,
    loading,
    refreshing,
    error,
    refetch,
  } = useSecurityImmuneQuery({
    minRisk,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const busy = loading || refreshing
  const errors = useMemo(() => [
    ...(partialErrors || []),
    ...(error ? [error.message] : []),
  ], [error, partialErrors])
  const {
    heroMetaItems,
    riskCards,
    incidentRows,
    hasIncidentRows,
    runtimeBadge,
    mission,
    gateCards,
    triage,
  } = useMemo(() => buildSecurityImmuneViewModel({
    runtime,
    assets,
    incidents,
    partialErrors,
    loading: busy,
    minRisk,
  }), [assets, busy, incidents, minRisk, partialErrors, runtime])
  const visibleIncidentRows = useMemo(
    () => filterSecurityIncidentRows(incidentRows, { query: incidentQuery, focus: incidentFocus }),
    [incidentFocus, incidentQuery, incidentRows],
  )
  const hasVisibleIncidentRows = visibleIncidentRows.length > 0

  const runHunt = async () => {
    setRunningHunt(true)
    setHuntResult(null)
    try {
      const result = await runSecurityHuntOnce('manual_web_security_immune')
      setHuntResult({ ok: true, data: result })
      await refetch()
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
        eyebrow={SECURITY_IMMUNE_COPY.heroEyebrow}
        title={SECURITY_IMMUNE_COPY.heroTitle}
        description={SECURITY_IMMUNE_COPY.heroDescription}
        gradientClass="from-emerald-50 via-white to-rose-50"
        metaItems={heroMetaItems}
      />

      {errors.length > 0 ? (
        <NoticeBanner variant="warning" title="部分接口降级">
          {errors.join(' | ')}
        </NoticeBanner>
      ) : null}

      <SecurityImmuneCommandRail
        mission={mission}
        gateCards={gateCards}
        minRisk={minRisk}
        onMinRiskChange={setMinRisk}
        onRefresh={() => { void refetch() }}
        onRunHunt={runHunt}
        busy={busy}
        runningHunt={runningHunt}
      />

      {huntResult ? (
        <NoticeBanner variant={huntResult.ok ? 'success' : 'warning'} title={huntResult.ok ? 'Hunt 已完成' : 'Hunt 失败'}>
          {huntResult.ok ? <JsonBlock data={huntResult.data} className="mt-2" /> : huntResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {riskCards.map((card) => (
          <div key={card.risk} className={`rounded-lg border px-3 py-2 shadow-sm ${card.className}`}>
            <div className="text-lg font-bold text-gray-800">{card.value}</div>
            <div className="text-xs capitalize opacity-80">{card.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section id="security-immune-incidents" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-gray-800">{SECURITY_IMMUNE_COPY.triageTitle}</h2>
              <p className="mt-0.5 text-xs text-gray-500">{SECURITY_IMMUNE_COPY.triageDescription}</p>
            </div>
            <div className="flex items-center gap-2">
              <label htmlFor="security-immune-min-risk" className="sr-only">Minimum risk</label>
              <select
                id="security-immune-min-risk"
                className="rounded border border-gray-300 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-primary-200"
                value={minRisk}
                onChange={(event) => setMinRisk(event.target.value)}
              >
                {SECURITY_MIN_RISK_OPTIONS.map((risk) => (
                  <option key={risk} value={risk}>{risk}</option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => { void refetch() }}
                disabled={busy}
                className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw size={12} />
                刷新
              </button>
            </div>
          </div>
          <div className="space-y-3">
            <IncidentTriageSummary
              triage={triage}
              visibleCount={visibleIncidentRows.length}
              totalCount={incidentRows.length}
              onRunHunt={runHunt}
              runningHunt={runningHunt}
            />
            <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
              <label className="relative min-w-0 flex-1">
                <span className="sr-only">搜索 Security Incidents</span>
                <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-gray-400" />
                <input
                  value={incidentQuery}
                  onChange={(event) => setIncidentQuery(event.target.value)}
                  placeholder="搜索标题、资产、source 或建议动作..."
                  className="w-full rounded-lg border border-gray-200 bg-white py-2 pl-8 pr-8 text-sm text-gray-700 outline-none focus:border-primary-200 focus:ring-2 focus:ring-primary-100"
                />
                {incidentQuery ? (
                  <button
                    type="button"
                    onClick={() => setIncidentQuery('')}
                    className="absolute right-2 top-2 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                    aria-label="清除 Security Incident 搜索"
                  >
                    <X size={13} />
                  </button>
                ) : null}
              </label>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="inline-flex items-center gap-1 text-xs font-semibold text-gray-400">
                  <Filter size={12} />
                  focus
                </span>
                {SECURITY_INCIDENT_FOCUS_OPTIONS.map((focus) => (
                  <button
                    key={focus}
                    type="button"
                    onClick={() => setIncidentFocus(focus)}
                    className={`rounded-full border px-2.5 py-1 text-xs font-semibold transition ${
                      incidentFocus === focus
                        ? 'border-primary-200 bg-primary-50 text-primary'
                        : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'
                    }`}
                  >
                    {INCIDENT_FOCUS_LABELS[focus] || focus}
                  </button>
                ))}
              </div>
            </div>
            <DataState
              loading={loading && !incidents}
              state={!loading && !hasVisibleIncidentRows ? 'no_sample' : undefined}
              title="当前风险阈值下没有 incident"
              hint={hasIncidentRows ? '当前搜索或本地 focus 没有匹配项，可以清除过滤。' : '可以降低 min risk，或运行一次 Hunt 拉取最新安全样本。'}
              compact
            >
              {visibleIncidentRows.map((incident) => (
                <IncidentCard key={incident.key} incident={incident} />
              ))}
            </DataState>
          </div>
        </section>

        <aside className="space-y-4">
          <section id="security-immune-runtime" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold text-gray-800">{SECURITY_IMMUNE_COPY.runtimeTitle}</h2>
              <StatusBadge status={runtimeBadge.status} label={runtimeBadge.label} />
            </div>
            <JsonBlock data={runtime || { loading: busy }} />
          </section>

          <section id="security-immune-hunt" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <Bug size={16} className="text-primary" />
              <h2 className="text-base font-semibold text-gray-800">{SECURITY_IMMUNE_COPY.manualHuntTitle}</h2>
            </div>
            <p className="mb-3 text-xs leading-5 text-gray-500">{SECURITY_IMMUNE_COPY.manualHuntDescription}</p>
            <button
              type="button"
              disabled={runningHunt}
              onClick={runHunt}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-white hover:bg-primary-700 disabled:opacity-50"
            >
              {runningHunt ? '运行中...' : 'Run Once'}
            </button>
          </section>

          <section id="security-immune-assets" className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
            <h2 className="mb-3 text-base font-semibold text-gray-800">{SECURITY_IMMUNE_COPY.assetsTitle}</h2>
            <JsonBlock data={assets || {}} />
          </section>
        </aside>
      </div>
    </div>
  )
}
