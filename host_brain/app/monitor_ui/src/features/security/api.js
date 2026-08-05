import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet } from '../../shared/api/endpointClient.js'

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

export function getSecurityHuntAvailability() {
  const entry = securityImmune.huntRunOnce
  return Object.freeze({
    available: entry?.retired !== true,
    commandId: String(entry?.retiredCommandId || entry?.commandId || ''),
    replacementAuthority: String(entry?.replacementAuthority || ''),
    replacementWave: String(entry?.replacementWave || ''),
  })
}
