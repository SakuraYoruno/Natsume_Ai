import { deepRepairMojibake } from '../../utils/text.js'
import { ENDPOINT_REGISTRY } from './endpointRegistry.js'
import { httpRequest } from './httpClient.js'

const DEFAULT_TIMEOUT_MS = 35_000
const HOT_GET_CACHE_TTL_MS = 1_500
const { digitalLife, observability } = ENDPOINT_REGISTRY
const HOT_GET_ROUTES = [
  { path: digitalLife.runtimeSnapshot.path },
  { path: observability.promptBuilderStats.path },
  { path: observability.capabilityGapStats.path },
  { path: observability.insightsSummarySnapshot.path },
  { path: digitalLife.overviewSnapshot.path },
  { path: digitalLife.wallClockSoak.path },
  { path: digitalLife.avatarState.path },
  { path: observability.codexBioOps.path, match: 'family' },
  { path: digitalLife.lifeBody.path },
  { path: digitalLife.lifeTimeline.path },
  { path: digitalLife.pathologies.path },
  { path: observability.traceFeedSnapshot.path },
]

const inflightHotGets = new Map()
const hotGetCache = new Map()

export function buildQueryPath(path, params = {}) {
  const search = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  })
  const suffix = search.toString()
  return suffix ? `${path}?${suffix}` : path
}

export function invalidateObsCachePrefix(prefix) {
  const normalizedPrefix = String(prefix || '')
  for (const key of hotGetCache.keys()) {
    if (matchesCachePrefix(String(key), normalizedPrefix)) hotGetCache.delete(key)
  }
}

export function obsGet(path, params = {}, options = {}) {
  const url = buildQueryPath(path, params)
  return requestObs(url, { ...options, method: 'GET' })
}

export function obsPost(path, body = {}, params = {}, options = {}) {
  const url = buildQueryPath(path, params)
  return requestObs(url, { ...options, method: 'POST', body: body || {} })
}

export function obsPut(path, body = {}, params = {}, options = {}) {
  const url = buildQueryPath(path, params)
  return requestObs(url, { ...options, method: 'PUT', body: body || {} })
}

export function obsRequest(path, options = {}) {
  return requestObs(path, options)
}

export function createObsEventSource(path, params = {}) {
  if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
    throw new Error('EventSource not available')
  }
  return new EventSource(buildQueryPath(path, params))
}

export function createObsWebSocket(path) {
  if (typeof window === 'undefined' || typeof window.WebSocket === 'undefined') {
    throw new Error('WebSocket not available')
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(`${protocol}//${window.location.host}${path}`)
}

function requestObs(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase()
  if (method !== 'GET' || !isHotGet(url)) {
    return executeObsRequest(url, options)
  }

  const cached = getCached(url)
  if (cached != null) return cached

  const inflight = inflightHotGets.get(url)
  if (inflight) return inflight.then((data) => clonePayload(data))

  const promise = executeObsRequest(url, options)
    .then((data) => {
      setCached(url, data)
      return data
    })
    .finally(() => {
      inflightHotGets.delete(url)
    })

  inflightHotGets.set(url, promise)
  return promise.then((data) => clonePayload(data))
}

function executeObsRequest(url, options = {}) {
  return httpRequest(url, {
    timeoutMs: DEFAULT_TIMEOUT_MS,
    ...options,
  }).then((payload) => deepRepairMojibake(payload))
}

function isHotGet(url) {
  const path = String(url || '')
  return HOT_GET_ROUTES.some((route) => matchesHotGetRoute(path, route))
}

function matchesHotGetRoute(path, route) {
  if (!route?.path) return false
  if (path === route.path || path.startsWith(`${route.path}?`)) return true
  return route.match === 'family' && path.startsWith(`${route.path}/`)
}

function matchesCachePrefix(path, prefix) {
  if (!prefix) return false
  return path === prefix || path.startsWith(`${prefix}?`) || path.startsWith(`${prefix}/`)
}

function getCached(url) {
  const cached = hotGetCache.get(url)
  if (!cached) return null
  if (cached.expiresAtMs <= Date.now()) {
    hotGetCache.delete(url)
    return null
  }
  return clonePayload(cached.data)
}

function setCached(url, data) {
  hotGetCache.set(url, {
    expiresAtMs: Date.now() + HOT_GET_CACHE_TTL_MS,
    data: clonePayload(data),
  })
}

function clonePayload(payload) {
  if (payload == null) return payload
  if (typeof globalThis.structuredClone === 'function') {
    return globalThis.structuredClone(payload)
  }
  return JSON.parse(JSON.stringify(payload))
}
