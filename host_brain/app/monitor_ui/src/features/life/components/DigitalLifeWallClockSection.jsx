import React from 'react'
import { LifeSection } from './digitalLifeSectionShared.jsx'
import DigitalLifeWallClockSoakCard from './DigitalLifeWallClockSoakCard.jsx'

export default function DigitalLifeWallClockSection({ wallClockSoakQuery }) {
  return (
    <LifeSection
      title="真实墙钟闭环验证"
      hint="只读读取当前 24h wall-clock soak 记录，用来确认主链、器官映射与后台 tick 是否持续推进。"
    >
      <DigitalLifeWallClockSoakCard
        data={wallClockSoakQuery.data}
        loading={wallClockSoakQuery.loading}
        refreshing={wallClockSoakQuery.refreshing}
        error={wallClockSoakQuery.error}
        onRefresh={wallClockSoakQuery.refetch}
      />
    </LifeSection>
  )
}
