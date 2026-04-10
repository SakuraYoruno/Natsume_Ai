import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import {
  disableSchedulerTask,
  enableSchedulerTask,
  fetchImmuneGateReplay,
  fetchImmuneGateStats,
  fetchSchedulerHealth,
  fetchSchedulerHistory,
  fetchSchedulerTasks,
  triggerSchedulerTask,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
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
    <div className={`space-y-3 rounded-lg border bg-white p-4 ${task.enabled ? 'border-gray-200' : 'border-gray-100 opacity-75'}`}>
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
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.run_count ?? 0}</div>
          <div className="text-gray-400">执行次数</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{health?.error_count ?? 0}</div>
          <div className="text-gray-400">失败次数</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="font-bold text-gray-700">{task.trigger_mode || '—'}</div>
          <div className="text-gray-400">触发模式</div>
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
          className="flex-1 rounded border border-blue-300 px-2 py-1.5 text-xs text-blue-600 transition-colors hover:bg-blue-50 disabled:opacity-40"
        >
          {isLoading ? '处理中…' : '▶ 立即触发'}
        </button>
        {task.enabled ? (
          <button
            onClick={() => onDisable(task.task_id)}
            disabled={isLoading}
            className="flex-1 rounded border border-gray-300 px-2 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-40"
          >
            暂停
          </button>
        ) : (
          <button
            onClick={() => onEnable(task.task_id)}
            disabled={isLoading}
            className="flex-1 rounded border border-green-300 px-2 py-1.5 text-xs text-green-600 transition-colors hover:bg-green-50 disabled:opacity-40"
          >
            启用
          </button>
        )}
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

function ImmuneGateOpsPanel({ pollingEnabled, pollingInterval, initialReplayRequestId = '' }) {
  const [windowMinutes, setWindowMinutes] = useState(60)
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [replayRequestId, setReplayRequestId] = useState('')
  const [replayLoading, setReplayLoading] = useState(false)
  const [replayError, setReplayError] = useState('')
  const [replayPayload, setReplayPayload] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const payload = await fetchImmuneGateStats({ window_minutes: windowMinutes, limit: 12 })
      setStats(payload)
    } catch (fetchError) {
      setError(fetchError.message || 'immune gate stats load failed')
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(() => {
      load()
    }, Math.max(15, pollingInterval) * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, load])

  const loadReplay = useCallback(async (requestId) => {
    const rid = String(requestId || replayRequestId || '').trim()
    if (!rid) return
    setReplayLoading(true)
    setReplayError('')
    try {
      const payload = await fetchImmuneGateReplay(rid, { limit: 120 })
      setReplayPayload(payload)
      setReplayRequestId(rid)
    } catch (fetchError) {
      setReplayPayload(null)
      setReplayError(fetchError.message || 'immune gate replay load failed')
    } finally {
      setReplayLoading(false)
    }
  }, [replayRequestId])

  useEffect(() => {
    const rid = String(initialReplayRequestId || '').trim()
    if (!rid) return
    setReplayRequestId(rid)
    void loadReplay(rid)
  }, [initialReplayRequestId, loadReplay])

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

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <div className="text-sm font-semibold text-gray-700">护栏与回放</div>
          <div className="text-xs text-gray-500">运行态、事件统计与原因排行。</div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="rounded border border-gray-300 px-2 py-1 text-xs"
            value={windowMinutes}
            onChange={(event) => setWindowMinutes(Number(event.target.value))}
          >
            <option value={60}>60m</option>
            <option value={360}>6h</option>
            <option value={1440}>24h</option>
            <option value={10080}>7d</option>
          </select>
          <button
            onClick={load}
            disabled={loading}
            className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
          <input
            value={replayRequestId}
            onChange={(event) => setReplayRequestId(event.target.value)}
            placeholder="request_id"
            className="w-44 rounded border border-gray-300 px-2 py-1 text-xs"
          />
          <button
            onClick={() => loadReplay(replayRequestId)}
            disabled={replayLoading || !String(replayRequestId || '').trim()}
            className="rounded border border-violet-300 px-3 py-1.5 text-xs text-violet-700 hover:bg-violet-50 disabled:opacity-40"
          >
            {replayLoading ? 'Replaying...' : 'Replay'}
          </button>
          <a
            href="#/prompt-config?keyword=immune_agent.runtime_rules&scope=immune_agent"
            className="rounded border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-100"
          >
            Edit Rules
          </a>
        </div>
      </div>

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}
      {replayError ? <NoticeBanner variant="error">{replayError}</NoticeBanner> : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass}`}>
            {runtimeStatus.toUpperCase() || 'UNKNOWN'}
          </div>
          <div className="mt-1 text-xs text-gray-400">runtime</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="text-lg font-bold text-gray-700">{stats?.total ?? 0}</div>
          <div className="text-xs text-gray-400">events</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="text-lg font-bold text-green-600">{stats?.allow ?? 0}</div>
          <div className="text-xs text-gray-400">allow</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="text-lg font-bold text-amber-600">{stats?.report ?? 0}</div>
          <div className="text-xs text-gray-400">report</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="text-lg font-bold text-red-500">{stats?.block ?? 0}</div>
          <div className="text-xs text-gray-400">block</div>
        </div>
        <div className="rounded bg-gray-50 p-2 text-center">
          <div className="text-lg font-bold text-red-500">{stats?.block_rate ?? 0}%</div>
          <div className="text-xs text-gray-400">block rate</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded border border-gray-200 bg-gray-50 p-3 text-xs space-y-1.5">
          <div className="font-semibold text-gray-700">Runtime Config</div>
          <div>enabled: {String(runtimeState?.enabled ?? '-')}</div>
          <div>blocking_enabled: {String(runtimeState?.blocking_enabled ?? '-')}</div>
          <div>warn/block threshold: {runtimeState?.warn_threshold ?? '-'} / {runtimeState?.block_threshold ?? '-'}</div>
          <div>rules: {runtimeState?.template_rule_count ?? 0} template / {runtimeState?.total_rule_count ?? 0} total</div>
          <div>counter(total/allow/report/block): {counters.total ?? 0} / {counters.allow ?? 0} / {counters.report ?? 0} / {counters.block ?? 0}</div>
          <div>last decision: {runtimeState?.last_decision || '-'}</div>
          <div>last risk: {runtimeState?.last_risk_level || '-'}</div>
        </div>

        <div className="rounded border border-gray-200 bg-gray-50 p-3">
          <div className="mb-2 text-xs font-semibold text-gray-700">Top Reason Codes</div>
          {reasonCodes.length > 0 ? (
            <div className="space-y-1.5">
              {reasonCodes.slice(0, 6).map((item) => (
                <div key={item.reason_code} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-orange-600">{item.reason_code}</span>
                  <span className="text-gray-500">{item.count} ({item.pct}%)</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="No reason data yet" hint="Wait for immune events to accumulate." className="p-4" />
          )}
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-gray-700">Recent Events</div>
        {recent.length > 0 ? (
          <div className="space-y-2 max-h-52 overflow-y-auto">
            {recent.map((item, index) => (
              <div key={`${item.request_id}-${item.ts_ms}-${index}`} className="rounded border border-gray-100 bg-gray-50 px-2.5 py-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-blue-600">{item.request_id || '-'}</div>
                  <div className="text-gray-500">{item.decision} / {item.risk_level || 'none'}</div>
                </div>
                <div className="mt-1 text-gray-500">
                  {item.event_type} · score={item.score ?? '-'} · {item.reason_codes?.join(', ') || '-'}
                </div>
                <div className="mt-2">
                  <button
                    onClick={() => loadReplay(item.request_id)}
                    className="rounded border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700 hover:bg-violet-100"
                  >
                    Replay {item.request_id}
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyStateCard title="No immune events" hint="No immune gate events in selected window." className="p-4" />
        )}
      </div>

      {replayLoading || replayPayload ? (
        <div className="rounded border border-violet-200 bg-violet-50/30 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs font-semibold text-violet-800">Immune Replay</div>
            {replayPayload?.request_id ? (
              <a
                href={`#/trace/${encodeURIComponent(replayPayload.request_id)}`}
                className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700 hover:bg-blue-100"
              >
                Open Trace
              </a>
            ) : null}
          </div>
          {replayLoading && !replayPayload ? (
            <div className="text-xs text-gray-500">Loading replay timeline...</div>
          ) : null}
          {replayPayload ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded border border-violet-100 bg-white px-2 py-1">
                  request_id: <span className="font-mono">{replayPayload.request_id}</span>
                </div>
                <div className="rounded border border-violet-100 bg-white px-2 py-1">
                  session_id: <span className="font-mono">{replayPayload.session_id || '-'}</span>
                </div>
                <div className="rounded border border-violet-100 bg-white px-2 py-1">
                  first→last: {replayPayload.summary?.first_decision || '-'} → {replayPayload.summary?.last_decision || '-'}
                </div>
                <div className="rounded border border-violet-100 bg-white px-2 py-1">
                  events/max_score: {replayPayload.summary?.event_count ?? 0} / {replayPayload.summary?.max_score ?? '-'}
                </div>
              </div>
              {replayPayload.input_preview ? (
                <div className="rounded border border-violet-100 bg-white px-2 py-1.5 text-xs text-gray-700">
                  input_preview: {replayPayload.input_preview}
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
              <div className="max-h-56 space-y-1.5 overflow-y-auto rounded border border-violet-100 bg-white p-2">
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

function SchedulerHistorySection() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const history = await fetchSchedulerHistory({ window_minutes: 1440 })
      setData(history)
    } catch (fetchError) {
      setError(fetchError.message || '加载执行历史失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-700">执行历史趋势（近 24h）</div>
        <button
          onClick={loadHistory}
          disabled={loading}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      {loading && !data ? (
        <NoticeBanner variant="info" title="加载中">
          正在同步执行历史趋势…
        </NoticeBanner>
      ) : null}

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

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
          <div key={label} className="rounded bg-gray-50 p-2 text-center">
            <div className={`text-lg font-bold ${color}`}>{value ?? '—'}</div>
            <div className="mt-0.5 text-xs text-gray-400">{label}</div>
          </div>
        ))}
      </div>

      {(data?.blocked_reason_codes?.length > 0 || data?.gate_states?.length > 0) ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded border border-gray-200 bg-gray-50 p-3">
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
          <div className="rounded border border-gray-200 bg-gray-50 p-3">
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
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="px-3 py-2 text-left font-medium text-gray-500">任务 ID</th>
                  <th className="px-3 py-2 text-center font-medium text-gray-500">状态</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">执行次数</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">失败次数</th>
                  <th className="px-3 py-2 text-right font-medium text-gray-500">错误率</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">最近原因</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Gate</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Immune</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">Immune Reason</th>
                  <th className="px-3 py-2 text-left font-medium text-gray-500">最近错误</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {(data?.tasks ?? []).map((task) => (
                  <tr key={task.task_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-blue-600">{task.task_id}</td>
                    <td className="px-3 py-2 text-center">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          task.status === 'SUCCESS'
                            ? 'bg-green-100 text-green-700'
                            : task.status === 'FAILED'
                              ? 'bg-red-100 text-red-700'
                              : task.status === 'RUNNING'
                                ? 'bg-blue-100 text-blue-700'
                                : 'bg-gray-100 text-gray-500'
                        }`}
                      >
                        {task.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-700">{task.run_count}</td>
                    <td className="px-3 py-2 text-right text-red-500">{task.error_count}</td>
                    <td className="px-3 py-2 text-right">
                      <span className={task.error_rate > 20 ? 'text-red-500' : 'text-green-600'}>
                        {task.error_rate != null ? `${task.error_rate}%` : '—'}
                      </span>
                    </td>
                    <td className="max-w-[180px] truncate px-3 py-2 font-mono text-orange-600">{task.last_reason_code || '—'}</td>
                    <td className="max-w-[120px] truncate px-3 py-2 font-mono text-blue-600">{task.last_gate_state || '—'}</td>
                    <td className="max-w-[120px] truncate px-3 py-2 font-mono text-violet-600">
                      {task.last_immune_decision
                        ? `${task.last_immune_decision}${task.last_immune_risk_level ? `/${task.last_immune_risk_level}` : ''}`
                        : '—'}
                    </td>
                    <td className="max-w-[220px] truncate px-3 py-2 font-mono text-violet-500">
                      {Array.isArray(task.last_immune_reason_codes) && task.last_immune_reason_codes.length > 0
                        ? task.last_immune_reason_codes.join(', ')
                        : '—'}
                    </td>
                    <td className="max-w-xs truncate px-3 py-2 text-gray-400">{task.last_error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <SchedulerTimelineChart tasks={data.tasks} />
        </>
      ) : !loading ? (
        <EmptyStateCard title="暂无执行历史" hint="等待任务运行后会出现趋势数据。" className="p-6" />
      ) : null}
    </div>
  )
}

function LoadingTasksNotice() {
  return (
    <NoticeBanner variant="info" title="加载中">
      正在同步调度任务列表…
    </NoticeBanner>
  )
}

export default function Automation() {
  const [searchParams, setSearchParams] = useSearchParams()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const [tasks, setTasks] = useState([])
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState('')
  const [actionResult, setActionResult] = useState(null)
  const reasonFilter = String(searchParams.get('reason_code') || '').trim().toUpperCase()
  const gateFilter = String(searchParams.get('gate_state') || '').trim().toLowerCase()
  const immuneReplayRequestId = String(searchParams.get('immune_replay_request_id') || '').trim()
  const hasTaskFilters = Boolean(reasonFilter || gateFilter)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [taskList, schedulerHealth] = await Promise.all([fetchSchedulerTasks(), fetchSchedulerHealth()])
      setTasks(Array.isArray(taskList) ? taskList : [])
      setHealth(schedulerHealth)
    } catch (fetchError) {
      setError(fetchError.message || '加载调度数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, load])

  const handleAction = async (taskId, action) => {
    setActionLoading(taskId)
    setActionResult(null)
    try {
      if (action === 'enable') await enableSchedulerTask(taskId)
      if (action === 'disable') await disableSchedulerTask(taskId)
      if (action === 'trigger') await triggerSchedulerTask(taskId)
      setActionResult({ ok: true, taskId, action })
      await load()
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">器官执行台</h1>
          <p className="mt-0.5 text-sm text-gray-500">统一查看数字生命体常驻节律任务的状态、历史和手动触发入口。</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Workflow}
        eyebrow="节律执行与回放"
        title={schedulerState.title}
        description="这里用于收口数字生命体的后台节律任务，包括启停状态、执行次数、失败告警和近 24 小时历史趋势。"
        gradientClass="from-violet-50 via-white to-sky-50"
        metaItems={[
          `节律任务总数：${tasks.length}`,
          `已启用：${enabledCount}`,
          `推进中：${runningCount}`,
          `异常任务：${failedCount}`,
        ]}
      />

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

      <DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />
      <ImmuneGateOpsPanel
        pollingEnabled={pollingEnabled}
        pollingInterval={pollingInterval}
        initialReplayRequestId={immuneReplayRequestId}
      />

      {hasTaskFilters ? (
        <NoticeBanner variant="info" title="已应用节律筛选">
          <span className="font-mono">
            {reasonFilter ? `reason=${reasonFilter}` : ''}
            {reasonFilter && gateFilter ? ' · ' : ''}
            {gateFilter ? `gate=${gateFilter}` : ''}
          </span>
          <span className="mx-1">，当前命中 {filteredCount} 个任务。</span>
          <button
            onClick={clearTaskFilters}
            className="rounded border border-blue-200 bg-white px-2 py-0.5 text-xs text-blue-600 hover:bg-blue-50"
          >
            清除筛选
          </button>
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
          <div key={label} className="rounded-lg border border-gray-200 bg-white p-3 text-center">
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

      <SchedulerHistorySection />
    </div>
  )
}
