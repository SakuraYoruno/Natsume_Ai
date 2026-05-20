import React from 'react'
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  Clock3,
  Gauge,
  HeartPulse,
  Radio,
  RefreshCw,
  Route,
  ShieldQuestion,
  Sparkles,
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

const ACCEPTANCE_VERDICT_LABELS = {
  PASS: '通过',
  RUNNING: '验证中',
  PARTIAL: '部分',
  UNKNOWN: '待证据',
  NOT_COVERED: '未覆盖',
  FAIL: '失败',
}

const LAYER_ICONS = {
  life_base: HeartPulse,
  main_chain: Route,
  inner_world: Sparkles,
  self_model: Brain,
  external_system: ShieldQuestion,
  proactive_loop: Radio,
}

const LAYER_CLASS = {
  healthy: 'border-emerald-200 bg-emerald-50/75',
  running: 'border-blue-200 bg-blue-50/75',
  degraded: 'border-orange-200 bg-orange-50/75',
  down: 'border-red-200 bg-red-50/75',
  unknown: 'border-gray-200 bg-gray-50/80',
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

function verdictLabel(value) {
  const key = String(value || '').toUpperCase()
  return ACCEPTANCE_VERDICT_LABELS[key] || key || '--'
}

function LayerAcceptanceCard({ item }) {
  const Icon = LAYER_ICONS[item?.id] || CheckCircle2
  const status = String(item?.status || 'unknown').toLowerCase()
  const className = LAYER_CLASS[status] || LAYER_CLASS.unknown
  const evidence = Array.isArray(item?.evidence) ? item.evidence.filter(Boolean) : []

  return (
    <div className={`rounded-xl border p-3 shadow-sm ${className}`}>
      <div className="flex items-start gap-2">
        <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white/80 text-primary shadow-sm">
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold text-gray-800">{item?.label || '--'}</div>
            <StatusBadge status={status} label={verdictLabel(item?.verdict)} />
          </div>
          <div className="mt-1 text-lg font-semibold text-gray-900">{item?.value || '--'}</div>
          <div className="mt-1 text-xs leading-5 text-gray-600">{item?.detail || '--'}</div>
          {evidence.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {evidence.slice(0, 4).map((text) => (
                <span key={text} className="rounded-full border border-white/80 bg-white/75 px-2 py-0.5 text-[11px] text-gray-500">
                  {text}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
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
  const acceptance = data?.acceptance || {}
  const acceptanceLayers = Array.isArray(acceptance.layers) ? acceptance.layers : []
  const notCovered = Array.isArray(acceptance.not_covered) ? acceptance.not_covered : []
  const verdict = String(summary.verdict || (loading ? 'RUNNING' : 'MISSING'))
  const available = Boolean(data?.available)
  const status = verdictToStatus(verdict, available)
  const acceptanceStatus = acceptance.overall_status || status
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
            {acceptance.title ? (
              <StatusBadge status={acceptanceStatus} label={acceptance.title} />
            ) : null}
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
          <div className="mt-2 text-base font-semibold text-gray-900">
            {acceptance.title || '真实 24h 墙钟生命闭环验证'}
          </div>
          {acceptance.headline ? (
            <div className="mt-1 max-w-5xl text-sm leading-6 text-gray-600">{acceptance.headline}</div>
          ) : null}
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

      {acceptanceLayers.length ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {acceptanceLayers.map((item) => (
            <LayerAcceptanceCard key={item.id || item.label} item={item} />
          ))}
        </div>
      ) : null}

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
          {notCovered.length ? (
            <div className="rounded-xl border border-gray-200 bg-gray-50/80 px-3 py-2 text-xs text-gray-600">
              <div className="flex items-center gap-1.5 font-medium text-gray-700">
                <ShieldQuestion size={13} />
                未覆盖项不是失败项
              </div>
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {notCovered.slice(0, 4).map((item) => (
                  <div key={item.id || item.label} className="rounded-lg border border-white/80 bg-white/70 px-2 py-1.5">
                    <div className="font-medium text-gray-700">{item.label}</div>
                    <div className="mt-0.5 leading-4 text-gray-500">{item.reason}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
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
