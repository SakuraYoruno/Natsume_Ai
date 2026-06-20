import { useCallback, useMemo } from 'react'
import {
  fetchLifeBody,
  fetchLifeTimeline,
  fetchPathologies,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useLifeBodyQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchLifeBody({ window_minutes: windowMinutes })
  }, [windowMinutes])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.lifeBody(windowMinutes),
    queryFn,
    retryCount: 1,
    retryDelayMs: 1_000,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.lifeBody,
  })
}

export function useLifeTimelineQuery({
  windowMinutes = 360,
  eventType = '',
  organ = '',
  requestId = '',
  sessionId = '',
  tickId = '',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const hasFocusedFilter = Boolean(eventType || organ || requestId || sessionId || tickId)
  const params = useMemo(() => ({
    window_minutes: windowMinutes,
    type: eventType || undefined,
    organ: organ || undefined,
    request_id: requestId || undefined,
    session_id: sessionId || undefined,
    tick_id: tickId || undefined,
  }), [eventType, organ, requestId, sessionId, tickId, windowMinutes])
  const queryFn = useCallback(async () => fetchLifeTimeline(params), [params])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.lifeTimeline(params),
    queryFn,
    retryCount: requestId || sessionId || tickId ? 2 : 1,
    retryDelayMs: requestId || sessionId || tickId ? 1_200 : 1_000,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: hasFocusedFilter ? Math.min(OBS_STALE_TIMES.lifeTimeline, 5_000) : OBS_STALE_TIMES.lifeTimeline,
  })
}

export function usePathologiesQuery({
  windowMinutes = 360,
  organ = '',
  severity = '',
  pathologyId = '',
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const params = useMemo(() => ({
    window_minutes: windowMinutes,
    organ: organ || undefined,
    severity: severity || undefined,
    pathology_id: pathologyId || undefined,
  }), [organ, pathologyId, severity, windowMinutes])
  const queryFn = useCallback(async () => fetchPathologies(params), [params])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.pathologies(params),
    queryFn,
    retryCount: pathologyId ? 2 : 1,
    retryDelayMs: pathologyId ? 1_200 : 1_000,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.pathologies,
  })
}
