import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import useDigitalLifeRuntimeSignals from '../../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../../store/useAppStore.js'
import {
  buildPromptFilterOptions,
  buildPromptTemplateListMetaState,
  buildPromptTemplateSampleMeta,
  buildPromptTemplateSummary,
  getNextPromptTemplateSelection,
  getPromptFiltersFromSearchParams,
} from './promptOpsModel.js'
import {
  usePromptTemplateDetailQuery,
  usePromptTemplateListQuery,
} from './promptQueries.js'

export function usePromptConfigController() {
  const [searchParams] = useSearchParams()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const [filters, setFilters] = useState(() => ({
    ...getPromptFiltersFromSearchParams(searchParams),
  }))
  const [selectedId, setSelectedId] = useState('')
  const [refreshingView, setRefreshingView] = useState(false)

  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const listQuery = usePromptTemplateListQuery({ filters })
  const detailQuery = usePromptTemplateDetailQuery({ templateId: selectedId })

  const scopes = useMemo(() => buildPromptFilterOptions(listQuery.items, 'scope'), [listQuery.items])
  const categories = useMemo(() => buildPromptFilterOptions(listQuery.items, 'category'), [listQuery.items])
  const summary = useMemo(() => buildPromptTemplateSummary(listQuery.items), [listQuery.items])

  useEffect(() => {
    const nextSelectedId = getNextPromptTemplateSelection(listQuery.items, selectedId)
    if (nextSelectedId !== selectedId) setSelectedId(nextSelectedId)
  }, [listQuery.items, selectedId])

  const listMetaState = useMemo(
    () => buildPromptTemplateListMetaState({
      templatesData: listQuery.templatesData,
      listError: listQuery.listError,
      listLoading: listQuery.loading,
      listRefreshing: listQuery.refreshing,
      listStale: listQuery.stale,
    }),
    [
      listQuery.templatesData,
      listQuery.listError,
      listQuery.loading,
      listQuery.refreshing,
      listQuery.stale,
    ],
  )

  const sampleMeta = useMemo(
    () => buildPromptTemplateSampleMeta({
      listMetaState,
      summary,
      listUpdatedAt: listQuery.lastUpdatedAt,
    }),
    [listMetaState, listQuery.lastUpdatedAt, summary],
  )

  const refreshView = useCallback(async () => {
    setRefreshingView(true)
    try {
      await Promise.allSettled([
        listQuery.refetch(),
        selectedId ? detailQuery.refetch() : Promise.resolve(null),
      ])
    } finally {
      setRefreshingView(false)
    }
  }, [detailQuery, listQuery, selectedId])

  const updateFilter = useCallback((key, value) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }, [])

  return {
    filters,
    selectedId,
    runtimeSnapshot,
    promptStats,
    items: listQuery.items,
    listError: listQuery.listError,
    listLoading: listQuery.loading,
    listRefreshing: listQuery.refreshing,
    scopes,
    categories,
    summary,
    sampleMeta,
    selected: detailQuery.item,
    detailLoading: detailQuery.loading,
    detailRefreshing: detailQuery.refreshing,
    detailError: detailQuery.detailError,
    detailUpdatedAt: detailQuery.lastUpdatedAt,
    detailStale: detailQuery.stale,
    refreshingView,
    onFilterChange: updateFilter,
    onSelectTemplate: setSelectedId,
    onRefreshList: listQuery.refetch,
    onRefreshView: refreshView,
  }
}
