import { ENDPOINT_REGISTRY } from '../../shared/api/endpointRegistry.js'
import { httpGet, httpPost } from '../../shared/api/httpClient.js'

const { integrations } = ENDPOINT_REGISTRY

export function fetchBodyIntegrationBundle() {
  return Promise.allSettled([
    httpGet(integrations.mateCapabilities.path),
    httpGet(integrations.mateHealth.path),
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
  return httpPost(integrations.mateAction.path, payload)
}

export function sendMateEngineSay(payload) {
  return httpPost(integrations.mateSay.path, payload)
}

function unwrap(result) {
  return result.status === 'fulfilled' ? result.value : null
}
