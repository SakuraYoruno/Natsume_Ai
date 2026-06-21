import { OBS_ENDPOINTS, observabilityGet } from './observabilityApiClient.js'

export function fetchInsightsSummarySnapshot(params = {}) {
  return observabilityGet(OBS_ENDPOINTS.insightsSummarySnapshot, params)
}
