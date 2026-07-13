import { ENDPOINT_REGISTRY } from './endpointRegistry.js'
import { submitOperatorCommandAsSse } from './operatorCommandGateway.js'

export function sendChatDebugStream(payload, options = {}) {
  return submitOperatorCommandAsSse(
    ENDPOINT_REGISTRY.execution.chatDebug.commandId,
    payload,
    options,
  )
}
