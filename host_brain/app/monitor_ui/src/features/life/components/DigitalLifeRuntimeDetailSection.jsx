import React from 'react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import BackgroundRuntimeCard from './BackgroundRuntimeCard.jsx'
import DigitalLifeRuntimeCard from './DigitalLifeRuntimeCard.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'
import VitalLoopSummaryPanel from './VitalLoopSummaryPanel.jsx'

export default function DigitalLifeRuntimeDetailSection({
  runtimeSnapshot,
  runtimeLoading,
  promptStats,
  capabilityGapData,
}) {
  if (runtimeSnapshot) {
    return (
      <LifeSection
        title="生命体细部解剖"
        hint="上面先看主体、器官和病灶；下面继续看运行细部、稳态与节律剖面。"
      >
        <div className="space-y-4">
          <DigitalLifeRuntimeCard
            runtimeSnapshot={runtimeSnapshot}
            promptStats={promptStats}
            capabilityGapData={capabilityGapData}
          />
          <VitalLoopSummaryPanel runtimeSnapshot={runtimeSnapshot} showDetailLink />
          <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
        </div>
      </LifeSection>
    )
  }

  if (!runtimeLoading) {
    return (
      <EmptyStateCard
        title="暂时没有数字生命体快照"
        hint="请先产生一次完整对话或等待采样完成。"
      />
    )
  }

  return null
}
