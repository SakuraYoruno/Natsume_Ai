import { ENDPOINT_REGISTRY, endpointPath } from './endpointRegistry.js'
import { obsGet } from './obsClient.js'

const { observability } = ENDPOINT_REGISTRY

function getObs(entry, params = {}) {
  return obsGet(endpointPath(entry, {}, params))
}

export function fetchAgentLoopStats(params = {}) {
  return getObs(observability.agentLoopStats, params)
}

export function fetchAgentLoopHistory(params = {}) {
  return getObs(observability.agentLoopHistory, params)
}

export function fetchPermissionStats(params = {}) {
  return getObs(observability.permissionStats, params)
}

export function fetchSchedulerHistory(params = {}) {
  return getObs(observability.schedulerHistory, params)
}
