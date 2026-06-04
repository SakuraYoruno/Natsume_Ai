import React from 'react'
import { Activity, Bot, CheckCircle2, RefreshCw, ShieldCheck, ShieldX, Waves } from 'lucide-react'

function valueOrDash(value) {
  if (value === true) return 'true'
  if (value === false) return 'false'
  const text = String(value ?? '').trim()
  return text || '--'
}

function boolLabel(value, trueLabel = '开启', falseLabel = '关闭') {
  if (value === true) return trueLabel
  if (value === false) return falseLabel
  return '--'
}

function StatusChip({ label, value, tone = 'slate' }) {
  const tones = {
    green: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    rose: 'border-rose-200 bg-rose-50 text-rose-700',
    slate: 'border-slate-200 bg-slate-50 text-slate-700',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${tones[tone] || tones.slate}`}>
      <span className="text-slate-400">{label}</span>
      <span>{valueOrDash(value)}</span>
    </span>
  )
}

function SafetyRow({ label, blocked, safeWhenBlocked = true }) {
  const safe = safeWhenBlocked ? blocked === true : blocked === false
  const Icon = safe ? ShieldCheck : ShieldX
  return (
    <div className={`rounded-xl border px-3 py-2 ${safe ? 'border-emerald-100 bg-emerald-50/70' : 'border-rose-100 bg-rose-50/70'}`}>
      <div className="flex items-center gap-2 text-xs font-semibold text-gray-700">
        <Icon size={14} className={safe ? 'text-emerald-600' : 'text-rose-600'} />
        {label}
      </div>
      <div className={`mt-1 text-sm font-semibold ${safe ? 'text-emerald-700' : 'text-rose-700'}`}>
        {blocked === true ? '已拦截' : blocked === false ? '未拦截' : '--'}
      </div>
    </div>
  )
}

export default function AvatarStateCard({
  data,
  loading = false,
  refreshing = false,
  error = null,
  commandError = null,
  behaviorBusy = false,
  updatedAt = null,
  onRefresh = null,
  onBehavior = null,
}) {
  const event = data?.event || {}
  const driver = event.driver || {}
  const safety = event.safety || {}
  const pipeline = event.voice_pipeline || {}
  const behavior = event.behavior || {}
  const current = behavior.current || {}
  const blockedHotkeys = safety.blocked_hotkeys || {}
  const ok = Boolean(data?.success && driver.available)
  const statusTone = ok ? 'green' : data ? 'amber' : 'slate'
  const behaviorControls = [
    { id: 'idle', label: '静止' },
    { id: 'listening', label: '聆听' },
    { id: 'thinking', label: '思考' },
    { id: 'interrupted', label: '打断' },
  ]

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/75 bg-[radial-gradient(circle_at_top_left,_rgba(14,165,233,0.10),_transparent_35%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.12),_transparent_38%)] p-5 shadow-card backdrop-blur-sm">
      <div className="pointer-events-none absolute inset-x-10 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200 to-transparent" />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-2 rounded-full border border-cyan-100 bg-white/85 px-3 py-1 text-xs font-semibold text-cyan-700 shadow-sm">
            <Bot size={13} />
            桌宠表现链路
          </div>
          <div className="mt-3 text-lg font-semibold text-gray-900">
            {ok ? 'Avatar 驱动在线，表现状态可观测。' : loading ? '正在读取桌宠状态…' : '桌宠状态暂未完全就绪。'}
          </div>
          <div className="mt-2 text-sm leading-6 text-gray-600">
            这里只读展示 Edge/MateEngine/VRM 的状态，不会触发表情、动作或 VTS 热键。
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <StatusChip label="状态" value={data?.status || event.status} tone={statusTone} />
          {onRefresh ? (
            <button
              className="btn-secondary text-xs"
              onClick={onRefresh}
              disabled={loading || refreshing}
            >
              <RefreshCw size={13} className={loading || refreshing ? 'animate-spin' : ''} />
              刷新
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {error.message || String(error)}
        </div>
      ) : null}
      {commandError ? (
        <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {commandError.message || String(commandError)}
        </div>
      ) : null}

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatusChip label="backend" value={event.avatar_backend} tone="blue" />
        <StatusChip label="driver" value={driver.driver_type} tone="blue" />
        <StatusChip label="state" value={driver.state} tone={driver.state === 'AUTHENTICATED' ? 'green' : 'amber'} />
        <StatusChip label="talking" value={boolLabel(driver.talking, '说话中', '静默')} tone={driver.talking ? 'green' : 'slate'} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(280px,0.9fr)]">
        <div className="rounded-2xl border border-white/75 bg-white/78 p-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
            <Activity size={15} className="text-cyan-600" />
            当前行为
          </div>
          <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-2">
            <StatusChip label="behavior" value={current.behavior_id || current.state || 'idle'} tone="blue" />
            <StatusChip label="body action" value={pipeline.body_action_active ? 'active' : 'idle'} tone={pipeline.body_action_active ? 'green' : 'slate'} />
            <StatusChip label="mouth sync" value={pipeline.mouth_sync?.state} tone={pipeline.mouth_sync?.state === 'running' ? 'green' : 'slate'} />
            <StatusChip label="last reason" value={current.reason || pipeline.last_body_action?.reason || '--'} tone="slate" />
          </div>
          <div className="mt-3 rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2 text-xs leading-5 text-slate-600">
            表现层目录：{Array.isArray(behavior.catalog) ? behavior.catalog.map((item) => item.behavior_id).join(' / ') : '--'}
          </div>
          {onBehavior ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {behaviorControls.map((item) => {
                const active = String(current.behavior_id || current.state || '').toLowerCase() === item.id
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                      active
                        ? 'border-cyan-300 bg-cyan-50 text-cyan-700'
                        : 'border-slate-200 bg-white/85 text-slate-600 hover:border-cyan-200 hover:bg-cyan-50/60'
                    }`}
                    onClick={() => onBehavior(item.id)}
                    disabled={behaviorBusy || loading || refreshing}
                  >
                    {behaviorBusy ? '处理中…' : item.label}
                  </button>
                )
              })}
            </div>
          ) : null}
        </div>

        <div className="rounded-2xl border border-white/75 bg-white/78 p-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-800">
            <Waves size={15} className="text-emerald-600" />
            安全开关
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:grid-cols-1">
            <SafetyRow label="水印热键" blocked={blockedHotkeys.hotkey_remove_watermark} />
            <SafetyRow label="Reset All 热键" blocked={blockedHotkeys.hotkey_reset_all} />
            <SafetyRow label="普通表情热键" blocked={blockedHotkeys.hotkey_exp_blush} safeWhenBlocked={false} />
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <StatusChip label="body motion" value={boolLabel(safety.body_motion_enabled)} tone={safety.body_motion_enabled ? 'amber' : 'green'} />
            <StatusChip label="neutral expr" value={boolLabel(safety.neutral_expression_enabled)} tone={safety.neutral_expression_enabled ? 'amber' : 'green'} />
            <StatusChip label="reset neutral" value={boolLabel(safety.reset_to_neutral_enabled)} tone={safety.reset_to_neutral_enabled ? 'amber' : 'green'} />
            <StatusChip label="latency" value={data?.latency_ms == null ? '--' : `${data.latency_ms}ms`} tone="slate" />
          </div>
        </div>
      </div>

      {updatedAt ? (
        <div className="mt-3 flex items-center gap-1.5 text-xs text-gray-400">
          <CheckCircle2 size={12} />
          最近更新：{new Date(updatedAt).toLocaleTimeString('zh-CN')}
        </div>
      ) : null}
    </div>
  )
}
