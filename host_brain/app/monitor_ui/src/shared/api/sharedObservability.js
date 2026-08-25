import { ENDPOINT_REGISTRY } from './endpointRegistry.js'
import { endpointGet } from './endpointClient.js'

const { digitalLife, observability } = ENDPOINT_REGISTRY

export function fetchCanonDocuments() {
  return endpointGet(digitalLife.canonDocuments)
}

export function fetchCodexBioOpsObservability(params = {}) {
  return endpointGet(observability.codexBioOps, params)
}

export function fetchDistillationStats(params = {}) {
  return endpointGet(observability.distillationStats, params)
}

export function fetchEpisodicStoreStats(params = {}) {
  return endpointGet(observability.episodicStoreStats, params)
}

export function fetchLlmCallStats(params = {}) {
  return endpointGet(observability.llmCallStats, params)
}

export function fetchRateLimitBackendStats(params = {}) {
  return endpointGet(observability.rateLimitBackendStats, params)
}

export function fetchRetrievalRuntime(params = {}) {
  return endpointGet(observability.retrievalRuntime, params)
}

export function fetchServicesHealth(params = {}) {
  return endpointGet(observability.servicesHealth, params)
}
