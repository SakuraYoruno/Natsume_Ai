import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet } from '../../shared/api/endpointClient.js'

export function fetchEvolutionLabSuites({
  limit = 20,
  cursor = '',
  workspaceId = '',
} = {}) {
  return endpointGet(ENDPOINT_REGISTRY.evolutionLab.suites, {
    limit,
    cursor,
    workspace_id: workspaceId,
  })
}
