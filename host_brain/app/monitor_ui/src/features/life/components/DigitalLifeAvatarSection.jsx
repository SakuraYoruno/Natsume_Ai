import AvatarStateCard from './AvatarStateCard.jsx'
import { LifeSection } from './digitalLifeSectionShared.jsx'

export default function DigitalLifeAvatarSection({
  avatarStateQuery,
  avatarCommandBusy,
  avatarCommandError,
  onApplyAvatarBehavior,
}) {
  return (
    <LifeSection
      title="桌宠表现链路"
      hint="把 Host 对话、Edge 行为状态、MateEngine/VRM 驱动和危险热键拦截放在一张只读卡片里。"
    >
      <AvatarStateCard
        data={avatarStateQuery.avatarState}
        loading={avatarStateQuery.loading}
        refreshing={avatarStateQuery.refreshing}
        error={avatarStateQuery.error}
        commandError={avatarCommandError}
        behaviorBusy={avatarCommandBusy}
        updatedAt={avatarStateQuery.lastUpdatedAt}
        onRefresh={() => avatarStateQuery.refetch()}
        onBehavior={onApplyAvatarBehavior}
      />
    </LifeSection>
  )
}
