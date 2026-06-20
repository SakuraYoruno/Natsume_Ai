import { useCallback } from 'react'
import {
  fetchCanonDocuments,
  fetchCanonProposals,
  fetchVaultStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

export function useCanonProposalsQuery({
  status = '',
  page = 1,
  pageSize = 20,
  enabled = true,
} = {}) {
  const normalizedStatus = String(status || '').trim()
  const params = {
    status: normalizedStatus || 'all',
    page,
    pageSize,
  }

  const queryFn = useCallback(async () => {
    try {
      const response = await fetchCanonProposals({
        status: normalizedStatus || undefined,
        page,
        page_size: pageSize,
      })
      return {
        data: response,
        proposalsError: null,
      }
    } catch (error) {
      return {
        data: null,
        proposalsError: error?.message || '提案加载失败',
      }
    }
  }, [normalizedStatus, page, pageSize])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.canonProposals(params),
    queryFn,
    enabled,
    staleTimeMs: OBS_STALE_TIMES.canon,
  })

  return {
    ...query,
    proposalsData: query.data?.data ?? null,
    proposals: query.data?.data?.items ?? [],
    total: Number(query.data?.data?.total ?? 0),
    proposalsError: query.data?.proposalsError ?? null,
  }
}

export function useCanonDocumentsQuery({ enabled = true } = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchCanonDocuments()
      return {
        data: response,
        documentsError: null,
      }
    } catch (error) {
      return {
        data: null,
        documentsError: error?.message || '文档加载失败',
      }
    }
  }, [])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.canonDocuments(),
    queryFn,
    enabled,
    staleTimeMs: OBS_STALE_TIMES.canon,
  })

  return {
    ...query,
    documentsData: query.data?.data ?? null,
    documents: query.data?.data?.items ?? query.data?.data?.documents ?? [],
    documentsError: query.data?.documentsError ?? null,
  }
}

export function useCanonVaultStatsQuery({ limit = 10 } = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchVaultStats({ limit })
      return {
        data: response,
        vaultError: null,
      }
    } catch (error) {
      return {
        data: null,
        vaultError: error?.message || '清单统计加载失败',
      }
    }
  }, [limit])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.canonVaultStats(limit),
    queryFn,
    staleTimeMs: OBS_STALE_TIMES.canonVault,
  })

  return {
    ...query,
    vaultData: query.data?.data ?? null,
    vaultError: query.data?.vaultError ?? null,
  }
}
