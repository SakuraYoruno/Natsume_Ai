import { useCallback } from 'react'
import { fetchPromptTemplates } from './api.js'
import { OBS_STALE_TIMES } from '../../shared/query/freshness.js'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'

function buildPersonaPromptCatalog(response) {
  const items = Array.isArray(response?.items)
    ? response.items.filter((item) => (
      String(item?.scope || '').trim().toLowerCase() === 'persona'
      || String(item?.category || '').trim().toLowerCase() === 'persona_prompt'
    ))
    : []
  const entries = items.map((item) => ({
    template_id: item.template_id,
    title: item.purpose || item.template_id,
    description: item.description || item.purpose || '',
    focus_keys: [item.scope, item.category].filter(Boolean),
    operator_notes: item.source_path ? [`source: ${item.source_path}`] : [],
    item,
  }))
  const bundles = entries.length > 0
    ? [{
      bundle_id: 'persona-prompt-specs',
      title: '人格 PromptSpec',
      description: '来自现行 PromptSpec 目录的只读人格提示规范。',
      template_count: entries.length,
      entries,
    }]
    : []
  return {
    ...response,
    bundles,
    items,
    total: items.length,
    source: 'prompt-config',
  }
}

export function usePersonaPromptCatalogQuery() {
  const queryFn = useCallback(async () => {
    try {
      const response = await fetchPromptTemplates({
        scope: 'persona',
        category: 'persona_prompt',
      })
      return {
        data: buildPersonaPromptCatalog(response),
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
