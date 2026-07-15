import { useCallback } from 'react'
import { fetchSkillCatalogStats } from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useSkillCatalogQuery({
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const result = await fetchSkillCatalogStats()
      return {
        data: result,
        catalogError: null,
      }
    } catch (error) {
      return {
        data: null,
        catalogError: error?.message || '技能目录数据加载失败',
      }
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.skillCatalog(),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.skillCatalog,
  })

  return {
    ...query,
    catalogData: query.data?.data ?? null,
    catalogError: query.data?.catalogError ?? null,
  }
}
