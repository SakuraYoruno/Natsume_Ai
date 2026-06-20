import { useCallback } from 'react'
import {
  fetchPromptTemplate,
  fetchPromptTemplates,
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
