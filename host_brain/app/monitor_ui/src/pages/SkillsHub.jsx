import React, { useMemo } from 'react'
import SkillsHubSurface from '../features/execution/components/SkillsHubSurface.jsx'
import { useSkillsHubController } from '../features/execution/skillsHubController.js'
import CapabilityGovernanceSection from '../features/governance/components/CapabilityGovernanceSection.jsx'
import { buildGovernanceViewModel } from '../features/governance/governanceModel.js'
import { useGovernanceSnapshotQuery } from '../features/governance/governanceQueries.js'

export default function SkillsHub() {
  const skillsHub = useSkillsHubController()
  const governanceQuery = useGovernanceSnapshotQuery({
    pollingEnabled: skillsHub.pollingEnabled,
    pollingInterval: skillsHub.pollingInterval,
    pollingTick: skillsHub.pollingTick,
  })
  const governance = useMemo(
    () => buildGovernanceViewModel(governanceQuery.data),
    [governanceQuery.data],
  )

  return (
    <SkillsHubSurface
      {...skillsHub}
      CapabilityGovernanceSection={CapabilityGovernanceSection}
      governance={governance}
      governanceError={governanceQuery.error}
      onRefresh={() => Promise.allSettled([skillsHub.onRefresh(), governanceQuery.refetch()])}
    />
  )
}
