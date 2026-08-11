import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet } from '../../shared/api/endpointClient.js'

export function fetchGovernanceSnapshot() {
  return endpointGet(ENDPOINT_REGISTRY.governance.snapshot)
}
