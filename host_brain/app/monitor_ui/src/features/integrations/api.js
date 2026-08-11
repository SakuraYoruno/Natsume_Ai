import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet } from '../../shared/api/endpointClient.js'

const { integrations } = ENDPOINT_REGISTRY

export function fetchBodyIntegrationBundle() {
  return Promise.allSettled([
    endpointGet(integrations.mateCapabilities),
    endpointGet(integrations.mateHealth),
  ]).then((results) => ({
    capabilities: unwrap(results[0]),
    health: unwrap(results[1]),
    errors: results
      .map((result, index) => ({ result, index }))
      .filter(({ result }) => result.status === 'rejected')
      .map(({ result, index }) => ({ index, message: result.reason?.message || String(result.reason) })),
  }))
}

export function getMateDispatchAvailability() {
  const entries = [integrations.mateAction, integrations.mateSay]
  const retired = entries.filter((entry) => entry?.retired === true)
  return Object.freeze({
    available: retired.length === 0,
    commandIds: retired.map((entry) => String(entry.retiredCommandId || '')).filter(Boolean),
    replacementAuthority: String(retired[0]?.replacementAuthority || ''),
    replacementWave: String(retired[0]?.replacementWave || ''),
  })
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}
