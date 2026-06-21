import { useCallback } from 'react'
import { fetchPersonaFirstBatchCatalog } from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function usePersonaPromptCatalogQuery() {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchPersonaFirstBatchCatalog()
      return {
        data: response,
        catalogError: null,
      }
    } catch (error) {
      return {
        data: null,
        catalogError: error?.message || '人格文案目录加载失败',
      }
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.personaPromptCatalog(),
    queryFn,
    staleTimeMs: OBS_STALE_TIMES.promptConfig,
  })

  return {
    ...query,
    catalogData: query.data?.data ?? null,
    bundles: query.data?.data?.bundles ?? [],
    catalogError: query.data?.catalogError ?? null,
  }
}
