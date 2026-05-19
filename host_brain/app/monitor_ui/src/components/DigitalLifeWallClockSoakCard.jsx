import React from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Gauge,
  RefreshCw,
  Route,
  TimerReset,
} from 'lucide-react'
import NoticeBanner from './NoticeBanner.jsx'
import StatusBadge from './StatusBadge.jsx'
import { compactText, formatDateTime, formatNumber } from '../utils/digitalLife.js'

const VERDICT_LABELS = {
  RUNNING: '验证中',
  PASS: '通过',
  FAIL: '失败',
  MISSING: '未发现',
}

function formatDuration(seconds) {
  const numeric = Number(seconds)
  if (!Number.isFinite(numeric) || numeric < 0) return '--'
  const whole = Math.floor(numeric)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const secs = whole % 60
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${secs}s`
  return `${secs}s`
}

function formatProgress(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${formatNumber(Math.max(0, Math.min(1, numeric)) * 100, 1)}%`
}

function verdictToStatus(verdict, available) {
  if (verdict === 'PASS') return 'healthy'
  if (verdict === 'RUNNING') return 'running'
  if (verdict === 'FAIL') return 'down'
  return available ? 'degraded' : 'unknown'
}

function MetricCell({ icon: Icon, label, value, hint }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/70 px-3 py-2 shadow-sm">
      <div className="flex items-center gap-1.5 text-xs text-gray-500">
        {Icon ? <Icon size={13} className="text-primary" /> : null}
        <span>{label}</span>
      </div>
      <div className="mt-1 text-base font-semibold text-gray-900">{value ?? '--'}</div>
      <div className="mt-0.5 text-xs text-gray-400">{hint ?? '--'}</div>
    </div>
  )
}

function CheckList({ failedChecks }) {
  if (!failedChecks?.length) {
    return (
      <div className="inline-flex items-center gap-1.5 rounded-full border border-green-200 bg-green-50 px-3 py-1 text-xs font-medium text-green-700">
        <CheckCircle2 size={12} />
        所有关键检查通过
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-700">
      <div className="flex items-center gap-1.5 font-medium">
        <AlertTriangle size={13} />
        失败检查 {failedChecks.length}
      </div>
      <div className="mt-1 break-words">{failedChecks.slice(0, 4).join('、')}</div>
    </div>
  )
}

export default function DigitalLifeWallClockSoakCard({
  data,
  loading = false,
  refreshing = false,
  error = null,
  onRefresh,
}) {
  const summary = data?.summary || {}
  const meta = data?.meta || {}
  const verdict = String(summary.verdict || (loading ? 'RUNNING' : 'MISSING'))
  const available = Boolean(data?.available)
  const status = verdictToStatus(verdict, available)
  const progress = Number(summary.progress_ratio)
  const checkpointAge = summary.latest_checkpoint_age_seconds
  const latestCheckpoints = Array.isArray(data?.latestCheckpoints) ? data.latestCheckpoints : []
  const recentCheckpoints = latestCheckpoints.slice(-3).reverse()
  const failedChecks = Array.isArray(summary.failed_checks) ? summary.failed_checks : []
  const expectedOrganCount = Number(meta.expectedOrganCount || 23)
  const registeredOrganCount = Number(summary.registered_organ_count || 0)
  const mappedOrganCount = Number(summary.last_mapped_organ_count || 0)
  const runId = summary.run_id || 'latest'
  const runDir = data?.paths?.summary ? data.paths.summary.replace(/[\\/]+summary\.json$/i, '') : summary.run_dir

  return (
    <div className="rounded-2xl border border-white/80 bg-white/80 p-4 shadow-sm backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={status} label={VERDICT_LABELS[verdict] || verdict} />
            {refreshing ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
                <RefreshCw size={11} className="animate-spin" />
                刷新中
              </span>
            ) : null}
            {meta.readOnly ? (
              <span className="rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-500">
                只读观测
              </span>
            ) : null}
          </div>
          <div className="mt-2 text-base font-semibold text-gray-900">真实 24h 墙钟生命闭环验证</div>
          <div className="mt-1 text-xs leading-5 text-gray-500">
            {available
              ? `run ${runId} · ${compactText(runDir, 120)}`
              : '尚未在 artifacts/codex_runs 中发现真实墙钟 soak 记录。'}
          </div>
        </div>

        {onRefresh ? (
          <button className="btn-secondary text-sm" onClick={onRefresh} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新验证
          </button>
        ) : null}
      </div>

      {error ? (
        <NoticeBanner variant="warning" title="soak 观测接口暂不可用" className="mt-3">
          {error.message || String(error)}
        </NoticeBanner>
      ) : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCell
          icon={Gauge}
          label="进度"
          value={formatProgress(progress)}
          hint={`${formatDuration(summary.elapsed_seconds)} / ${formatDuration(summary.expected_seconds)}`}
        />
        <MetricCell
          icon={Activity}
          label="idle tick"
          value={summary.idle_tick_count ?? '--'}
          hint={`checkpoint ${summary.checkpoint_count ?? '--'}`}
        />
        <MetricCell
          icon={Clock3}
          label="tick 新鲜度"
          value={summary.tick_health || '--'}
          hint={`最近 ${formatDuration(checkpointAge)} 前`}
        />
        <MetricCell
          icon={Route}
          label="23 器官接入"
          value={`${registeredOrganCount}/${expectedOrganCount}`}
          hint={`映射 ${mappedOrganCount}/${expectedOrganCount}`}
        />
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(260px,0.9fr)]">
        <div className="space-y-2">
          <CheckList failedChecks={failedChecks} />
          <div className="grid gap-2 sm:grid-cols-3">
            <MetricCell
              icon={TimerReset}
              label="运行状态"
              value={summary.run_status || '--'}
              hint={summary.soak_status || '--'}
            />
            <MetricCell
              icon={CheckCircle2}
              label="调度器"
              value={summary.scheduler_status || '--'}
              hint={meta.processTracked ? `PID ${summary.process_pid || '--'}` : 'PID 未绑定'}
            />
            <MetricCell
              icon={AlertTriangle}
              label="断链"
              value={failedChecks.length ? '需处理' : '未发现'}
              hint={failedChecks.length ? '见失败检查' : '当前巡检无失败项'}
            />
          </div>
        </div>

        <div className="rounded-xl border border-white/80 bg-white/70 p-3 shadow-sm">
          <div className="mb-2 text-xs font-medium text-gray-500">最近 checkpoint</div>
          {recentCheckpoints.length ? (
            <div className="space-y-2">
              {recentCheckpoints.map((item, index) => (
                <div key={`${item.ts_ms || index}-${item.event_type || 'event'}`} className="flex items-start justify-between gap-3 text-xs">
                  <div className="min-w-0">
                    <div className="font-medium text-gray-700">{item.event_type || '--'}</div>
                    <div className="mt-0.5 text-gray-400">tick {item.tick_index ?? '--'}</div>
                  </div>
                  <div className="shrink-0 text-right text-gray-400">{formatDateTime(item.ts_ms)}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-gray-400">暂无 checkpoint 样本。</div>
          )}
        </div>
      </div>
    </div>
  )
}
