import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { SESSIONS_PAGE_SIZE } from '../../sessionsModel.js'
import FlowSection from './FlowSection.jsx'
import SessionCard from './SessionCard.jsx'
import SessionSkeleton from './SessionSkeleton.jsx'

export default function SessionsStreamSection({
  total,
  loading,
  sessionsData,
  listError,
  refreshing,
  sessions,
  sessionCards,
  onOpenSession,
}) {
  return (
    <FlowSection title="会话沉积流" hint={`共 ${total} 条，单页 ${SESSIONS_PAGE_SIZE} 条。`}>
      {loading && !sessionsData ? <SessionSkeleton /> : null}

      {!loading && !refreshing && !listError && sessions.length === 0 ? (
        <EmptyStateCard
          title="暂无会话沉积"
          hint="可以调整筛选条件，或等待新的对话请求进入系统。"
          className="p-6"
        />
      ) : null}

      {!loading && !listError && sessionCards.length > 0 ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {sessionCards.map(({ item, view }) => (
            <SessionCard key={item.request_id} item={item} view={view} onOpen={onOpenSession} />
          ))}
        </div>
      ) : null}
    </FlowSection>
  )
}
