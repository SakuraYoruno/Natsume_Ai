import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { httpGet, httpPost, httpRequest, httpStreamPost } from '../../shared/api/httpClient.js'
import { obsGet, obsPost } from '../../shared/api/obsClient.js'

const { execution } = ENDPOINT_REGISTRY

const REQUEST_TIMEOUT_MS = 35_000
const SSE_HEADERS = { Accept: 'text/event-stream' }

export { sendChatDebugStream } from '../../shared/api/chatDebug.js'

export function fetchAgentInfo() {
  return httpGet(execution.agentInfo.path)
}

export function fetchAgentLoopStats(params = {}) {
  return getObs(execution.agentLoopStats, params)
}

export function fetchAgentLoopHistory(params = {}) {
  return getObs(execution.agentLoopHistory, params)
}

export function fetchUIAssertionStats(params = {}) {
  return getObs(execution.uiAssertionStats, params)
}

export function runAgentLoopStream(payload, options = {}) {
  return httpStreamPost(execution.agentRun.path, payload, {
    ...options,
    headers: { ...SSE_HEADERS, ...(options.headers || {}) },
  })
}

export function fetchGatewayStats(params = {}) {
  return getObs(execution.gatewayStats, params)
}

export function fetchWeChatStats(params = {}) {
  return getObs(execution.wechatStats, params)
}

export function resetWeChatChannel(params = {}) {
  return obsPost(endpointPath(execution.wechatReset, {}, params), {})
}

export function fetchSchedulerTasks() {
  return getObs(execution.schedulerTasks)
}

export function fetchSchedulerHealth() {
  return getObs(execution.schedulerHealth)
}

export function fetchResourceManagerStatus() {
  return getObs(execution.resourceManagerStatus)
}

export function fetchImmuneGateStats(params = {}) {
  return getObs(execution.immuneGateStats, params)
}

export function fetchImmuneGateReplay(requestId, params = {}) {
  const normalizedRequestId = String(requestId || '').trim()
  if (!normalizedRequestId) {
    throw new Error('request_id is required')
  }
  return getObs(execution.immuneGateReplay, params, { requestId: normalizedRequestId })
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

export function fetchSchedulerHistory(params = {}) {
  return getObs(execution.schedulerHistory, params)
}

export function fetchToolHealth() {
  return getObs(execution.toolHealth)
}

export function fetchToolMetaPack(params = {}) {
  return getObs(execution.toolMetaPack, params)
}

export function fetchToolMeta(toolId) {
  return getObs(execution.toolMeta, {}, { toolId })
}

export function executeTool(body) {
  return httpPost(endpointPath(execution.toolExecute), body, { timeoutMs: REQUEST_TIMEOUT_MS })
}

export function fetchCustomSkillProviders() {
  return getObs(execution.customSkillProviders)
}

export function fetchCustomSkillProviderTemplate() {
  return getObs(execution.customSkillProviderTemplate)
}

export function installCustomSkillProvider(body) {
  return httpPost(endpointPath(execution.customSkillProviderInstall), body, { timeoutMs: REQUEST_TIMEOUT_MS })
}

export function fetchPermissionStats(params = {}) {
  return getObs(execution.permissionStats, params)
}

export function fetchActionPlanStats(params = {}) {
  return getObs(execution.actionPlanStats, params)
}

export function fetchSkillsHubStats() {
  return getObs(execution.skillsHubStats)
}

export function fetchTribeBundle() {
  return Promise.allSettled([
    httpGet(execution.tribeSummary.path),
    httpGet(execution.tribeEvents.path),
    httpGet(execution.tribeSubscribers.path),
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
  return httpGet(execution.edgeStatus.path)
}

export function pushEdgeCommand(payload) {
  return httpPost(execution.edgePush.path, payload)
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}

function getObs(entry, params = {}, pathParams = {}) {
  return obsGet(endpointPath(entry, pathParams, params))
}

function postWithoutBody(entry, pathParams = {}) {
  return httpRequest(endpointPath(entry, pathParams), {
    method: 'POST',
    timeoutMs: REQUEST_TIMEOUT_MS,
  })
}
