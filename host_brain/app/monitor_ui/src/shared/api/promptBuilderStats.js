import { ENDPOINT_REGISTRY, endpointPath } from './endpointRegistry.js'
import { obsGet } from './obsClient.js'

export function fetchPromptBuilderStats(params = {}) {
  return obsGet(endpointPath(ENDPOINT_REGISTRY.operations.promptBuilderStats, {}, params))
}
