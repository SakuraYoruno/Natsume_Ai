import React from 'react'
import { Activity, Clock3, ShieldCheck, ShieldX } from 'lucide-react'
import KeyValueRow from './KeyValueRow.jsx'
import NoticeBanner from './NoticeBanner.jsx'

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const value = Math.max(0, Math.floor(Number(seconds)))
  if (value < 60) return `${value}s`
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function formatTickHealth(value) {
  const key = String(value || '').toLowerCase()
  if (key === 'fresh') return 'Fresh'
  if (key === 'delayed') return 'Delayed'
  if (key === 'stalled') return 'Stalled'
  if (key === 'inactive') return 'Inactive'
  return 'Unknown'
}

function formatTaskStatus(task) {
  if (!task) return 'MISSING'
  return String(task.status || 'UNKNOWN').toUpperCase()
}

function formatReasonCode(task) {
  const reason = String(task?.last_reason_code || '').trim()
  return reason || '—'
}

function formatGateState(task) {
  const key = String(task?.last_gate_state || '').toLowerCase()
  if (!key) return '—'
  if (key === 'open') return 'open'
  if (key === 'cautious') return 'cautious'
  if (key === 'protective') return 'protective'
  return key
}

export default function BackgroundRuntimeCard({ runtimeSnapshot }) {
  const background = runtimeSnapshot?.background_runtime
  if (!background) return null

  const tasks = background.core_tick_tasks || {}
  const innerTick = tasks.inner_world_tick || null
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
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
            <Activity size={15} className="text-gray-500" />
            <span>后台推进状态</span>
          </div>
          <div className="mt-2 text-sm text-gray-600">
            {isAdvancing ? '内在世界正在后台持续推进。' : '后台推进未达预期，请检查调度与用户对齐。'}
          </div>
        </div>
        <div className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${isAdvancing ? 'border-green-200 bg-green-50 text-green-700' : 'border-orange-200 bg-orange-50 text-orange-700'}`}>
          {isAdvancing ? <ShieldCheck size={12} /> : <ShieldX size={12} />}
          {isAdvancing ? 'Advancing' : 'Not Advancing'}
        </div>
      </div>

      {!isAdvancing ? (
        <NoticeBanner className="mt-4" variant="warning" title="后台推进告警">
          Tick Health: {tickHealth} · Scheduler: {schedulerStatus}
          {blockedTasks.length > 0 ? (
            <span className="ml-1">· Gate Blocked: {blockedTasks.join(', ')}</span>
          ) : null}
        </NoticeBanner>
      ) : null}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Clock3 size={13} />
            <span>调度总览</span>
          </div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="Scheduler" value={schedulerStatus} />
            <KeyValueRow label="Tick Health" value={tickHealth} />
            <KeyValueRow label="Core Ticks" value={background.core_ticks_registered ? 'Registered' : 'Missing'} />
            <KeyValueRow label="Inner Tick Ago" value={formatDuration(background.seconds_since_inner_world_tick)} />
            <KeyValueRow label="Persona Align" value={personaAligned ? 'OK' : 'MISMATCH'} />
            <KeyValueRow label="Proactive Task" value={formatTaskStatus(proactiveTask)} />
            <KeyValueRow label="Proactive Gate" value={formatGateState(proactiveTask)} />
          </div>
        </div>

        <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs text-gray-500">核心 Tick</div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="inner_world_tick" value={formatTaskStatus(innerTick)} />
            <KeyValueRow label="inner reason" value={formatReasonCode(innerTick)} />
            <KeyValueRow label="silence_tick" value={formatTaskStatus(silenceTick)} />
            <KeyValueRow label="silence reason" value={formatReasonCode(silenceTick)} />
            <KeyValueRow label="body_sense_tick" value={formatTaskStatus(bodySenseTick)} />
            <KeyValueRow label="body reason" value={formatReasonCode(bodySenseTick)} />
          </div>
        </div>

        <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs text-gray-500">最近运行间隔</div>
          <div className="mt-3 space-y-2">
            <KeyValueRow label="inner_world_tick" value={formatDuration(innerTick?.seconds_since_last_run)} />
            <KeyValueRow label="silence_tick" value={formatDuration(silenceTick?.seconds_since_last_run)} />
            <KeyValueRow label="body_sense_tick" value={formatDuration(bodySenseTick?.seconds_since_last_run)} />
          </div>
          {missingTicks.length > 0 ? (
            <div className="mt-3 text-xs text-orange-600">
              Missing: {missingTicks.join(', ')}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
