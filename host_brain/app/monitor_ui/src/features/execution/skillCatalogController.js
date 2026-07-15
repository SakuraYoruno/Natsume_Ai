import { useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore.js'
import { buildSkillCatalogViewModel, SKILL_SOURCE_FILTER_ALL } from './skillCatalogModel.js'
import { useSkillCatalogQuery } from './skillCatalogQueries.js'

export function useSkillCatalogController() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const [filter, setFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState(SKILL_SOURCE_FILTER_ALL)

  const query = useSkillCatalogQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const view = useMemo(
    () => buildSkillCatalogViewModel({
      data: query.catalogData,
      filter,
      sourceFilter,
      catalogError: query.catalogError,
      loading: query.loading,
      refreshing: query.refreshing,
      stale: query.stale,
      lastUpdatedAt: query.lastUpdatedAt,
    }),
    [
      query.catalogData,
      filter,
      sourceFilter,
      query.catalogError,
      query.loading,
      query.refreshing,
      query.stale,
      query.lastUpdatedAt,
    ],
  )

  return {
    pollingEnabled,
    pollingInterval,
    pollingTick,
    filter,
    sourceFilter,
    loading: query.loading,
    refreshing: query.refreshing,
    view,
    onFilterChange: setFilter,
    onSourceFilterChange: setSourceFilter,
    onRefresh: query.refetch,
  }
}
