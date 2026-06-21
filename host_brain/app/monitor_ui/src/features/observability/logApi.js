import { OBS_ENDPOINTS, observabilityGet } from './observabilityApiClient.js'

export function fetchErrors(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.errors, params)
}
