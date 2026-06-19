import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { httpGet } from '../../shared/api/httpClient.js'
import { obsGet, obsPost, obsPut } from '../../shared/api/obsClient.js'

const { digitalLife } = ENDPOINT_REGISTRY
const LIFE_RUNTIME_TIMEOUT_MS = 6_000

export function fetchDigitalLifeOverviewSnapshot(params = {}) {
  return getObs(digitalLife.overviewSnapshot, params)
}

export function fetchDigitalLifeWallClockSoak(params = {}) {
  return getObs(digitalLife.wallClockSoak, params)
}

export function fetchLifeBody(params = {}) {
  return getObs(digitalLife.lifeBody, params)
}

export function fetchLifeTimeline(params = {}) {
  return getObs(digitalLife.lifeTimeline, params)
}

export function fetchPathologies(params = {}) {
  return getObs(digitalLife.pathologies, params)
}

export function fetchAvatarState(params = {}) {
  return getObs(digitalLife.avatarState, params)
}

export function postAvatarBehavior(body = {}) {
  return obsPost(endpointPath(digitalLife.avatarBehavior), body || {})
}

export function fetchDigitalLifeRuntimeSnapshot(params = {}) {
  return getObs(digitalLife.runtimeSnapshot, params)
}

export function fetchMemoryStats(params = {}) {
  return getObs(digitalLife.memoryStats, params)
}

export function fetchCanonDocuments() {
  return getObs(digitalLife.canonDocuments)
}

export function fetchResidentMemoryContext() {
  return getObs(digitalLife.residentMemoryContext)
}

export function fetchDistillationStats(params = {}) {
  return getObs(digitalLife.distillationStats, params)
}

export function fetchContextCompressorStats(params = {}) {
  return getObs(digitalLife.contextCompressorStats, params)
}

export function fetchEpisodicStoreStats(params = {}) {
  return getObs(digitalLife.episodicStoreStats, params)
}

export function fetchBondHealth() {
  return getObs(digitalLife.bondHealth)
}

export function fetchBondUsers() {
  return getObs(digitalLife.bondUsers)
}

export function fetchBondStage(userId) {
  return getObs(digitalLife.bondStage, {}, { userId })
}

export function fetchBondSummary(userId) {
  return getObs(digitalLife.bondSummary, {}, { userId })
}

export function submitBondEvent(body) {
  return obsPost(endpointPath(digitalLife.bondEvent), body)
}

export function fetchSysModeStatus() {
  return obsGet(digitalLife.sysMode.path)
}

export function switchSysMode(body) {
  return obsPost(endpointPath(digitalLife.sysMode), body)
}

export function fetchSysModeHistory(params = {}) {
  return getObs(digitalLife.sysModeHistory, params)
}

export function updateAutoRules(body) {
  return obsPut(endpointPath(digitalLife.sysModeAutoRules), body)
}

export function postSysModeEvent(eventType) {
  return obsPost(endpointPath(digitalLife.sysModeEvent, {}, { event_type: eventType }), {})
}

export function fetchVtsStats(params = {}) {
  return getObs(digitalLife.vtsStats, params)
}

export function fetchLifeRuntimeBundle() {
  return Promise.allSettled([
    fetchLifeRuntimePart(digitalLife.state.path),
    fetchLifeRuntimePart(digitalLife.runtimeHealth.path),
    fetchLifeRuntimePart(digitalLife.environment.path),
    fetchLifeRuntimePart(digitalLife.organs.path),
    fetchLifeRuntimePart(digitalLife.experience.path),
    fetchLifeRuntimePart(digitalLife.memoryHealth.path),
    fetchLifeRuntimePart(digitalLife.memoryEvents.path),
  ]).then((results) => ({
    state: unwrap(results[0]),
    runtimeHealth: unwrap(results[1]),
    environment: unwrap(results[2]),
    organs: unwrap(results[3]),
    experience: unwrap(results[4]),
    memoryHealth: unwrap(results[5]),
    memoryEvents: unwrap(results[6]),
    errors: results
      .map((result, index) => ({ result, index }))
      .filter(({ result }) => result.status === 'rejected')
      .map(({ result, index }) => ({ index, message: result.reason?.message || String(result.reason) })),
  }))
}

function fetchLifeRuntimePart(path) {
  return httpGet(path, { timeoutMs: LIFE_RUNTIME_TIMEOUT_MS })
}

function getObs(entry, params = {}, pathParams = {}) {
  return obsGet(endpointPath(entry, pathParams, params))
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}
