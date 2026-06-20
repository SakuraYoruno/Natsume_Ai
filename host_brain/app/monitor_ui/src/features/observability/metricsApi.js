import { fetchRateLimitBackendStats } from '../../shared/api/sharedObservability.js'
import {
  OBS_ENDPOINTS,
  observabilityGet,
  timeRangeToMinutes,
} from './observabilityApiClient.js'

export { fetchRateLimitBackendStats }

export function fetchMetricsTimeseries(params = {}) {
  const { time_range, ...rest } = params
  const rangeMinutes = time_range ? timeRangeToMinutes(time_range) : 60
  const intervalMinutes = rangeMinutes <= 60 ? 5 : rangeMinutes <= 360 ? 30 : 60

  return observabilityGet(OBS_ENDPOINTS.metricsTimeseries, {
    ...rest,
    range_minutes: rangeMinutes,
    interval_minutes: intervalMinutes,
  })
}
