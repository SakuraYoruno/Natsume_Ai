import { useMemo } from 'react'

import useAppStore from '../../store/useAppStore.js'
import { useRuntimeSignalsQuery } from './digitalLifeQueries.js'
import { buildBondDashboardViewModel } from './bondModel.js'

function buildCanonicalBondData(runtimeSnapshot) {
  const bondState = runtimeSnapshot?.bond_state || {}
  const userId = String(
    bondState.user_id || runtimeSnapshot?.persona_user_id || '',
  ).trim() || null
  const currentStage = String(bondState.current_stage || '').trim() || null
  const users = userId ? [userId] : []
  const userDetail = userId
    ? {
        ...bondState,
        user_id: userId,
        current_stage: currentStage || 'FRIEND',
      }
    : null
  const stageDist = currentStage ? [{ stage: currentStage, count: 1 }] : []
  const slotStatus = String(runtimeSnapshot?.slot_status?.bond_state || '').trim()
  const health = runtimeSnapshot
    ? {
        status: runtimeSnapshot.degraded || slotStatus === 'degraded' ? 'DEGRADED' : 'OK',
        user_count: users.length,
      }
    : null

  return { health, users, stageDist, userId, userDetail }
}

export function useBondDashboardController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const runtimeQuery = useRuntimeSignalsQuery({
    windowMinutes: 60,
    pollingEnabled,
    pollingTick,
    pollingInterval,
    minIntervalSeconds: 15,
  })
  const canonical = useMemo(
    () => buildCanonicalBondData(runtimeQuery.runtimeSnapshot),
    [runtimeQuery.runtimeSnapshot],
  )
  const stageSampleCount = canonical.stageDist.length
  const runtimeError = runtimeQuery.error?.message || runtimeQuery.error || null
  const dashboardView = useMemo(
    () => buildBondDashboardViewModel({
      health: canonical.health,
      users: canonical.users,
      stageDist: canonical.stageDist,
      stageSampleCount,
      overviewError: runtimeError,
      loading: runtimeQuery.loading,
      refreshing: runtimeQuery.refreshing,
      stale: runtimeQuery.stale,
      lastUpdatedAt: runtimeQuery.lastUpdatedAt,
      selectedUser: canonical.userId,
      userDetail: canonical.userDetail,
      detailUpdatedAt: runtimeQuery.lastUpdatedAt,
    }),
    [
      canonical,
      runtimeQuery.lastUpdatedAt,
      runtimeQuery.loading,
      runtimeQuery.refreshing,
      runtimeQuery.stale,
      runtimeError,
      stageSampleCount,
    ],
  )

  return {
    ...dashboardView,
    users: canonical.users,
    stageDist: canonical.stageDist,
    selectedUser: canonical.userId,
    userDetail: canonical.userDetail,
    loading: runtimeQuery.loading,
    refreshing: runtimeQuery.refreshing,
    detailLoading: false,
    detailRefreshing: false,
    onRefresh: runtimeQuery.refetch,
    onSelectUser: () => {},
  }
}
