import { useCallback } from 'react'
import {
  fetchBondHealth,
  fetchBondStage,
  fetchBondUsers,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

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
