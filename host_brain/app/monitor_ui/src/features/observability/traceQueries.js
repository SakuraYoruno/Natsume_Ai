import { useCallback } from 'react'
import {
  fetchRetrievalRuntime,
  fetchTraceLiveSnapshot,
  fetchTraceNodes,
  fetchTraceStreamEvents,
  fetchTribeEvents,
} from './traceApi.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function normalizeTraceError(error) {
  const message = error?.message || 'Trace 摘要拉取失败'
  return message.includes('404') ? '链路摘要尚未落盘，流结束后会自动补拉。' : message
}

export function useTraceSnapshotQuery({
  requestId = null,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const traceSnapshot = await fetchTraceNodes(requestId)
      return {
        traceSnapshot,
        traceError: null,
      }
    } catch (error) {
      return {
        traceSnapshot: null,
        traceError: normalizeTraceError(error),
      }
    }
  }, [requestId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceSnapshot(requestId),
    queryFn,
    enabled: Boolean(requestId) && enabled,
    pollingEnabled: Boolean(requestId) && enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.traceSnapshot,
  })

  return {
    ...query,
    traceSnapshot: query.data?.traceSnapshot ?? null,
    traceError: query.data?.traceError ?? null,
  }
}

export function useTraceStreamEventsQuery({
  requestId = null,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const payload = await fetchTraceStreamEvents(requestId)
      return {
        streamEventsPayload: payload,
        streamEventsError: null,
      }
    } catch (error) {
      return {
        streamEventsPayload: null,
        streamEventsError: error?.message || '流式事件加载失败',
      }
    }
  }, [requestId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceStreamEvents(requestId),
    queryFn,
    enabled: Boolean(requestId) && enabled,
    pollingEnabled: Boolean(requestId) && enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.traceStreamEvents,
  })

  return {
    ...query,
    streamEventsPayload: query.data?.streamEventsPayload ?? null,
    streamEventsError: query.data?.streamEventsError ?? null,
  }
}

export function useTraceTribeEventsQuery({
  requestId = null,
  limit = 120,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const payload = await fetchTribeEvents({ request_id: requestId, limit })
      return {
        tribeEventsPayload: payload,
        tribeEventsError: null,
      }
    } catch (error) {
      return {
        tribeEventsPayload: null,
        tribeEventsError: error?.message || 'Tribe 事件加载失败',
      }
    }
  }, [limit, requestId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceTribeEvents(requestId, limit),
    queryFn,
    enabled: Boolean(requestId) && enabled,
    pollingEnabled: Boolean(requestId) && enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.traceTribeEvents,
  })

  return {
    ...query,
    tribeEventsPayload: query.data?.tribeEventsPayload ?? null,
    tribeEventsError: query.data?.tribeEventsError ?? null,
  }
}

export function useTraceTribeFeedQuery({
  queryParams,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const normalizedParams = queryParams || {}

  const queryFn = useCallback(async () => {
    try {
      const payload = await fetchTribeEvents(normalizedParams)
      return {
        tribeFeedPayload: payload,
        tribeFeedError: null,
      }
    } catch (error) {
      return {
        tribeFeedPayload: null,
        tribeFeedError: error?.message || 'Tribe 事件加载失败',
      }
    }
  }, [normalizedParams])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceTribeFeed(normalizedParams),
    queryFn,
    enabled,
    pollingEnabled: enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.traceTribeEvents,
  })

  return {
    ...query,
    tribeFeedPayload: query.data?.tribeFeedPayload ?? null,
    tribeFeedError: query.data?.tribeFeedError ?? null,
  }
}

export function useTraceRetrievalRuntimeQuery({
  requestId = null,
  limit = 5,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const retrievalRuntime = await fetchRetrievalRuntime({ request_id: requestId, limit, include_legacy: true })
      return {
        retrievalRuntime,
        retrievalRuntimeError: null,
      }
    } catch (error) {
      return {
        retrievalRuntime: null,
        retrievalRuntimeError: error?.message || '检索运行时加载失败',
      }
    }
  }, [limit, requestId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceRetrievalRuntime(requestId, limit),
    queryFn,
    enabled: Boolean(requestId) && enabled,
    pollingEnabled: Boolean(requestId) && enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.traceRetrievalRuntime,
  })

  return {
    ...query,
    retrievalRuntime: query.data?.retrievalRuntime ?? null,
    retrievalRuntimeError: query.data?.retrievalRuntimeError ?? null,
  }
}

export function useTraceLiveSnapshotQuery({
  queryParams,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const normalizedParams = queryParams || {}

  const queryFn = useCallback(async () => {
    try {
      const liveSnapshot = await fetchTraceLiveSnapshot(normalizedParams)
      return {
        liveSnapshot,
        liveSnapshotError: null,
      }
    } catch (error) {
      return {
        liveSnapshot: null,
        liveSnapshotError: error?.message || '实时链路快照加载失败',
      }
    }
  }, [normalizedParams])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.traceLiveSnapshot(normalizedParams),
    queryFn,
    enabled,
    pollingEnabled: enabled && pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 10_000,
    staleTimeMs: OBS_STALE_TIMES.traceLiveSnapshot,
  })

  return {
    ...query,
    liveSnapshot: query.data?.liveSnapshot ?? null,
    liveSnapshotError: query.data?.liveSnapshotError ?? null,
  }
}
