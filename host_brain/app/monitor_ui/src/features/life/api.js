import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost, endpointPut } from '../../shared/api/endpointClient.js'

const { digitalLife } = ENDPOINT_REGISTRY
const LIFE_RUNTIME_TIMEOUT_MS = 6_000

export { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'
export {
  fetchCanonDocuments,
  fetchContextCompressorStats,
  fetchDistillationStats,
  fetchEpisodicStoreStats,
} from '../../shared/api/sharedObservability.js'

export function fetchDigitalLifeOverviewSnapshot(params = {}) {
  return endpointGet(digitalLife.overviewSnapshot, params)
}

export function fetchDigitalLifeWallClockSoak(params = {}) {
  return endpointGet(digitalLife.wallClockSoak, params)
}

export function fetchLifeEvolutionSnapshot(params = {}) {
  return endpointGet(digitalLife.lifeEvolutionSnapshot, params)
}

export function postLifeEvolutionPreview(body = {}) {
  return endpointPost(digitalLife.lifeEvolutionPreview, body || {})
}

export function fetchLifeBody(params = {}) {
  return endpointGet(digitalLife.lifeBody, params)
}

export function fetchLifeTimeline(params = {}) {
  return endpointGet(digitalLife.lifeTimeline, params)
}

export function fetchPathologies(params = {}) {
  return endpointGet(digitalLife.pathologies, params)
}

export function fetchAvatarState(params = {}) {
  return endpointGet(digitalLife.avatarState, params)
}

export function postAvatarBehavior(body = {}) {
  return endpointPost(digitalLife.avatarBehavior, body || {})
}

export function fetchDigitalLifeRuntimeSnapshot(params = {}) {
  return endpointGet(digitalLife.runtimeSnapshot, params)
}

export function fetchMemoryStats(params = {}) {
  return endpointGet(digitalLife.memoryStats, params)
}

export function fetchResidentMemoryContext() {
  return endpointGet(digitalLife.residentMemoryContext)
}

export function fetchBondHealth() {
  return endpointGet(digitalLife.bondHealth)
}

export function fetchBondUsers() {
  return endpointGet(digitalLife.bondUsers)
}

export function fetchBondStage(userId) {
  return endpointGet(digitalLife.bondStage, {}, { userId })
}

export function fetchBondSummary(userId) {
  return endpointGet(digitalLife.bondSummary, {}, { userId })
}

export function submitBondEvent(body) {
  return endpointPost(digitalLife.bondEvent, body)
}

export function fetchSysModeStatus() {
  return endpointGet(digitalLife.sysMode)
}

export function switchSysMode(body) {
  return endpointPost(digitalLife.sysMode, body)
}

export function fetchSysModeHistory(params = {}) {
  return endpointGet(digitalLife.sysModeHistory, params)
}

export function updateAutoRules(body) {
  return endpointPut(digitalLife.sysModeAutoRules, body)
}

export function postSysModeEvent(eventType) {
  return endpointPost(digitalLife.sysModeEvent, {}, {}, { event_type: eventType })
}

export function fetchVtsStats(params = {}) {
  return endpointGet(digitalLife.vtsStats, params)
}

export function fetchLifeRuntimeBundle() {
  return Promise.allSettled([
    fetchLifeRuntimePart(digitalLife.state),
    fetchLifeRuntimePart(digitalLife.runtimeHealth),
    fetchLifeRuntimePart(digitalLife.environment),
    fetchLifeRuntimePart(digitalLife.organs),
    fetchLifeRuntimePart(digitalLife.experience),
    fetchLifeRuntimePart(digitalLife.memoryHealth),
    fetchLifeRuntimePart(digitalLife.memoryEvents),
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

function fetchLifeRuntimePart(entry) {
  return endpointGet(entry, {}, {}, { timeoutMs: LIFE_RUNTIME_TIMEOUT_MS })
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}
