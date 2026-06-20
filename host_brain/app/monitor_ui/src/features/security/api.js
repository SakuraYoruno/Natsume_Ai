import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'
import { httpText } from '../../shared/api/httpClient.js'

const { pentest, securityImmune } = ENDPOINT_REGISTRY

export const PENTEST_RUN_EVENT_TYPES = Object.freeze([
  'run_created',
  'approval_required',
  'tool_start',
  'tool_finish',
  'tool_missing',
  'tool_executed',
  'max_steps_reached',
  'manifest_validated',
  'mcts_iteration',
  'action_rejected_by_policy',
  'action_selected',
  'graph_updated',
  'finding_confirmed',
  'session_opened',
  'chain_advanced',
  'report_ready',
  'side_effect_approved',
  'side_effect_continue_start',
  'side_effect_continued',
  'run_canceled',
  'run_failed',
])

export function fetchPentestCapabilities() {
  return endpointGet(pentest.capabilities)
}

export function fetchPentestEnvironment() {
  return endpointGet(pentest.environment)
}

export function createPentestRun(payload) {
  return endpointPost(pentest.runs, payload)
}

export function fetchPentestRuns(limit = 20) {
  return endpointGet(pentest.runs, { limit })
}

export function fetchPentestRun(runId) {
  return endpointGet(pentest.run, {}, { runId })
}

export function pentestRunEventsPath(runId) {
  return endpointPath(pentest.events, { runId })
}

export function subscribePentestRunEvents(runId, { onOpen, onEvent, onError } = {}) {
  if (!runId) return () => {}
  if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
    onError?.(new Error('EventSource not available'))
    return () => {}
  }

  const source = new window.EventSource(pentestRunEventsPath(runId))
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
  }
  const parseEvent = (messageEvent) => {
    const raw = messageEvent?.data
    if (!raw) return null
    try {
      return JSON.parse(raw)
    } catch (_) {
      return { type: messageEvent?.type || 'message', message: String(raw), raw }
    }
  }
  const handleEvent = (messageEvent) => {
    const event = parseEvent(messageEvent)
    if (event) onEvent?.(event)
  }

  source.onopen = () => onOpen?.()
  source.onmessage = handleEvent
  PENTEST_RUN_EVENT_TYPES.forEach((eventType) => {
    source.addEventListener(eventType, handleEvent)
  })
  source.onerror = () => {
    onError?.(new Error('Pentest live event stream unavailable'))
    close()
  }

  return close
}

export function fetchPentestReport(runId, format = 'markdown') {
  return format === 'json'
    ? endpointGet(pentest.report, { format }, { runId })
    : httpText(endpointPath(pentest.report, { runId }, { format }))
}

export function cancelPentestRun(runId) {
  return endpointPost(pentest.cancel, {}, { runId })
}

export function approvePentestSideEffect(runId) {
  return endpointPost(pentest.approveSideEffect, {}, { runId })
}

export function continuePentestSideEffect(runId, payload = {}) {
  return endpointPost(pentest.continueSideEffect, payload, { runId })
}

export function fetchSecurityRuntime() {
  return endpointGet(securityImmune.runtime)
}

export function fetchSecurityAssets() {
  return endpointGet(securityImmune.assets)
}

export function fetchSecurityIncidents(minRisk = 'low') {
  return endpointGet(securityImmune.incidents, { min_risk: minRisk })
}

export function runSecurityHuntOnce(reason = 'manual_web_trigger') {
  return endpointPost(securityImmune.huntRunOnce, { reason }, {}, {}, { timeoutMs: 15000 })
}
