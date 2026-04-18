import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, History, ListChecks, Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  triggerSchedulerTask,
} from '../api/automation.js'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import {
  useAutomationOverviewQuery,
  useImmuneGateReplayQuery,
  useImmuneGateStatsQuery,
  useSchedulerHistoryQuery,
} from '../queries/automationQueries.js'
import useAppStore from '../store/useAppStore.js'

const STATUS_STYLES = {
  PENDING: { bg: 'bg-gray-100', text: 'text-gray-600', dot: 'bg-gray-400' },
  RUNNING: { bg: 'bg-blue-100', text: 'text-blue-700', dot: 'bg-blue-500 animate-pulse' },
  SUCCESS: { bg: 'bg-green-100', text: 'text-green-700', dot: 'bg-green-500' },
  FAILED: { bg: 'bg-red-100', text: 'text-red-700', dot: 'bg-red-500' },
  DISABLED: { bg: 'bg-gray-100', text: 'text-gray-400', dot: 'bg-gray-300' },
  CANCELLED: { bg: 'bg-yellow-100', text: 'text-yellow-700', dot: 'bg-yellow-400' },
}

const PRIORITY_STYLES = {
  HIGH: 'text-red-600 bg-red-50',
  MEDIUM: 'text-blue-600 bg-blue-50',
  LOW: 'text-gray-500 bg-gray-50',
}

function formatPercent(value, digits = 0) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '—'
  return `${numeric.toFixed(digits)}%`
}

function AutomationSection({ icon: Icon, title, description, actions = null, children }) {
  return (
    <section className="card space-y-4 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {Icon ? (
            <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2.5 text-primary">
              <Icon size={16} />
            </div>
          ) : null}
          <div>
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            {description ? <p className="mt-1 text-xs text-gray-500">{description}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
      {children}
    </section>
  )
}

function TaskCard({ item, onEnable, onDisable, onTrigger, actionLoading }) {
  const { task, health } = item
  const status = health?.status || 'PENDING'
  const style = STATUS_STYLES[status] || STATUS_STYLES.PENDING
  const isLoading = actionLoading === task.task_id
  const lastReasonCode = String(health?.last_reason_code || '').trim()
  const lastGateState = String(health?.last_gate_state || '').trim()
  const lastMoodState = String(health?.last_mood_state || '').trim()
  const lastImmuneDecision = String(health?.last_immune_decision || '').trim().toUpperCase()
  const lastImmuneRisk = String(health?.last_immune_risk_level || '').trim().toLowerCase()
  const lastImmuneReasonCodes = Array.isArray(health?.last_immune_reason_codes)
    ? health.last_immune_reason_codes.filter((code) => String(code || '').trim()).slice(0, 3)
    : []
  const lastCortisol = Number.isFinite(Number(health?.last_endocrine_cortisol))
    ? Number(health?.last_endocrine_cortisol).toFixed(2)
    : '—'
  const lastEnergy = Number.isFinite(Number(health?.last_endocrine_energy))
    ? Number(health?.last_endocrine_energy).toFixed(2)
    : '—'

  return (
    <div className={`card-hover space-y-3 p-4 ${task.enabled ? '' : 'opacity-75 saturate-75'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 shrink-0 rounded-full ${style.dot}`} />
            <span className="truncate text-sm font-medium text-gray-800">{task.task_id}</span>
          </div>
          {task.description ? <div className="ml-4 mt-0.5 truncate text-xs text-gray-500">{task.description}</div> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${PRIORITY_STYLES[task.priority] || PRIORITY_STYLES.MEDIUM}`}>
            {task.priority}
          </span>
          <span className={`rounded px-1.5 py-0.5 text-xs ${style.bg} ${style.text}`}>{status}</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.run_count ?? 0}</div>
          <div className="text-gray-400">节律次数</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.error_count ?? 0}</div>
          <div className="text-gray-400">失衡次数</div>
        </div>
        <div className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
          <div className="font-bold text-gray-700">{task.trigger_mode || '—'}</div>
          <div className="text-gray-400">触发方式</div>
        </div>
      </div>

      {health?.last_error ? <NoticeBanner variant="error">✕ {health.last_error}</NoticeBanner> : null}
      {lastReasonCode ? (
        <NoticeBanner variant={lastReasonCode.includes('BLOCKED') ? 'warning' : 'info'}>
          reason={lastReasonCode}
          {lastGateState ? ` · gate=${lastGateState}` : ''}
          {lastMoodState ? ` · mood=${lastMoodState}` : ''}
          {lastImmuneDecision ? ` · immune=${lastImmuneDecision}${lastImmuneRisk ? `/${lastImmuneRisk}` : ''}` : ''}
          {lastImmuneReasonCodes.length > 0 ? ` · immune_reason=${lastImmuneReasonCodes.join('|')}` : ''}
          {` · cortisol=${lastCortisol} · energy=${lastEnergy}`}
        </NoticeBanner>
      ) : null}

      <div className="flex gap-2">
        <button
          onClick={() => onTrigger(task.task_id)}
          disabled={isLoading || !task.enabled}
          className="btn-secondary flex-1 justify-center border-blue-200 text-xs text-blue-700 disabled:opacity-40"
        >
          {isLoading ? '处理中…' : '▶ 立即触发'}
        </button>
        {task.enabled ? (
          <button
            onClick={() => onDisable(task.task_id)}
            disabled={isLoading}
            className="btn-secondary flex-1 justify-center text-xs disabled:opacity-40"
          >
            暂停
          </button>
        ) : (
          <button
            onClick={() => onEnable(task.task_id)}
            disabled={isLoading}
            className="btn-secondary flex-1 justify-center border-green-200 text-xs text-green-700 disabled:opacity-40"
          >
            启用
          </button>
        )}
      </div>
      <div className="lifeline-rail h-1">
        <div className="lifeline-wave h-full w-full" />
      </div>
    </div>
  )
}

function SchedulerTimelineChart({ tasks }) {
  if (!tasks || tasks.length === 0) return null
  const maxRun = Math.max(...tasks.map((task) => task.run_count ?? 0), 1)

  return (
    <div className="mt-4">
      <div className="mb-3 text-xs font-semibold text-gray-600">各节律执行量对比（近 24h）</div>
      <div className="space-y-2">
        {tasks.map((task) => {
          const runPct = Math.max(((task.run_count ?? 0) / maxRun) * 100, 1)
          const errPct = task.run_count > 0 ? Math.min(((task.error_count ?? 0) / task.run_count) * 100, 100) : 0
          return (
            <div key={task.task_id} className="flex items-center gap-2">
              <div className="w-40 shrink-0 truncate text-xs text-gray-600" title={task.task_id}>
                {task.task_id}
              </div>
              <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-gray-100">
                <div className="absolute left-0 top-0 h-3 rounded-full bg-green-400 transition-all" style={{ width: `${runPct}%` }} />
                {errPct > 0 ? (
                  <div className="absolute left-0 top-0 h-3 rounded-full bg-red-400 transition-all" style={{ width: `${(runPct * errPct) / 100}%` }} />
                ) : null}
              </div>
              <div className="w-20 shrink-0 text-right text-xs text-gray-500">
                {task.run_count ?? 0} 次
                {task.error_count > 0 ? <span className="ml-1 text-red-500">/ {task.error_count} 失败</span> : null}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs text-gray-400">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-green-400" />
          执行次数
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-red-400" />
          失败次数（叠加）
        </span>
      </div>
    </div>
  )
}

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
  const statusClass = runtimeStatus === 'live'
    ? 'border-green-200 bg-green-50 text-green-700'
    : runtimeStatus === 'degraded'
      ? 'border-orange-200 bg-orange-50 text-orange-700'
      : 'border-gray-200 bg-gray-50 text-gray-600'
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
            href="#/prompt-config?keyword=immune_agent.runtime_rules&scope=immune_agent"
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
                href={`#/trace/${encodeURIComponent(replayPayload.request_id)}`}
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

function SchedulerHistorySection({
  pollingEnabled,
  pollingInterval,
  pollingTick,
}) {
  const {
    history: data,
    historyError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useSchedulerHistoryQuery({
    windowMinutes: 1440,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const historyLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: historyError && !data ? new Error(historyError) : null,
    hasData: Boolean(data),
    hasSamples: (data?.total_tasks ?? 0) > 0 || (data?.tasks ?? []).length > 0,
    available: Boolean(data),
    stale,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-700">节律历史脉络（近 24h）</div>
        <button
          onClick={() => refetch()}
          disabled={loading || refreshing}
          className="btn-secondary text-xs disabled:opacity-50"
        >
          {loading || refreshing ? '刷新中…' : '刷新'}
        </button>
      </div>

      <SampleMeta
        loadState={historyLoadState}
        windowMinutes={1440}
        sampleSize={data?.total_tasks ?? 0}
        sampleLabel="历史任务"
        updatedAt={lastUpdatedAt}
        source="scheduler history"
        definition="汇总近 24h 的节律任务执行摘要、门控阻断模式和单任务最近体征。"
      />

      {loading && !data ? (
        <NoticeBanner variant="info" title="加载中">
          正在同步节律历史脉络…
        </NoticeBanner>
      ) : null}

      {historyError ? <NoticeBanner variant={data ? 'warning' : 'error'}>{historyError}</NoticeBanner> : null}

      {!historyError && !loading && !refreshing && historyLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">近 24 小时还没有调度历史样本。</NoticeBanner>
      ) : null}

      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {[
          { label: '总任务', value: data?.total_tasks, color: 'text-gray-700' },
          { label: '运行中', value: data?.running_count, color: 'text-blue-600' },
          { label: '成功', value: data?.success_count, color: 'text-green-600' },
          { label: '失败', value: data?.failed_count, color: 'text-red-500' },
          { label: '已禁用', value: data?.disabled_count, color: 'text-gray-400' },
          {
            label: '整体错误率',
            value: data?.overall_error_rate != null ? `${data.overall_error_rate}%` : '—',
            color: data?.overall_error_rate > 20 ? 'text-red-500' : 'text-green-600',
          },
          {
            label: '门控拦截',
            value: data?.gate_blocked_total ?? '—',
            color: (data?.gate_blocked_total ?? 0) > 0 ? 'text-orange-500' : 'text-green-600',
          },
          {
            label: '门控拦截率',
            value: data?.gate_blocked_rate != null ? `${data.gate_blocked_rate}%` : '—',
            color: (data?.gate_blocked_rate ?? 0) > 20 ? 'text-red-500' : 'text-gray-500',
          },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl border border-primary-100/70 bg-white/70 p-2 text-center">
            <div className={`text-lg font-bold ${color}`}>{value ?? '—'}</div>
            <div className="mt-0.5 text-xs text-gray-400">{label}</div>
          </div>
        ))}
      </div>

      {(data?.blocked_reason_codes?.length > 0 || data?.gate_states?.length > 0) ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-primary-100/80 bg-white/75 p-3">
            <div className="mb-2 text-xs font-semibold text-gray-600">门控原因 Top</div>
            <div className="space-y-1.5">
              {(data?.blocked_reason_codes || []).slice(0, 5).map((item) => (
                <div key={item.reason_code} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-orange-600">{item.reason_code}</span>
                  <span className="text-gray-500">{item.count} ({item.pct}%)</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-primary-100/80 bg-white/75 p-3">
            <div className="mb-2 text-xs font-semibold text-gray-600">Gate 分布</div>
            <div className="space-y-1.5">
              {(data?.gate_states || []).slice(0, 5).map((item) => (
                <div key={item.gate_state} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-blue-600">{item.gate_state}</span>
                  <span className="text-gray-500">{item.count} ({item.pct}%)</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {!loading && (data?.tasks ?? []).length > 0 ? (
        <>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            {(data?.tasks ?? []).map((task) => {
              const status = String(task.status || 'UNKNOWN').toUpperCase()
              const statusTone = status === 'SUCCESS'
                ? 'border-emerald-200 bg-emerald-50/70 text-emerald-700'
                : status === 'FAILED'
                  ? 'border-rose-200 bg-rose-50/70 text-rose-700'
                  : status === 'RUNNING'
                    ? 'border-sky-200 bg-sky-50/70 text-sky-700'
                    : 'border-gray-200 bg-gray-50 text-gray-600'
              return (
                <div key={task.task_id} className="rounded-2xl border border-primary-100/80 bg-white/75 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-mono text-sm font-semibold text-gray-900">{task.task_id}</div>
                      <div className="mt-1 text-xs text-gray-500">
                        {task.last_reason_code || '暂无最近原因'}
                      </div>
                    </div>
                    <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${statusTone}`}>
                      {status}
                    </span>
                  </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div className="rounded-xl border border-primary-100/70 bg-white/70 px-2.5 py-2 text-center">
                      <div className="font-semibold text-gray-800">{task.run_count}</div>
                      <div className="text-gray-400">节律次数</div>
                    </div>
                    <div className="rounded-xl border border-primary-100/70 bg-white/70 px-2.5 py-2 text-center">
                      <div className="font-semibold text-gray-800">{task.error_count}</div>
                      <div className="text-gray-400">失衡次数</div>
                    </div>
                    <div className="rounded-xl border border-primary-100/70 bg-white/70 px-2.5 py-2 text-center">
                      <div className="font-semibold text-gray-800">{task.error_rate != null ? `${task.error_rate}%` : '—'}</div>
                      <div className="text-gray-400">错误率</div>
                    </div>
                  </div>
                  <div className="mt-3 space-y-2 text-xs text-gray-500">
                    <div>Gate: {task.last_gate_state || '—'}</div>
                    <div>
                      Immune: {task.last_immune_decision
                        ? `${task.last_immune_decision}${task.last_immune_risk_level ? `/${task.last_immune_risk_level}` : ''}`
                        : '—'}
                    </div>
                    <div className="truncate" title={Array.isArray(task.last_immune_reason_codes) && task.last_immune_reason_codes.length > 0 ? task.last_immune_reason_codes.join(', ') : '—'}>
                      Immune Reason: {Array.isArray(task.last_immune_reason_codes) && task.last_immune_reason_codes.length > 0
                        ? task.last_immune_reason_codes.join(', ')
                        : '—'}
                    </div>
                    <div className="truncate" title={task.last_error || '—'}>
                      最近错误: {task.last_error || '—'}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <SchedulerTimelineChart tasks={data.tasks} />
        </>
      ) : !loading ? (
        <EmptyStateCard title="暂无节律历史" hint="等待任务运行后会出现脉络数据。" className="p-6" />
      ) : null}
    </div>
  )
}

function LoadingTasksNotice() {
  return (
    <NoticeBanner variant="info" title="加载中">
      正在同步节律任务脉络…
    </NoticeBanner>
  )
}

export default function Automation() {
  const [searchParams, setSearchParams] = useSearchParams()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const {
    runtimeSnapshot,
    promptStats,
    loading: runtimeLoading,
    error: runtimeError,
    reload: reloadRuntime,
  } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })
  const [actionLoading, setActionLoading] = useState('')
  const [actionResult, setActionResult] = useState(null)
  const {
    tasks,
    health,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useAutomationOverviewQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    refetch: refetchHistory,
  } = useSchedulerHistoryQuery({
    windowMinutes: 1440,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const reasonFilter = String(searchParams.get('reason_code') || '').trim().toUpperCase()
  const gateFilter = String(searchParams.get('gate_state') || '').trim().toLowerCase()
  const immuneReplayRequestId = String(searchParams.get('immune_replay_request_id') || '').trim()
  const hasTaskFilters = Boolean(reasonFilter || gateFilter)
  const automationLoadState = resolveLoadState({
    loading: loading || refreshing,
    error: overviewError && !tasks.length && !health ? new Error(overviewError) : null,
    hasData: Boolean(tasks.length > 0 || health),
    hasSamples: tasks.length > 0,
    available: Boolean(tasks.length > 0 || health),
    stale,
  })

  const refreshAll = useCallback(async () => {
    await Promise.allSettled([
      refetch(),
      refetchHistory(),
      reloadRuntime(),
    ])
  }, [refetch, refetchHistory, reloadRuntime])

  const handleAction = async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable') await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await Promise.allSettled([refetch(), refetchHistory()])
    } catch (actionError) {
      setActionResult({
        ok: false,
        taskId,
        action,
        error: actionError.message || '任务操作失败',
      })
    } finally {
      setActionLoading('')
    }
  }

  const clearTaskFilters = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('reason_code')
    next.delete('gate_state')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const filteredTasks = useMemo(() => {
    if (!hasTaskFilters) return tasks
    return tasks.filter((item) => {
      const taskReason = String(item?.health?.last_reason_code || '').trim().toUpperCase()
      const taskGate = String(item?.health?.last_gate_state || '').trim().toLowerCase()
      if (reasonFilter && taskReason !== reasonFilter) return false
      if (gateFilter && taskGate !== gateFilter) return false
      return true
    })
  }, [tasks, hasTaskFilters, reasonFilter, gateFilter])

  const enabledCount = useMemo(() => tasks.filter((item) => item.task?.enabled).length, [tasks])
  const runningCount = useMemo(() => tasks.filter((item) => item.health?.status === 'RUNNING').length, [tasks])
  const failedCount = useMemo(() => tasks.filter((item) => item.health?.status === 'FAILED').length, [tasks])
  const filteredCount = filteredTasks.length
  const schedulerState = useMemo(() => {
    const rawStatus = String(health?.scheduler_status || '').toUpperCase()
    if (typeof health?.running === 'boolean') {
      if (!health.running) {
        return { running: false, label: '已停止', title: '节律当前未运行' }
      }
      if (rawStatus === 'DEGRADED') {
        return { running: true, label: '降级运行', title: '节律降级运行中' }
      }
      return { running: true, label: '运行中', title: '节律正在运行' }
    }
    if (rawStatus === 'DEGRADED') {
      return { running: true, label: '降级运行', title: '节律降级运行中' }
    }
    if (['HEALTHY', 'RUNNING', 'UP', 'OK'].includes(rawStatus)) {
      return { running: true, label: '运行中', title: '节律正在运行' }
    }
    if (['STOPPED', 'DOWN', 'DISABLED', 'OFFLINE'].includes(rawStatus)) {
      return { running: false, label: '已停止', title: '节律当前未运行' }
    }
    return { running: false, label: '未知', title: '节律状态未知' }
  }, [health])

  const actionNameMap = {
    enable: '启用',
    disable: '暂停',
    trigger: '立即触发',
  }
  const schedulerPulseNodes = useMemo(() => ([
    {
      label: '节律态',
      value: schedulerState.label,
      tone: schedulerState.running ? 'primary' : 'gray',
      active: schedulerState.running,
    },
    {
      label: '任务',
      value: String(tasks.length),
      tone: 'teal',
      active: tasks.length > 0,
    },
    {
      label: '启用',
      value: String(enabledCount),
      tone: 'violet',
      active: enabledCount > 0,
    },
    {
      label: '推进中',
      value: String(runningCount),
      tone: 'amber',
      active: runningCount > 0,
    },
    {
      label: '异常',
      value: String(failedCount),
      tone: 'rose',
      active: failedCount > 0,
    },
    {
      label: '已筛选',
      value: String(filteredCount),
      tone: 'gray',
      active: hasTaskFilters,
    },
  ]), [enabledCount, failedCount, filteredCount, hasTaskFilters, runningCount, schedulerState.label, schedulerState.running, tasks.length])

  return (
    <div className="space-y-6">
      <div className="card space-y-3 p-5 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-gray-800">器官执行台</h1>
            <p className="mt-0.5 text-sm text-gray-500">统一查看数字生命体常驻节律任务的状态、历史和手动触发入口。</p>
          </div>
          <button
            onClick={refreshAll}
            disabled={loading || refreshing || runtimeLoading}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {loading || refreshing || runtimeLoading ? '刷新中…' : '刷新'}
          </button>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 md:grid-cols-4">
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">任务总数：{tasks.length}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">已启用：{enabledCount}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">推进中：{runningCount}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">异常任务：{failedCount}</div>
        </div>
      </div>

      <PageHero
        icon={Workflow}
        eyebrow="节律执行与回放"
        title={schedulerState.title}
        description="这里用于收口数字生命体的节律执行与免疫回放，把任务、原因和历史趋势放进同一段生命流里。"
        gradientClass="from-violet-50 via-white to-sky-50"
        metaItems={[
          `节律任务总数：${tasks.length}`,
          `已启用：${enabledCount}`,
          `推进中：${runningCount}`,
          `异常任务：${failedCount}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={automationLoadState}
            sampleSize={tasks.length}
            sampleLabel="节律任务"
            updatedAt={lastUpdatedAt}
            source="scheduler / automation"
            definition="Automation 页汇总节律任务总览、immune gate 运行态与 24h 历史脉络，页面主数据已经统一走 query 层。"
          />
        )}
      />

      {overviewError ? <NoticeBanner variant={tasks.length > 0 || health ? 'warning' : 'error'}>{overviewError}</NoticeBanner> : null}
      {runtimeError ? <NoticeBanner variant="warning">{runtimeError.message || String(runtimeError)}</NoticeBanner> : null}

      {!overviewError && !loading && !refreshing && automationLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">当前还没有已登记的节律任务样本。</NoticeBanner>
      ) : null}

      <LivingPulseStrip
        title="节律脉冲"
        subtitle="先看这组器官节律当前怎么流动，再往下看免疫回放和任务卡。"
        nodes={schedulerPulseNodes}
      />

      <AutomationSection
        icon={Activity}
        title="生命体运行与免疫护栏"
        description="把生命体实时快照、免疫门禁统计和事件回放汇聚在同一段体内流。"
      >
        <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
        <ImmuneGateOpsPanel
          pollingEnabled={pollingEnabled}
          pollingInterval={pollingInterval}
          pollingTick={pollingTick}
          initialReplayRequestId={immuneReplayRequestId}
        />
      </AutomationSection>

      <AutomationSection
        icon={ListChecks}
        title="节律任务执行域"
        description="按器官节律对任务进行观测、筛选与即时干预。"
        actions={hasTaskFilters ? (
          <button onClick={clearTaskFilters} className="btn-secondary text-xs">
            清除筛选
          </button>
        ) : null}
      >
        {hasTaskFilters ? (
          <NoticeBanner variant="info" title="已应用节律筛选">
            <span className="font-mono">
              {reasonFilter ? `reason=${reasonFilter}` : ''}
              {reasonFilter && gateFilter ? ' · ' : ''}
              {gateFilter ? `gate=${gateFilter}` : ''}
            </span>
            <span className="mx-1">，当前命中 {filteredCount} 个任务。</span>
          </NoticeBanner>
        ) : null}

        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            {
              label: '节律状态',
              value: schedulerState.label,
              color: schedulerState.running ? 'text-green-600' : schedulerState.label === '未知' ? 'text-gray-500' : 'text-red-500',
            },
            { label: '任务总数', value: tasks.length, color: 'text-gray-800' },
            { label: '当前展示', value: filteredCount, color: hasTaskFilters ? 'text-violet-600' : 'text-gray-500' },
            { label: '已启用', value: enabledCount, color: 'text-blue-600' },
            {
              label: '推进中任务',
              value: runningCount,
              color: runningCount > 0 ? 'text-blue-600' : 'text-gray-400',
            },
          ].map(({ label, value, color }) => (
            <div key={label} className="rounded-xl border border-primary-100/70 bg-white/70 p-3 text-center">
              <div className={`text-xl font-bold ${color}`}>{value}</div>
              <div className="mt-0.5 text-xs text-gray-500">{label}</div>
            </div>
          ))}
        </div>

        {actionResult ? (
          actionResult.ok ? (
            <NoticeBanner variant="success">
              节律 <code className="font-mono">{actionResult.taskId}</code> 已执行「{actionNameMap[actionResult.action] || actionResult.action}」。
            </NoticeBanner>
          ) : (
            <NoticeBanner variant="error">
              节律 <code className="font-mono">{actionResult.taskId}</code> 操作失败：{actionResult.error}
            </NoticeBanner>
          )
        ) : null}

        {failedCount > 0 ? (
          <NoticeBanner variant="warning" title="检测到异常节律">
            当前有 {failedCount} 个任务处于失败状态，请检查对应日志与依赖状态。
          </NoticeBanner>
        ) : null}

        {loading && tasks.length === 0 ? (
          <LoadingTasksNotice />
        ) : tasks.length === 0 ? (
          <EmptyStateCard title="暂无登记任务" hint="请确认节律任务已注册。" />
        ) : filteredTasks.length === 0 ? (
          <EmptyStateCard title="筛选条件无匹配任务" hint="可清除 reason/gate 筛选后重试。" />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredTasks.map((item) => (
              <TaskCard
                key={item.task?.task_id}
                item={item}
                onEnable={(taskId) => handleAction(taskId, 'enable')}
                onDisable={(taskId) => handleAction(taskId, 'disable')}
                onTrigger={(taskId) => handleAction(taskId, 'trigger')}
                actionLoading={actionLoading}
              />
            ))}
          </div>
        )}
      </AutomationSection>

      <AutomationSection
        icon={History}
        title="节律历史脉络"
        description="回看近 24h 执行体征，识别门控阻断模式与失衡趋势。"
      >
        <SchedulerHistorySection
          pollingEnabled={pollingEnabled}
          pollingInterval={pollingInterval}
          pollingTick={pollingTick}
        />
      </AutomationSection>
    </div>
  )
}
