import { AUTOMATION_TASK_STATUS_STYLES } from '../../shared/statusMeta.js'
import { getTaskResourceProfile } from './automationResourceModel.js'

function formatEndocrineValue(value) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(2) : '—'
}

function getTaskFilterFields(item) {
  const task = item?.task || {}
  const health = item?.health || {}
  const resourceProfile = getTaskResourceProfile(item)
  return [
    task.task_id,
    task.description,
    task.priority,
    task.trigger_mode,
    health.status,
    health.last_reason_code,
    health.last_gate_state,
    health.last_immune_decision,
    resourceProfile.resource_class,
    resourceProfile.meta.label,
    resourceProfile.uses_llm ? 'llm' : '',
    resourceProfile.uses_gpu ? 'gpu' : '',
    ...(Array.isArray(health.last_immune_reason_codes) ? health.last_immune_reason_codes : []),
  ].map((value) => String(value || '').toLowerCase())
}

export function getTaskView(item) {
  const { task = {}, health = {} } = item || {}
  const status = health?.status || 'PENDING'
  const style = AUTOMATION_TASK_STATUS_STYLES[status] || AUTOMATION_TASK_STATUS_STYLES.PENDING
  const resourceProfile = getTaskResourceProfile(item)
  const lastReasonCode = String(health?.last_reason_code || '').trim()
  const lastGateState = String(health?.last_gate_state || '').trim()
  const lastMoodState = String(health?.last_mood_state || '').trim()
  const lastImmuneDecision = String(health?.last_immune_decision || '').trim().toUpperCase()
  const lastImmuneRisk = String(health?.last_immune_risk_level || '').trim().toLowerCase()
  const lastImmuneReasonCodes = Array.isArray(health?.last_immune_reason_codes)
    ? health.last_immune_reason_codes.filter((code) => String(code || '').trim()).slice(0, 3)
    : []

  return {
    task,
    health,
    status,
    style,
    resourceProfile,
    lastReasonCode,
    lastGateState,
    lastMoodState,
    lastImmuneDecision,
    lastImmuneRisk,
    lastImmuneReasonCodes,
    lastCortisol: formatEndocrineValue(health?.last_endocrine_cortisol),
    lastEnergy: formatEndocrineValue(health?.last_endocrine_energy),
  }
}

export function taskMatchesAutomationFilters(item, { reasonFilter = '', gateFilter = '', keyword = '' } = {}) {
  const taskReason = String(item?.health?.last_reason_code || '').trim().toUpperCase()
  const taskGate = String(item?.health?.last_gate_state || '').trim().toLowerCase()
  if (reasonFilter && taskReason !== reasonFilter) return false
  if (gateFilter && taskGate !== gateFilter) return false

  const targetKeyword = String(keyword || '').trim().toLowerCase()
  if (!targetKeyword) return true

  return getTaskFilterFields(item).some((value) => value.includes(targetKeyword))
}

export function getAutomationTaskUrlFilters(searchParams) {
  return {
    reasonFilter: String(searchParams?.get?.('reason_code') || '').trim().toUpperCase(),
    gateFilter: String(searchParams?.get?.('gate_state') || '').trim().toLowerCase(),
    immuneReplayRequestId: String(searchParams?.get?.('immune_replay_request_id') || '').trim(),
  }
}

export function buildAutomationClearedTaskFilterParams(searchParams) {
  const next = new URLSearchParams(searchParams)
  next.delete('reason_code')
  next.delete('gate_state')
  return next
}

export function buildAutomationTaskSelectionView({
  tasks = [],
  reasonFilter = '',
  gateFilter = '',
  taskSearch = '',
  selectedTaskId = '',
} = {}) {
  const keyword = String(taskSearch || '').trim().toLowerCase()
  const filteredTasks = (Array.isArray(tasks) ? tasks : []).filter((item) => (
    taskMatchesAutomationFilters(item, { reasonFilter, gateFilter, keyword })
  ))
  const selectedTask = filteredTasks.find((item) => item.task?.task_id === selectedTaskId) || filteredTasks[0] || null

  return {
    filteredTasks,
    selectedTask,
    hasTaskFilters: Boolean(reasonFilter || gateFilter),
    hasAnyTaskFilter: Boolean(reasonFilter || gateFilter || keyword),
    selectedTaskId: selectedTask?.task?.task_id || '',
  }
}
