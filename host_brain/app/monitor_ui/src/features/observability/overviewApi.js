import { fetchServicesHealth } from '../../shared/api/sharedObservability.js'
import {
  fetchAgentLoopStats,
} from '../../shared/api/runtimeObservability.js'
import { createObsWebSocket } from '../../shared/api/obsClient.js'
import { fetchSubsystemsStats } from './diagnosticsApi.js'
import { OBS_ENDPOINTS, observabilityPath } from './observabilityApiClient.js'

export {
  fetchAgentLoopStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
}

export function connectHealthWebSocket(onMessage, onError, onClose) {
  const ws = createObsWebSocket(observabilityPath(OBS_ENDPOINTS.healthWebSocket))

  ws.onopen = () => {
    console.log('[WS] Health WebSocket connected')
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage?.(data)
    } catch (error) {
      console.warn('[WS] Failed to parse message:', error)
    }
  }

  ws.onerror = (error) => {
    console.warn('[WS] Health WebSocket error:', error)
    onError?.(error)
  }

  ws.onclose = () => {
    console.log('[WS] Health WebSocket closed')
    onClose?.()
  }

  return {
    close: () => ws.close(),
    ws,
  }
}
