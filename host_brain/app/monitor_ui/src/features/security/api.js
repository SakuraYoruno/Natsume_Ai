import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'

const { securityImmune } = ENDPOINT_REGISTRY

export function fetchSecurityRuntime() {
  return endpointGet(securityImmune.runtime)
}

export function fetchSecurityAssets() {
  return endpointGet(securityImmune.assets)
}

export function fetchSecurityIncidents(minRisk = 'low') {
  return endpointGet(securityImmune.incidents, { min_risk: minRisk })
}

export function runSecurityHuntOnce(reason = 'manual_web_trigger') {
  return endpointPost(securityImmune.huntRunOnce, { reason }, {}, {}, { timeoutMs: 15000 })
}
