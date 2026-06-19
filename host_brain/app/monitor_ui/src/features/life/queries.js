import { useCallback, useMemo } from 'react'
import {
  fetchAvatarState,
  fetchBondHealth,
  fetchBondStage,
  fetchBondUsers,
  fetchCanonDocuments,
  fetchContextCompressorStats,
  fetchDigitalLifeRuntimeSnapshot,
  fetchDigitalLifeOverviewSnapshot,
  fetchDigitalLifeWallClockSoak,
  fetchDistillationStats,
  fetchEpisodicStoreStats,
  fetchLifeBody,
  fetchLifeTimeline,
  fetchMemoryStats,
  fetchPathologies,
  fetchResidentMemoryContext,
  fetchSysModeHistory,
  fetchSysModeStatus,
  fetchVtsStats,
} from './api.js'
import { fetchPromptBuilderStats } from '../../shared/api/promptBuilderStats.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function sleep(ms) {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms)
  })
}

async function loadRuntimeSnapshotWithRetry() {
  let lastError = null
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await fetchDigitalLifeRuntimeSnapshot()
    } catch (error) {
      lastError = error
      if (attempt === 0) {
        await sleep(600)
      }
    }
  }
  throw lastError
}

function buildStageDistribution(samples) {
  const distribution = samples.reduce((result, item) => {
    if (!item?.current_stage) return result
    result[item.current_stage] = (result[item.current_stage] || 0) + 1
    return result
  }, {})

  return Object.entries(distribution).map(([stage, count]) => ({
    stage,
    count,
  }))
}

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

export function useDigitalLifeOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    return fetchDigitalLifeOverviewSnapshot({ window_minutes: windowMinutes })
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.digitalLifeOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.overview,
  })

  return {
    ...query,
    vtsData: query.data?.vtsData ?? null,
    memoryData: query.data?.memoryData ?? null,
    servicesData: query.data?.servicesData ?? null,
    subsystemsData: query.data?.subsystemsData ?? null,
    capabilityGapData: query.data?.capabilityGapData ?? null,
    humanoidLoopData: query.data?.humanoidLoopData ?? null,
    humanoidLoopTrendData: query.data?.humanoidLoopTrendData ?? null,
    humanoidLoopAlertsData: query.data?.humanoidLoopAlertsData ?? null,
    humanoidLoopRemediationData: query.data?.humanoidLoopRemediationData ?? null,
    humanoidLoopDiagnosticsData: query.data?.humanoidLoopDiagnosticsData ?? null,
    humanoidLoopHotspotsData: query.data?.humanoidLoopHotspotsData ?? null,
    humanoidLoopHotspotTrendData: query.data?.humanoidLoopHotspotTrendData ?? null,
    humanoidLoopSourceCorrelationData: query.data?.humanoidLoopSourceCorrelationData ?? null,
  }
}

export function useDigitalLifeWallClockSoakQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 60,
} = {}) {
  const queryFn = useCallback(async () => fetchDigitalLifeWallClockSoak(), [])

  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.digitalLifeWallClockSoak(),
    queryFn,
    retryCount: 1,
    retryDelayMs: 1_200,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 60_000,
    staleTimeMs: OBS_STALE_TIMES.wallClockSoak,
  })
}

export function useMemoryOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [memory, episodic, distill, canon, compress, resident] = await Promise.all([
      fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchEpisodicStoreStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchDistillationStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchCanonDocuments().catch(() => null),
      fetchContextCompressorStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchResidentMemoryContext().catch(() => null),
    ])

    return {
      memoryData: memory,
      episodicData: episodic,
      distillData: distill,
      canonDocs: canon,
      compressData: compress,
      residentContext: resident,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.memoryOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.memory,
  })

  return {
    ...query,
    memoryData: query.data?.memoryData ?? null,
    episodicData: query.data?.episodicData ?? null,
    distillData: query.data?.distillData ?? null,
    canonDocs: query.data?.canonDocs ?? null,
    compressData: query.data?.compressData ?? null,
    residentContext: query.data?.residentContext ?? null,
  }
}

export function useRuntimeSignalsQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
  minIntervalSeconds = 15,
} = {}) {
  const queryFn = useCallback(async () => {
    const [runtimeRes, promptRes] = await Promise.allSettled([
      loadRuntimeSnapshotWithRetry(),
      fetchPromptBuilderStats({ window_minutes: windowMinutes }),
    ])

    return {
      runtimeSnapshot: runtimeRes.status === 'fulfilled' ? runtimeRes.value : null,
      promptStats: promptRes.status === 'fulfilled' ? promptRes.value : null,
      runtimeError: runtimeRes.status === 'rejected'
        ? (runtimeRes.reason?.message || 'runtime snapshot load failed')
        : null,
      promptError: promptRes.status === 'rejected'
        ? (promptRes.reason?.message || 'prompt builder stats load failed')
        : null,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.runtimeSignals(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(pollingInterval, minIntervalSeconds) * 1000,
    minPollingIntervalMs: minIntervalSeconds * 1000,
    staleTimeMs: OBS_STALE_TIMES.runtime,
  })

  return {
    ...query,
    runtimeSnapshot: query.data?.runtimeSnapshot ?? null,
    promptStats: query.data?.promptStats ?? null,
    error: query.data?.runtimeError ?? query.error ?? null,
    promptError: query.data?.promptError ?? null,
    reload: query.refetch,
  }
}

export function useAvatarStateQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 15,
} = {}) {
  const queryFn = useCallback(async () => fetchAvatarState(), [])
  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.avatarState(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(15, Number(pollingInterval || 15)) * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.runtime,
  })

  return {
    ...query,
    avatarState: query.data ?? null,
    reload: query.refetch,
  }
}

export function useEmotionOverviewQuery({
  windowMinutes = 360,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [vts, memory] = await Promise.all([
      fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
    ])

    return { vtsData: vts, memoryData: memory }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.emotionOverview(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.emotion,
  })

  return {
    ...query,
    vtsData: query.data?.vtsData ?? null,
    memoryData: query.data?.memoryData ?? null,
  }
}

export function useRuntimeModeConsoleQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [statusRes, historyRes] = await Promise.allSettled([
      fetchSysModeStatus(),
      fetchSysModeHistory({ limit: 15 }),
    ])

    const errors = []
    if (statusRes.status === 'rejected') {
      errors.push(statusRes.reason?.message || '生命体存在态轴加载失败')
    }
    if (historyRes.status === 'rejected') {
      errors.push(historyRes.reason?.message || '切换历史加载失败')
    }

    return {
      status: statusRes.status === 'fulfilled' ? statusRes.value : null,
      history: historyRes.status === 'fulfilled' ? (historyRes.value?.items ?? []) : [],
      consoleError: errors[0] || null,
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.runtimeModeConsole(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.runtimeMode,
  })

  return {
    ...query,
    statusData: query.data?.status ?? null,
    historyData: query.data?.history ?? [],
    consoleError: query.data?.consoleError ?? null,
  }
}

export function useBondOverviewQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [healthRes, usersRes] = await Promise.allSettled([
      fetchBondHealth(),
      fetchBondUsers(),
    ])

    const health = healthRes.status === 'fulfilled' ? healthRes.value : null
    const usersResponse = usersRes.status === 'fulfilled' ? usersRes.value : null
    const users = Array.isArray(usersResponse?.users)
      ? usersResponse.users
      : Array.isArray(usersResponse)
        ? usersResponse
        : []

    const stageSamples = users.length > 0
      ? await Promise.allSettled(
        users.slice(0, 24).map((userId) => fetchBondStage(userId))
      )
      : []

    const fulfilledStageSamples = stageSamples
      .filter((item) => item.status === 'fulfilled')
      .map((item) => item.value)

    const errors = []
    if (healthRes.status === 'rejected') {
      errors.push(healthRes.reason?.message || '关系健康加载失败')
    }
    if (usersRes.status === 'rejected') {
      errors.push(usersRes.reason?.message || '关系用户列表加载失败')
    }
    if (users.length > 0 && fulfilledStageSamples.length === 0 && stageSamples.length > 0) {
      errors.push('关系阶段分布加载失败')
    }

    return {
      health,
      users,
      stageDist: buildStageDistribution(fulfilledStageSamples),
      stageSampleCount: fulfilledStageSamples.length,
      overviewError: errors[0] || null,
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.bondOverview(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.bondOverview,
  })

  return {
    ...query,
    health: query.data?.health ?? null,
    users: query.data?.users ?? [],
    stageDist: query.data?.stageDist ?? [],
    stageSampleCount: query.data?.stageSampleCount ?? 0,
    overviewError: query.data?.overviewError ?? null,
  }
}

export function useBondUserDetailQuery({
  userId,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const detail = await fetchBondStage(userId)
      return { detail, detailError: null }
    } catch (error) {
      return {
        detail: null,
        detailError: error?.message || '关系用户详情加载失败',
      }
    }
  }, [userId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.bondUserDetail(userId),
    queryFn,
    enabled: Boolean(userId),
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.bondUserDetail,
  })

  return {
    ...query,
    userDetail: query.data?.detail ?? null,
    detailError: query.data?.detailError ?? null,
  }
}
