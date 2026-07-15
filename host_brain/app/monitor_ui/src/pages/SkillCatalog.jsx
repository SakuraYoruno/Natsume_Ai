import React, { useMemo } from 'react'
import SkillCatalogSurface from '../features/execution/components/SkillCatalogSurface.jsx'
import { useSkillCatalogController } from '../features/execution/skillCatalogController.js'
import CapabilityGovernanceSection from '../features/governance/components/CapabilityGovernanceSection.jsx'
import { buildGovernanceViewModel } from '../features/governance/governanceModel.js'
import { useGovernanceSnapshotQuery } from '../features/governance/governanceQueries.js'

export default function SkillCatalog() {
  const skillCatalog = useSkillCatalogController()
  const governanceQuery = useGovernanceSnapshotQuery({
    pollingEnabled: skillCatalog.pollingEnabled,
    pollingInterval: skillCatalog.pollingInterval,
    pollingTick: skillCatalog.pollingTick,
  })
  const governance = useMemo(
    () => buildGovernanceViewModel(governanceQuery.data),
    [governanceQuery.data],
  )

  return (
    <SkillCatalogSurface
      {...skillCatalog}
      CapabilityGovernanceSection={CapabilityGovernanceSection}
      governance={governance}
      governanceError={governanceQuery.error}
      onRefresh={() => Promise.allSettled([skillCatalog.onRefresh(), governanceQuery.refetch()])}
    />
  )
}
