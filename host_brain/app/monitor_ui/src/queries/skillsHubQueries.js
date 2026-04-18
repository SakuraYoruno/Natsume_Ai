import { useCallback } from 'react'
import { fetchSkillsHubStats } from '../api/skillsHub.js'
import { OBS_STALE_TIMES } from './freshness.js'
import { OBS_QUERY_KEYS } from './queryKeys.js'
import useObservabilityQuery from './useObservabilityQuery.js'

export function useSkillsHubQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const result = await fetchSkillsHubStats()
      return {
        data: result,
        hubError: null,
      }
    } catch (error) {
      return {
        data: null,
        hubError: error?.message || '技能中心数据加载失败',
      }
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.skillsHub(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.skillsHub,
  })

  return {
    ...query,
    hubData: query.data?.data ?? null,
    hubError: query.data?.hubError ?? null,
  }
}
