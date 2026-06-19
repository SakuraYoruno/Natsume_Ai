import { ENDPOINT_REGISTRY } from './endpointRegistry.js'
import { httpStreamPost } from './httpClient.js'

const SSE_HEADERS = { Accept: 'text/event-stream' }

export function sendChatDebugStream(payload, options = {}) {
  return httpStreamPost(ENDPOINT_REGISTRY.execution.chatDebug.path, payload, {
    ...options,
    headers: { ...SSE_HEADERS, ...(options.headers || {}) },
  })
}
