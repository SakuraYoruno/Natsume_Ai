import { endpointGet } from './endpointClient.js'
import { ENDPOINT_REGISTRY } from './endpointRegistry.js'

const { execution } = ENDPOINT_REGISTRY

export function fetchImmuneGateReplay(requestId, params = {}) {
  const normalizedRequestId = String(requestId || '').trim()
  if (!normalizedRequestId) {
    throw new Error('request_id is required')
  }
  return endpointGet(execution.immuneGateReplay, params, { requestId: normalizedRequestId })
}
