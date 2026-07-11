import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost, endpointRequest } from '../../shared/api/endpointClient.js'
import { httpStreamPost } from '../../shared/api/httpClient.js'

const { execution, pentest } = ENDPOINT_REGISTRY

const REQUEST_TIMEOUT_MS = 35_000
const SSE_HEADERS = { Accept: 'text/event-stream' }

export { sendChatDebugStream } from '../../shared/api/chatDebug.js'
export {
  fetchAgentLoopHistory,
  fetchAgentLoopStats,
  fetchPermissionStats,
  fetchSchedulerHistory,
  fetchUIAssertionStats,
} from '../../shared/api/runtimeObservability.js'
export { fetchImmuneGateReplay } from '../../shared/api/immuneGate.js'

export function fetchAgentInfo() {
  return endpointGet(execution.agentInfo)
}

export function runAgentLoopStream(payload, options = {}) {
  return httpStreamPost(endpointPath(execution.agentRun), payload, {
    ...options,
    headers: { ...SSE_HEADERS, ...(options.headers || {}) },
  })
}

export function fetchGatewayStats(params = {}) {
  return endpointGet(execution.gatewayStats, params)
}

export function fetchWeChatStats(params = {}) {
  return endpointGet(execution.wechatStats, params)
}

export function resetWeChatChannel(params = {}) {
  return endpointPost(execution.wechatReset, {}, {}, params)
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

export function fetchPentestCapabilities() {
  return endpointGet(pentest.capabilities)
}

export function fetchToolMetaPack(params = {}) {
  return endpointGet(execution.toolMetaPack, params)
}

export function fetchToolMeta(toolId) {
  return endpointGet(execution.toolMeta, {}, { toolId })
}

export function executeTool(body) {
  return endpointPost(execution.toolExecute, body, {}, {}, { timeoutMs: REQUEST_TIMEOUT_MS })
}

export function fetchCustomSkillProviders() {
  return endpointGet(execution.customSkillProviders)
}

export function fetchCustomSkillProviderTemplate() {
  return endpointGet(execution.customSkillProviderTemplate)
}

export function installCustomSkillProvider(body) {
  return endpointPost(execution.customSkillProviderInstall, body, {}, {}, { timeoutMs: REQUEST_TIMEOUT_MS })
}

export function fetchActionPlanStats(params = {}) {
  return endpointGet(execution.actionPlanStats, params)
}

export function fetchSkillsHubStats() {
  return endpointGet(execution.skillsHubStats)
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

export function fetchEdgeCommandStatus() {
  return endpointGet(execution.edgeStatus)
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
