import { useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore.js'
import { buildSkillsHubViewModel, SKILL_SOURCE_FILTER_ALL } from './skillsHubModel.js'
import { useSkillsHubQuery } from './skillsHubQueries.js'

export function useSkillsHubController() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const [filter, setFilter] = useState('')
  const [sourceFilter, setSourceFilter] = useState(SKILL_SOURCE_FILTER_ALL)

  const query = useSkillsHubQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const view = useMemo(
    () => buildSkillsHubViewModel({
      data: query.hubData,
      filter,
      sourceFilter,
      hubError: query.hubError,
      loading: query.loading,
      refreshing: query.refreshing,
      stale: query.stale,
      lastUpdatedAt: query.lastUpdatedAt,
    }),
    [
      query.hubData,
      filter,
      sourceFilter,
      query.hubError,
      query.loading,
      query.refreshing,
      query.stale,
      query.lastUpdatedAt,
    ],
  )

  return {
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
