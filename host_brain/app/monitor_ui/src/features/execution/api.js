import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost, endpointRequest } from '../../shared/api/endpointClient.js'

const { execution, pentest } = ENDPOINT_REGISTRY

const REQUEST_TIMEOUT_MS = 35_000
export {
  fetchAgentLoopHistory,
  fetchAgentLoopStats,
  fetchPermissionStats,
  fetchSchedulerHistory,
} from '../../shared/api/runtimeObservability.js'
export { fetchImmuneGateReplay } from '../../shared/api/immuneGate.js'

export function fetchAgentInfo() {
  return endpointGet(execution.agentInfo)
}

export function fetchGatewayStats(params = {}) {
  return endpointGet(execution.gatewayStats, params)
}

export function fetchWeChatStats(params = {}) {
  return endpointGet(execution.wechatStats, params)
}

export function fetchSchedulerTasks() {
  return endpointGet(execution.schedulerTasks)
}

export function fetchSchedulerHealth() {
  return endpointGet(execution.schedulerHealth)
}

export function fetchResourceManagerStatus() {
  return endpointGet(execution.resourceManagerStatus)
}

export function fetchImmuneGateStats(params = {}) {
  return endpointGet(execution.immuneGateStats, params)
}

export function enableSchedulerTask(taskId) {
  return postWithoutBody(execution.schedulerTaskEnable, { taskId })
}

export function disableSchedulerTask(taskId) {
  return postWithoutBody(execution.schedulerTaskDisable, { taskId })
}

export function triggerSchedulerTask(taskId) {
  return postWithoutBody(execution.schedulerTaskTrigger, { taskId })
}

export function fetchToolHealth() {
  return endpointGet(execution.toolHealth)
}

export function fetchToolsPentestCapabilities() {
  return endpointGet(pentest.capabilities)
}

export function fetchToolMetaPack(params = {}) {
  return endpointGet(execution.toolMetaPack, params)
}

export function fetchToolMeta(toolId) {
  return endpointGet(execution.toolMeta, {}, { toolId })
}

export function getToolExecutionAvailability() {
  const entry = execution.toolExecute
  return Object.freeze({
    available: entry?.retired !== true,
    commandId: String(entry?.retiredCommandId || entry?.commandId || ''),
    replacementAuthority: String(entry?.replacementAuthority || ''),
    replacementWave: String(entry?.replacementWave || ''),
  })
}

export function fetchActionPlanStats(params = {}) {
  return endpointGet(execution.actionPlanStats, params)
}

export function fetchSkillCatalogStats() {
  return endpointGet(execution.skillCatalogStats)
}

export function fetchTribeBundle() {
  return Promise.allSettled([
    endpointGet(execution.tribeSummary),
    endpointGet(execution.tribeEvents),
    endpointGet(execution.tribeSubscribers),
  ]).then((results) => ({
    summary: unwrap(results[0]),
    events: unwrap(results[1]),
    subscribers: unwrap(results[2]),
    errors: results
      .map((result, index) => ({ result, index }))
      .filter(({ result }) => result.status === 'rejected')
      .map(({ result, index }) => ({ index, message: result.reason?.message || String(result.reason) })),
  }))
}

export function pushEdgeCommand(payload) {
  return endpointPost(execution.edgePush, payload)
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}

function postWithoutBody(entry, pathParams = {}) {
  return endpointRequest(entry, {
    method: 'POST',
    pathParams,
    options: { timeoutMs: REQUEST_TIMEOUT_MS },
  })
}
