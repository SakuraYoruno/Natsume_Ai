import CustomSkillProviderInstaller from './CustomSkillProviderInstaller.jsx'
import {
  ActionPlanStats,
  PermissionGuardStats,
} from './ToolsOperationsStatsPanels.jsx'
import {
  useActionPlanStatsQuery,
  usePermissionStatsQuery,
} from '../toolsQueries.js'

export default function ToolsOperationsSection({
  pollingEnabled,
  pollingInterval,
  pollingTick,
  permissionWindowMinutes = 1440,
  actionPlanWindowMinutes = 1440,
}) {
  const {
    permissionStats,
    statsError: permissionError,
    loading: permissionLoading,
    refreshing: permissionRefreshing,
    stale: permissionStale,
    lastUpdatedAt: permissionUpdatedAt,
  } = usePermissionStatsQuery({
    windowMinutes: permissionWindowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })
  const {
    actionPlanStats,
    statsError: actionPlanError,
    loading: actionPlanLoading,
    refreshing: actionPlanRefreshing,
    stale: actionPlanStale,
    lastUpdatedAt: actionPlanUpdatedAt,
  } = useActionPlanStatsQuery({
    windowMinutes: actionPlanWindowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  return (
    <div className="space-y-4">
      <CustomSkillProviderInstaller />
      <PermissionGuardStats
        data={permissionStats}
        loading={permissionLoading}
        refreshing={permissionRefreshing}
        stale={permissionStale}
        error={permissionError}
        updatedAt={permissionUpdatedAt}
        windowMinutes={permissionWindowMinutes}
      />
      <ActionPlanStats
        data={actionPlanStats}
        loading={actionPlanLoading}
        refreshing={actionPlanRefreshing}
        stale={actionPlanStale}
        error={actionPlanError}
        updatedAt={actionPlanUpdatedAt}
        windowMinutes={actionPlanWindowMinutes}
      />
    </div>
  )
}
