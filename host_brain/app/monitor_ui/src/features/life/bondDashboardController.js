import { useCallback, useEffect, useState } from 'react'

import useAppStore from '../../store/useAppStore.js'
import { buildBondDashboardViewModel } from './bondModel.js'
import { useBondOverviewQuery, useBondUserDetailQuery } from './bondQueries.js'

export function useBondDashboardController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [selectedUser, setSelectedUser] = useState(null)

  const {
    health,
    users,
    stageDist,
    stageSampleCount,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useBondOverviewQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    userDetail,
    detailError,
    loading: detailLoading,
    refreshing: detailRefreshing,
    lastUpdatedAt: detailUpdatedAt,
    refetch: refetchUserDetail,
  } = useBondUserDetailQuery({
    userId: selectedUser,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    if (!selectedUser && users.length > 0) {
      setSelectedUser(users[0])
      return
    }
    if (selectedUser && !users.includes(selectedUser)) {
      setSelectedUser(users[0] || null)
    }
  }, [selectedUser, users])

  const dashboardView = buildBondDashboardViewModel({
    health,
    users,
    stageDist,
    stageSampleCount,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    selectedUser,
    userDetail,
    detailError,
    detailUpdatedAt,
  })

  const handleRefresh = useCallback(() => {
    return Promise.allSettled([
      refetch(),
      selectedUser ? refetchUserDetail() : Promise.resolve(),
    ])
  }, [refetch, refetchUserDetail, selectedUser])

  return {
    ...dashboardView,
    users,
    stageDist,
    selectedUser,
    userDetail,
    loading,
    refreshing,
    detailLoading,
    detailRefreshing,
    onRefresh: handleRefresh,
    onSelectUser: setSelectedUser,
  }
}
