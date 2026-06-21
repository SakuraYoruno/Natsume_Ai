import { httpBlob } from '../../shared/api/httpClient.js'
import {
  OBS_ENDPOINTS,
  observabilityGet,
  observabilityPath,
  timeRangeToMinutes,
} from './observabilityApiClient.js'

export function fetchSessions(params = {}) {
  const { time_range, ...rest } = params
  return observabilityGet(OBS_ENDPOINTS.sessions, {
    ...rest,
    window_minutes: time_range ? timeRangeToMinutes(time_range) : undefined,
  })
}

export async function exportSessionsCsv(params = {}) {
  const blob = await httpBlob(observabilityPath(OBS_ENDPOINTS.sessionsExport, params))
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `sessions_${Date.now()}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
