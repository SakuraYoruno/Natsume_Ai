import React from 'react'
import EmptyStateCard from '../EmptyStateCard.jsx'
import LoadingNotice from '../LoadingNotice.jsx'
import NoticeBanner from '../NoticeBanner.jsx'
import SampleMeta from '../obs/SampleMeta.jsx'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { useSchedulerHistoryQuery } from '../../queries/automationQueries.js'

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

export default function AutomationHistorySectionContent({
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
        <LoadingNotice>
          正在同步节律历史脉络…
        </LoadingNotice>
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
