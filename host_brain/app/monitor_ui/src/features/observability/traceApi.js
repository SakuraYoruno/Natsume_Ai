import { fetchRetrievalRuntime } from '../../shared/api/sharedObservability.js'
import { createObsEventSource } from '../../shared/api/obsClient.js'
import {
  OBS_ENDPOINTS,
  observabilityGet,
  observabilityPath,
} from './observabilityApiClient.js'

export { fetchRetrievalRuntime }

export function fetchTraceNodes(requestId) {
  return observabilityGet(OBS_ENDPOINTS.traceRequest, {}, { requestId })
}

export function fetchTraceStreamEvents(requestId) {
  return observabilityGet(OBS_ENDPOINTS.traceStreamEvents, {}, { requestId })
}

export function fetchTraceLiveSnapshot(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.traceFeedSnapshot, params)
}

export function fetchGraphSchema() {
  return observabilityGet(OBS_ENDPOINTS.graphSchema)
}

export function fetchTribeEvents(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.tribeEvents, params)
}

export function connectTraceLiveFeed(params = {}, handlers = {}) {
  const source = createObsEventSource(observabilityPath(OBS_ENDPOINTS.traceFeedLive), params)

  const parseEvent = (event) => {
    try {
      return JSON.parse(event.data)
    } catch (error) {
      console.warn('[SSE] Failed to parse trace live feed payload:', error)
      return null
    }
  }

  source.onopen = () => {
    handlers.onOpen?.()
  }

  source.addEventListener('snapshot', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onSnapshot?.(data)
  })

  source.addEventListener('delta', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onDelta?.(data)
  })

  source.addEventListener('heartbeat', (event) => {
    const data = parseEvent(event)
    if (data) handlers.onHeartbeat?.(data)
  })

  source.addEventListener('trace_error', (event) => {
    const data = parseEvent(event)
    handlers.onError?.(data ?? { type: 'error', detail: 'trace live feed payload parse failed' })
  })

  source.onerror = (error) => {
    handlers.onConnectionError?.(error)
  }

  return {
    close: () => source.close(),
    source,
  }
}

export function connectTraceRequestLive(requestId, handlers = {}, params = {}) {
  if (!requestId) {
    throw new Error('requestId is required')
  }
  return connectTraceLiveFeed(
    {
      keyword: requestId,
      limit: 1,
      window_minutes: 10080,
      interval_ms: 1000,
      ...params,
    },
    handlers
  )
}
