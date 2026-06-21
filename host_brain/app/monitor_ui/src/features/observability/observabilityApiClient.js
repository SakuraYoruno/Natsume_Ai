import { ENDPOINT_REGISTRY, endpointPath } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'
import { invalidateObsCachePrefix } from '../../shared/api/obsClient.js'

export const OBS_ENDPOINTS = ENDPOINT_REGISTRY.observability
export const CODEX_BIO_OPS_CACHE_PREFIX = endpointPath(OBS_ENDPOINTS.codexBioOps)

export function timeRangeToMinutes(range) {
  const map = { '1h': 60, '6h': 360, '24h': 1440, '7d': 10080 }
  return map[range] ?? 60
}

export function observabilityPath(endpoint, query = {}, pathParams = {}) {
  return endpointPath(endpoint, pathParams, query)
}

export function observabilityGet(endpoint, query = {}, pathParams = {}) {
  return endpointGet(endpoint, query, pathParams)
}

export function observabilityPost(endpoint, body = {}, query = {}, pathParams = {}, invalidatePrefix = '') {
  return endpointPost(endpoint, body || {}, pathParams, query)
    .then((payload) => {
      if (invalidatePrefix) invalidateObsCachePrefix(invalidatePrefix)
      return payload
    })
}

export function requireCandidateId(candidateId) {
  const id = String(candidateId || '').trim()
  if (!id) {
    throw new Error('candidate_id is required')
  }
  return id
}
