import React, { useCallback, useEffect, useState } from 'react'
import DigitalLifeRuntimeCard from '../../life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import LivingPulseStrip from '../../../shared/ui/LivingPulseStrip.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../../../models/loadState.js'
import { formatPercent } from '../../../shared/formatters.js'
import { AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES, resolveStatusClass } from '../../../shared/statusMeta.js'
import {
  useImmuneGateReplayQuery,
  useImmuneGateStatsQuery,
} from '../automationQueries.js'

function ImmuneGateOpsPanel({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  initialReplayRequestId = '',
}) {
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [replayRequestId, setReplayRequestId] = useState('')
  const [activeReplayRequestId, setActiveReplayRequestId] = useState('')
  const {
    stats,
    statsError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useImmuneGateStatsQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    replayPayload,
    replayError,
    loading: replayLoading,
    refreshing: replayRefreshing,
    lastUpdatedAt: replayUpdatedAt,
    refetch: refetchReplay,
  } = useImmuneGateReplayQuery({
    requestId: activeReplayRequestId,
    limit: 120,
  })

  useEffect(() => {
    const rid = String(initialReplayRequestId || '').trim()
    if (!rid) return
    setReplayRequestId(rid)
    setActiveReplayRequestId(rid)
  }, [initialReplayRequestId])

  const handleReplayLoad = useCallback((requestId) => {
    const rid = String(requestId || replayRequestId || '').trim()
    if (!rid) return
    setReplayRequestId(rid)
    if (rid === activeReplayRequestId) {
      void refetchReplay()
      return
    }
    setActiveReplayRequestId(rid)
  }, [activeReplayRequestId, refetchReplay, replayRequestId])

  const runtimeState = stats?.runtime_state || {}
  const counters = runtimeState?.counters || {}
  const reasonCodes = Array.isArray(stats?.reason_codes) ? stats.reason_codes : []
  const recent = Array.isArray(stats?.recent) ? stats.recent : []
  const runtimeStatus = String(runtimeState?.status || 'unknown').toLowerCase()
  const statusClass = resolveStatusClass(
    AUTOMATION_IMMUNE_RUNTIME_STATUS_CLASSES,
    runtimeStatus,
    'border-gray-200 bg-gray-50 text-gray-600',
  )
  const immunePulseNodes = [
    {
      label: '运行态',
      value: runtimeStatus.toUpperCase() || 'UNKNOWN',
      tone: 'primary',
      active: runtimeStatus === 'live',
    },
    {
      label: '总事件',
      value: String(stats?.total ?? 0),
      tone: 'teal',
      active: (stats?.total ?? 0) > 0,
    },
    {
      label: '允许',
      value: String(stats?.allow ?? 0),
      tone: 'teal',
      active: (stats?.allow ?? 0) > 0,
    },
    {
      label: '报告',
      value: String(stats?.report ?? 0),
      tone: 'amber',
      active: (stats?.report ?? 0) > 0,
    },
    {
      label: '拦截',
      value: String(stats?.block ?? 0),
      tone: 'rose',
      active: (stats?.block ?? 0) > 0,
    },
    {
      label: '拦截率',
      value: formatPercent(stats?.block_rate, 0),
      tone: 'violet',
      active: Number(stats?.block_rate ?? 0) > 0,
    },
  ]
  const immuneLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: statsError && !stats ? new Error(statsError) : null,
    hasData: Boolean(stats),
    hasSamples: (stats?.total ?? 0) > 0 || recent.length > 0 || reasonCodes.length > 0,
    available: Boolean(stats),
    stale,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <div className="text-sm font-semibold text-gray-700">护栏与回放</div>
          <div className="text-xs text-gray-500">运行态、事件统计与原因排行。</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="rounded-lg border border-primary-100/70 bg-white/80 px-2.5 py-1.5 text-xs text-gray-600"
            value={windowMinutes}
            onChange={(event) => setWindowMinutes(Number(event.target.value))}
          >
            <option value={60}>60m</option>
            <option value={360}>6h</option>
            <option value={1440}>24h</option>
            <option value={10080}>7d</option>
          </select>
          <button
            onClick={() => Promise.allSettled([
              refetch(),
              activeReplayRequestId ? refetchReplay() : Promise.resolve(),
            ])}
            disabled={loading || refreshing || replayLoading || replayRefreshing}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {loading || refreshing || replayLoading || replayRefreshing ? '刷新中…' : '刷新'}
          </button>
          <input
            value={replayRequestId}
            onChange={(event) => setReplayRequestId(event.target.value)}
            placeholder="request_id"
            className="w-44 rounded-lg border border-primary-100/70 bg-white/80 px-2.5 py-1.5 text-xs text-gray-600"
          />
          <button
            onClick={() => handleReplayLoad(replayRequestId)}
            disabled={replayLoading || !String(replayRequestId || '').trim()}
            className="btn-secondary border-violet-200 text-xs text-violet-700 disabled:opacity-40"
          >
            {replayLoading || replayRefreshing ? '回放中…' : '回放'}
          </button>
          <a
            href="#/operations/prompt?keyword=immune_agent.runtime_rules&scope=immune_agent"
            className="btn-secondary border-blue-200 text-xs text-blue-700"
          >
            编辑规则
          </a>
        </div>
      </div>

      <LivingPulseStrip
        title="免疫脉冲"
        subtitle="把免疫门禁的运行态、事件总量和拦截/报告分布压成一条可读节律。"
        nodes={immunePulseNodes}
      />

      <SampleMeta
        loadState={immuneLoadState}
        windowMinutes={windowMinutes}
        sampleSize={stats?.total ?? 0}
        sampleLabel="免疫事件"
        updatedAt={lastUpdatedAt}
        source="immune gate"
        definition="这一段聚合 immune gate 运行态、原因热区、近期事件和指定 request 的免疫回放。"
      />

      {statsError ? (
        <NoticeBanner variant={stats ? 'warning' : 'error'}>
          {statsError}
        </NoticeBanner>
      ) : null}

      {!statsError && !loading && !refreshing && immuneLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前窗口还没有 immune gate 事件样本。</NoticeBanner>
      ) : null}

      {replayError ? <NoticeBanner variant="error">{replayError}</NoticeBanner> : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass}`}>
            {runtimeStatus.toUpperCase() || 'UNKNOWN'}
          </div>
          <div className="mt-1 text-xs text-gray-400">运行态</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="text-lg font-bold text-gray-700">{stats?.total ?? 0}</div>
          <div className="text-xs text-gray-400">总事件</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="text-lg font-bold text-green-600">{stats?.allow ?? 0}</div>
          <div className="text-xs text-gray-400">允许</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="text-lg font-bold text-amber-600">{stats?.report ?? 0}</div>
          <div className="text-xs text-gray-400">报告</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="text-lg font-bold text-red-500">{stats?.block ?? 0}</div>
          <div className="text-xs text-gray-400">拦截</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="text-lg font-bold text-red-500">{stats?.block_rate ?? 0}%</div>
          <div className="text-xs text-gray-400">拦截率</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">运行配置</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">enabled</div>
              <div className="mt-1 font-semibold text-gray-800">{String(runtimeState?.enabled ?? '-')}</div>
            </div>
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">blocking</div>
              <div className="mt-1 font-semibold text-gray-800">{String(runtimeState?.blocking_enabled ?? '-')}</div>
            </div>
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">warn / block</div>
              <div className="mt-1 font-semibold text-gray-800">{runtimeState?.warn_threshold ?? '-'} / {runtimeState?.block_threshold ?? '-'}</div>
            </div>
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">rules</div>
              <div className="mt-1 font-semibold text-gray-800">{runtimeState?.template_rule_count ?? 0} / {runtimeState?.total_rule_count ?? 0}</div>
            </div>
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">last decision</div>
              <div className="mt-1 font-semibold text-gray-800">{runtimeState?.last_decision || '-'}</div>
            </div>
            <div className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="text-gray-400">last risk</div>
              <div className="mt-1 font-semibold text-gray-800">{runtimeState?.last_risk_level || '-'}</div>
            </div>
          </div>
          <div className="mt-3 text-xs text-gray-500">
            counter(total/allow/report/block): {counters.total ?? 0} / {counters.allow ?? 0} / {counters.report ?? 0} / {counters.block ?? 0}
          </div>
        </div>

        <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">原因热区</div>
          {reasonCodes.length > 0 ? (
            <div className="mt-3 space-y-2">
              {reasonCodes.slice(0, 6).map((item) => (
                <div key={item.reason_code} className="rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-orange-600">{item.reason_code}</span>
                    <span className="text-gray-500">{item.count} ({item.pct}%)</span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-orange-400 to-amber-400"
                      style={{ width: `${Math.min(Number(item.pct ?? 0), 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无原因热区数据" hint="等待免疫事件积累后展示。" className="mt-3 p-4" />
          )}
        </div>

        <div className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">近期事件</div>
          {recent.length > 0 ? (
            <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
              {recent.map((item, index) => (
                <button
                  key={`${item.request_id}-${item.ts_ms}-${index}`}
                  onClick={() => handleReplayLoad(item.request_id)}
                  className="w-full rounded-xl border border-primary-100/70 bg-white/70 px-3 py-2 text-left text-xs transition hover:border-violet-200 hover:bg-violet-50/60"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-mono text-blue-600">{item.request_id || '-'}</div>
                    <div className="text-gray-500">{item.decision} / {item.risk_level || 'none'}</div>
                  </div>
                  <div className="mt-1 text-gray-500">
                    {item.event_type} · score={item.score ?? '-'} · {item.reason_codes?.join(', ') || '-'}
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无免疫事件" hint="当前时间窗内没有免疫门禁事件。" className="mt-3 p-4" />
          )}
        </div>
      </div>

      {replayLoading || replayRefreshing || replayPayload || activeReplayRequestId ? (
        <div className="rounded-xl border border-violet-200 bg-violet-50/40 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs font-semibold text-violet-800">免疫回放</div>
            {replayPayload?.request_id ? (
              <a
                href={`#/observability/trace/${encodeURIComponent(replayPayload.request_id)}`}
                className="btn-secondary border-blue-200 px-2 py-0.5 text-[11px] text-blue-700"
              >
                打开链路
              </a>
            ) : null}
          </div>
          <div className="mb-2">
            <SampleMeta
              loadState={activeReplayRequestId ? (replayLoading || replayRefreshing ? LOAD_STATES.LOADING : replayPayload ? LOAD_STATES.READY : replayError ? LOAD_STATES.ERROR : LOAD_STATES.UNAVAILABLE) : LOAD_STATES.UNAVAILABLE}
              sampleSize={replayPayload?.summary?.event_count ?? 0}
              sampleLabel="回放事件"
              updatedAt={replayUpdatedAt}
              source={activeReplayRequestId ? `request ${activeReplayRequestId}` : 'immune replay'}
              definition="指定 request 的 immune 决策时间线，用于回看风险判定、命中规则和阶段演化。"
            />
          </div>
          {replayLoading && !replayPayload ? (
            <div className="text-xs text-gray-500">正在加载回放时间线…</div>
          ) : !replayLoading && !replayRefreshing && activeReplayRequestId && !replayPayload && !replayError ? (
            <div className="text-xs text-gray-500">当前请求还没有可展示的免疫回放。</div>
          ) : null}
          {replayPayload ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
                  请求 ID: <span className="font-mono">{replayPayload.request_id}</span>
                </div>
                <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
                  会话 ID: <span className="font-mono">{replayPayload.session_id || '-'}</span>
                </div>
                <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
                  首次→末次: {replayPayload.summary?.first_decision || '-'} → {replayPayload.summary?.last_decision || '-'}
                </div>
                <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
                  事件数/最高分: {replayPayload.summary?.event_count ?? 0} / {replayPayload.summary?.max_score ?? '-'}
                </div>
              </div>
              {replayPayload.input_preview ? (
                <div className="rounded border border-violet-100 bg-white/90 px-2 py-1.5 text-xs text-gray-700">
                  输入预览: {replayPayload.input_preview}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-1.5">
                {(replayPayload.summary?.reason_codes || []).slice(0, 8).map((reasonCode) => (
                  <span key={`replay-reason-${reasonCode}`} className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                    {reasonCode}
                  </span>
                ))}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(replayPayload.summary?.matched_rules || []).slice(0, 6).map((rule) => (
                  <span key={`replay-rule-${rule}`} className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700">
                    {rule}
                  </span>
                ))}
              </div>
              <div className="max-h-56 space-y-1.5 overflow-y-auto rounded border border-violet-100 bg-white/90 p-2">
                {(replayPayload.decisions || []).map((item, index) => (
                  <div key={`${item.ts_ms}-${item.event_type}-${index}`} className="rounded border border-gray-100 bg-gray-50 px-2 py-1.5 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-mono text-gray-700">{item.event_type}</div>
                      <div className="text-gray-500">{item.decision} / {item.risk_level || 'none'}</div>
                    </div>
                    <div className="mt-1 text-gray-500">
                      {item.stage || '-'} · score={item.score ?? '-'} · {item.reason_codes?.join(', ') || '-'}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export default function AutomationImmuneSectionContent({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  initialReplayRequestId = '',
  runtimeSnapshot,
  promptStats,
}) {
  return (
    <>
      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
      <ImmuneGateOpsPanel
        pollingEnabled={pollingEnabled}
        pollingInterval={pollingInterval}
        pollingTick={pollingTick}
        initialReplayRequestId={initialReplayRequestId}
      />
    </>
  )
}
