import React from 'react'
import { Link } from 'react-router-dom'
import { Activity, Gauge, Waves } from 'lucide-react'
import EmptyStateCard from './EmptyStateCard.jsx'
import KeyValueRow from './KeyValueRow.jsx'
import MetricCard from './MetricCard.jsx'
import { formatDigitalLifeTag, formatNumber, formatSlotLabel, formatSlotStatus } from '../utils/digitalLife.js'

function asPercent01(value, digits = 0) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${formatNumber(numeric * 100, digits)}%`
}

function toLabel(value, fallback = '--') {
  const raw = String(value || '').trim()
  if (!raw) return fallback
  return formatDigitalLifeTag(raw)
}

const SLOT_KEYS = [
  'interoception_state',
  'motor_cortex_state',
  'cerebellum_state',
  'glymphatic_state',
  'vestibular_state',
]

export default function VitalLoopSummaryPanel({ runtimeSnapshot, compact = false, showDetailLink = true }) {
  if (!runtimeSnapshot) {
    return (
      <EmptyStateCard
        title="器官稳态尚未采样"
        hint="等待 runtime-snapshot 产出后，这里会显示器官稳态、校正与恢复摘要。"
        className="p-5"
      />
    )
  }

  const slotStatus = runtimeSnapshot?.slot_status || {}
  const interoception = runtimeSnapshot?.interoception_state || {}
  const motorCortex = runtimeSnapshot?.motor_cortex_state || {}
  const cerebellum = runtimeSnapshot?.cerebellum_state || {}
  const glymphatic = runtimeSnapshot?.glymphatic_state || {}
  const vestibular = runtimeSnapshot?.vestibular_state || {}
  const homeostasis = runtimeSnapshot?.homeostasis_report || {}

  const interoIndex = Number(interoception.interoceptive_index || 0)
  const vestibularJitter = Number(vestibular.jitter_index || 0)
  const homeostasisStatus = String(homeostasis.status || 'stable').toLowerCase()
  const riskLabel = (
    interoIndex >= 0.88
    || vestibularJitter >= 0.82
    || String(vestibular.stability_state || '').toLowerCase() === 'critical'
    || homeostasisStatus === 'critical'
  )
    ? '高'
    : (
      interoIndex >= 0.72
      || vestibularJitter >= 0.65
      || homeostasisStatus === 'warning'
    )
      ? '中'
      : '低'

  const nonLiveSlots = SLOT_KEYS
    .map((slot) => [slot, slotStatus[slot]])
    .filter(([, status]) => status && status !== 'live')
    .map(([slot, status]) => `${formatSlotLabel(slot)}(${formatSlotStatus(status)})`)

  if (compact) {
    return (
      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Activity size={15} className="text-primary" />
          <h2 className="text-sm font-semibold text-gray-800">器官稳态摘要</h2>
          {showDetailLink ? (
            <Link to="/overview" className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
              查看器官级观测
            </Link>
          ) : null}
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="稳态风险" value={riskLabel} hint={`状态 ${toLabel(homeostasis.status || 'stable')}`} tone="text-cyan-600" />
          <MetricCard label="内感受指数" value={asPercent01(interoception.interoceptive_index, 1)} hint={`恢复紧迫 ${asPercent01(interoception.recovery_urgency, 1)}`} tone="text-rose-600" />
          <MetricCard label="前庭抖动" value={asPercent01(vestibular.jitter_index, 1)} hint={toLabel(vestibular.stability_state)} tone="text-blue-600" />
          <MetricCard
            label="动作分发"
            value={motorCortex.last_dispatch_ok === true ? '成功' : motorCortex.last_dispatch_ok === false ? '失败' : '--'}
            hint={motorCortex.last_dispatch_reason || '暂无最近记录'}
            tone="text-purple-600"
          />
        </div>
      </div>
    )
  }

  return (
    <div className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />

      <div className="relative mb-3 flex items-center gap-2">
        <Activity size={15} className="text-primary" />
        <h2 className="text-sm font-semibold text-gray-800">器官稳态摘要</h2>
        {showDetailLink ? (
          <Link to="/overview" className="ml-auto text-xs font-medium text-primary hover:text-primary-700">
            查看器官级观测
          </Link>
        ) : null}
      </div>

      <div className="lifeline-rail relative mb-4 h-5 px-2">
        <div className="lifeline-wave absolute inset-0 opacity-70" />
        <div className="relative flex h-full items-center justify-around">
          {[0, 1, 2, 3].map((index) => (
            <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
          ))}
        </div>
      </div>

      <div className="relative grid grid-cols-2 gap-3 xl:grid-cols-5">
        <MetricCard label="稳态风险" value={riskLabel} hint={`状态 ${toLabel(homeostasis.status || 'stable')}`} tone="text-cyan-600" />
        <MetricCard label="内感受指数" value={asPercent01(interoception.interoceptive_index, 1)} hint={`body budget ${asPercent01(interoception.body_budget, 1)}`} tone="text-rose-600" />
        <MetricCard label="前庭抖动" value={asPercent01(vestibular.jitter_index, 1)} hint={toLabel(vestibular.stability_state)} tone="text-blue-600" />
        <MetricCard label="小脑校正" value={toLabel(cerebellum.last_strategy || 'NO_ADJUST')} hint={`urgency ${asPercent01(cerebellum.last_urgency, 1)}`} tone="text-violet-600" />
        <MetricCard label="恢复循环" value={glymphatic.executed ? '已执行' : '等待窗口'} hint={glymphatic.reason_code || 'RECOVERY_WINDOW_CLOSED'} tone="text-emerald-600" />
      </div>

      <div className="relative mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="space-y-3">
          <KeyValueRow label="动作计划" value={motorCortex.last_plan_id || '--'} maxValueWidthClassName="max-w-[72%]" />
          <KeyValueRow
            label="动作分发"
            value={motorCortex.last_dispatch_ok === true ? '成功' : motorCortex.last_dispatch_ok === false ? `失败 · ${motorCortex.last_dispatch_reason || 'unknown'}` : '--'}
            maxValueWidthClassName="max-w-[72%]"
          />
          <KeyValueRow label="前庭路由偏置" value={toLabel(vestibular.route_bias || 'normal')} />
          <KeyValueRow
            label="稳态告警"
            value={homeostasis?.alerts?.chronic_overload ? '慢性过载' : homeostasis?.alerts?.chronic_low_energy ? '低能量' : '无'}
          />
        </div>
        <div className="space-y-3">
          {nonLiveSlots.length > 0 ? (
            <KeyValueRow label="非在线槽位" value={nonLiveSlots.join('、')} maxValueWidthClassName="max-w-[80%]" />
          ) : (
            <KeyValueRow label="非在线槽位" value="无" />
          )}
          <KeyValueRow label="30m 平均负荷" value={formatNumber(homeostasis?.minute_window?.avg_cognitive_load_index, 3)} />
          <KeyValueRow label="24h 平均压力" value={formatNumber(homeostasis?.hour_window?.avg_stress_index, 3)} />
          <KeyValueRow label="14d OK 比例" value={asPercent01(homeostasis?.day_window?.homeostasis_ok_ratio, 1)} />
        </div>
      </div>

      <div className="relative mt-4 rounded-xl border border-white/80 bg-white/75 p-3 text-xs text-gray-500 shadow-sm">
        <div className="flex items-center gap-1.5">
          <Gauge size={13} />
          这里展示器官级稳态摘要，详细时序与分层链路请查看器官级观测页。
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <Waves size={13} />
          若显示“等待窗口”，通常是正常节律行为，不代表异常。
        </div>
      </div>
    </div>
  )
}
