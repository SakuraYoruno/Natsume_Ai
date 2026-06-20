import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { endpointGet, endpointPost } from '../../shared/api/endpointClient.js'

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

export function sendMateEngineAction(payload) {
  return endpointPost(integrations.mateAction, payload)
}

export function sendMateEngineSay(payload) {
  return endpointPost(integrations.mateSay, payload)
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}
