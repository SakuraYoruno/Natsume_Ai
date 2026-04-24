import React from 'react'
import { Activity, Clock3, ShieldCheck, ShieldX } from 'lucide-react'
import KeyValueRow from './KeyValueRow.jsx'
import NoticeBanner from './NoticeBanner.jsx'

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '--'
  const value = Math.max(0, Math.floor(Number(seconds)))
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function formatTickHealth(value) {
  const key = String(value || '').toLowerCase()
  if (key === 'fresh') return '新鲜'
  if (key === 'delayed') return '延迟'
  if (key === 'stalled') return '停滞'
  if (key === 'inactive') return '闲置'
  return '未知'
}

function formatTaskStatus(task) {
  if (!task) return '缺失'
  const key = String(task.status || '').toLowerCase()
  if (!key || key === 'unknown') return '未知'
  if (['ok', 'healthy', 'done', 'success'].includes(key)) return '正常'
  if (['running', 'active', 'processing'].includes(key)) return '运行中'
  if (['blocked', 'canceled', 'cancelled'].includes(key)) return '受阻'
  if (['failed', 'error'].includes(key)) return '异常'
  if (key === 'missing') return '缺失'
  return key.toUpperCase()
}

function formatReasonCode(task) {
  const reason = String(task?.last_reason_code || '').trim()
  return reason || '--'
}

function formatGateState(task) {
  const key = String(task?.last_gate_state || '').toLowerCase()
  if (!key) return '--'
  if (key === 'open') return '开放'
  if (key === 'cautious') return '谨慎'
  if (key === 'protective') return '保护'
  return key
}

export default function BackgroundRuntimeCard({ runtimeSnapshot }) {
  const background = runtimeSnapshot?.background_runtime
  if (!background) return null

  const tasks = background.core_tick_tasks || {}
  const innerNumericTick = tasks.inner_world_numeric_tick || null
  const innerMonologueTick = tasks.inner_world_monologue_tick || null
  const silenceTick = tasks.silence_tick || null
  const bodySenseTick = tasks.body_sense_tick || null

  const isAdvancing = Boolean(background.is_advancing)
  const tickHealth = formatTickHealth(background.tick_health)
  const schedulerStatus = String(background.scheduler_status || 'UNAVAILABLE').toUpperCase()
  const personaAligned = Boolean(background.persona_user_alignment_ok)
  const missingTicks = Array.isArray(background.core_ticks_missing) ? background.core_ticks_missing : []
  const blockedTasks = Array.isArray(background.gate_blocked_tasks) ? background.gate_blocked_tasks : []
  const proactiveTask = background.proactive_task || null

  return (
    <div className="card relative overflow-hidden p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.06),_transparent_42%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_36%)]" />

      <div className="relative flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
            <Activity size={15} className="text-primary" />
            <span>内在推进节律</span>
          </div>
          <div className="mt-2 text-sm text-gray-600">
            {isAdvancing ? '内在世界正在持续推进。' : '推进未达预期，请检查节律与人格对齐。'}
          </div>
        </div>
        <div className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium shadow-sm ${isAdvancing ? 'border-green-200 bg-green-50 text-green-700' : 'border-orange-200 bg-orange-50 text-orange-700'}`}>
          {isAdvancing ? <ShieldCheck size={12} /> : <ShieldX size={12} />}
          {isAdvancing ? '正在推进' : '推进暂停'}
        </div>
      </div>

      <div className="lifeline-rail relative mt-4 h-5 px-2">
        <div className="lifeline-wave absolute inset-0 opacity-70" />
        <div className="relative flex h-full items-center justify-around">
          {[0, 1, 2, 3].map((index) => (
            <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
          ))}
        </div>
      </div>

      {!isAdvancing ? (
        <NoticeBanner className="mt-4" variant="warning" title="推进节律告警">
          节律健康：{tickHealth} · 调度器：{schedulerStatus}
          {blockedTasks.length > 0 ? <span className="ml-1">· 闸门阻塞：{blockedTasks.join(', ')}</span> : null}
        </NoticeBanner>
      ) : null}

      <div className="relative mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-2xl border border-white/75 bg-white/75 p-3 shadow-sm backdrop-blur-sm">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Clock3 size={13} />
            <span>调度总览</span>
          </div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="调度器" value={schedulerStatus} />
            <KeyValueRow label="节律健康" value={tickHealth} />
            <KeyValueRow label="核心 Tick" value={background.core_ticks_registered ? '已注册' : '缺失'} />
            <KeyValueRow label="内在世界 Tick 间隔" value={formatDuration(background.seconds_since_inner_world_tick)} />
            <KeyValueRow label="人格对齐" value={personaAligned ? '一致' : '不一致'} />
            <KeyValueRow label="主动任务" value={formatTaskStatus(proactiveTask)} />
            <KeyValueRow label="主动闸门" value={formatGateState(proactiveTask)} />
          </div>
        </div>

        <div className="rounded-2xl border border-white/75 bg-white/75 p-3 shadow-sm backdrop-blur-sm">
          <div className="text-xs text-gray-500">核心节律</div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="inner_world_numeric_tick" value={formatTaskStatus(innerNumericTick)} />
            <KeyValueRow label="数值推进原因" value={formatReasonCode(innerNumericTick)} />
            <KeyValueRow label="inner_world_monologue_tick" value={formatTaskStatus(innerMonologueTick)} />
            <KeyValueRow label="独白推进原因" value={formatReasonCode(innerMonologueTick)} />
            <KeyValueRow label="silence_tick" value={formatTaskStatus(silenceTick)} />
            <KeyValueRow label="沉默原因" value={formatReasonCode(silenceTick)} />
            <KeyValueRow label="body_sense_tick" value={formatTaskStatus(bodySenseTick)} />
            <KeyValueRow label="体感原因" value={formatReasonCode(bodySenseTick)} />
          </div>
        </div>

        <div className="rounded-2xl border border-white/75 bg-white/75 p-3 shadow-sm backdrop-blur-sm">
          <div className="text-xs text-gray-500">最近运行间隔</div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="inner_world_numeric_tick" value={formatDuration(innerNumericTick?.seconds_since_last_run)} />
            <KeyValueRow label="inner_world_monologue_tick" value={formatDuration(innerMonologueTick?.seconds_since_last_run)} />
            <KeyValueRow label="silence_tick" value={formatDuration(silenceTick?.seconds_since_last_run)} />
            <KeyValueRow label="body_sense_tick" value={formatDuration(bodySenseTick?.seconds_since_last_run)} />
          </div>
          {missingTicks.length > 0 ? <div className="mt-3 text-xs text-orange-600">缺失：{missingTicks.join(', ')}</div> : null}
        </div>
      </div>
    </div>
  )
}
