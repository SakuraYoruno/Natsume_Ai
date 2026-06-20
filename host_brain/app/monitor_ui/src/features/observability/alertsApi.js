import { fetchServicesHealth } from '../../shared/api/sharedObservability.js'
import { fetchCapabilityGapStats } from './diagnosticsApi.js'
import { fetchErrors } from './logApi.js'

export {
  fetchCapabilityGapStats,
  fetchErrors,
  fetchServicesHealth,
}
