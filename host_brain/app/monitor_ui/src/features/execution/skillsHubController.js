import { useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore.js'
import { buildSkillsHubViewModel, SKILL_SOURCE_FILTER_ALL } from './skillsHubModel.js'
import { useSkillsHubQuery } from './skillsHubQueries.js'
import { useGovernanceSnapshotQuery } from '../governance/governanceQueries.js'
import { buildGovernanceViewModel } from '../governance/governanceModel.js'

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
  const governanceQuery = useGovernanceSnapshotQuery({ pollingEnabled, pollingTick, pollingInterval })
  const governance = useMemo(
    () => buildGovernanceViewModel(governanceQuery.data),
    [governanceQuery.data],
  )

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
    governance,
    governanceError: governanceQuery.error,
    onFilterChange: setFilter,
    onSourceFilterChange: setSourceFilter,
    onRefresh: () => Promise.allSettled([query.refetch(), governanceQuery.refetch()]),
  }
}
