import { useCallback } from 'react'
import {
  fetchCapabilityForgeProposals,
  fetchCanonDocuments,
  fetchCanonProposals,
  fetchLlmCallStats,
  fetchLlmRouteStats,
  fetchPersonaFirstBatchCatalog,
  fetchPromptTemplate,
  fetchPromptTemplates,
  fetchRateLimitBackendStats,
  fetchRetrievalRuntime,
  fetchServicesHealth,
  fetchVaultStats,
} from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function normalizePromptFilters(filters = {}) {
  return {
    keyword: String(filters.keyword || '').trim(),
    scope: String(filters.scope || '').trim(),
    category: String(filters.category || '').trim(),
    source: String(filters.source || '').trim(),
  }
}

export function usePromptTemplateListQuery({ filters = {} } = {}) {
  const normalizedFilters = normalizePromptFilters(filters)

  const queryFn = useCallback(async () => {
    try {
      const response = await fetchPromptTemplates({
        keyword: normalizedFilters.keyword || undefined,
        scope: normalizedFilters.scope || undefined,
        category: normalizedFilters.category || undefined,
        source: normalizedFilters.source || undefined,
      })
      return {
        data: response,
        listError: null,
      }
    } catch (error) {
      return {
        data: null,
        listError: error?.message || '模板列表加载失败',
      }
    }
  }, [normalizedFilters.category, normalizedFilters.keyword, normalizedFilters.scope, normalizedFilters.source])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.promptTemplateList(normalizedFilters),
    queryFn,
    staleTimeMs: OBS_STALE_TIMES.promptConfig,
  })

  return {
    ...query,
    templatesData: query.data?.data ?? null,
    items: query.data?.data?.items ?? [],
    listError: query.data?.listError ?? null,
  }
}

export function usePromptTemplateDetailQuery({ templateId } = {}) {
  const normalizedTemplateId = String(templateId || '').trim()

  const queryFn = useCallback(async () => {
    try {
      const response = await fetchPromptTemplate(normalizedTemplateId)
      return {
        data: response,
        detailError: null,
      }
    } catch (error) {
      return {
        data: null,
        detailError: error?.message || '模板详情加载失败',
      }
    }
  }, [normalizedTemplateId])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.promptTemplateDetail(normalizedTemplateId),
    queryFn,
    enabled: Boolean(normalizedTemplateId),
    staleTimeMs: OBS_STALE_TIMES.promptDetail,
  })

  return {
    ...query,
    templateData: query.data?.data ?? null,
    item: query.data?.data?.item ?? null,
    detailError: query.data?.detailError ?? null,
  }
}

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

export function useSystemConfigQuery({
  windowMinutes = 60,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    const [route, call, rate, health, retrieval] = await Promise.all([
      fetchLlmRouteStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchLlmCallStats({ window_minutes: windowMinutes }).catch(() => null),
      fetchRateLimitBackendStats({}).catch(() => null),
      fetchServicesHealth({ window_minutes: 60 }).catch(() => null),
      fetchRetrievalRuntime({ limit: 5 }).catch(() => null),
    ])

    return {
      routeData: route,
      callData: call,
      rateData: rate,
      healthData: health,
      retrievalRuntime: retrieval,
    }
  }, [windowMinutes])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.systemConfig(windowMinutes),
    queryFn,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.systemConfig,
  })

  return {
    ...query,
    routeData: query.data?.routeData ?? null,
    callData: query.data?.callData ?? null,
    rateData: query.data?.rateData ?? null,
    healthData: query.data?.healthData ?? null,
    retrievalRuntime: query.data?.retrievalRuntime ?? null,
  }
}

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

export function useCapabilityForgeProposalsQuery({
  limit = 20,
  enabled = true,
  pollingEnabled = false,
  pollingTick = 0,
  pollingInterval = 30,
} = {}) {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchCapabilityForgeProposals({ limit })
      return {
        data: response,
        proposalsError: null,
      }
    } catch (error) {
      return {
        data: null,
        proposalsError: error?.message || 'Forge proposal 列表加载失败',
      }
    }
  }, [limit])

  const query = useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.capabilityForgeProposals(limit),
    queryFn,
    enabled,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: pollingInterval * 1000,
    minPollingIntervalMs: 15_000,
    staleTimeMs: OBS_STALE_TIMES.forge,
  })

  return {
    ...query,
    proposalsData: query.data?.data ?? null,
    proposals: query.data?.data?.items ?? [],
    proposalsError: query.data?.proposalsError ?? null,
  }
}
