import React from 'react'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import { useChatDebugController } from '../features/observability/chatDebugController.js'
import {
  ChatDebugMainColumn,
  ChatTraceSidebar,
} from '../features/observability/components/chatDebug/ChatDebugConsoleSections.jsx'

export default function ChatDebug() {
  const chatDebug = useChatDebugController()

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-4">
      <ChatDebugMainColumn
        stats={chatDebug.stats}
        streaming={chatDebug.streaming}
        heroMetaItems={chatDebug.heroMetaItems}
        runtimeCard={(
          <DigitalLifeRuntimeCard
            runtimeSnapshot={chatDebug.runtimeSnapshot}
            promptStats={chatDebug.promptStats}
            compact
          />
        )}
        error={chatDebug.error}
        chatHistory={chatDebug.chatHistory}
        currentResponse={chatDebug.currentResponse}
        chatEndRef={chatDebug.chatEndRef}
        message={chatDebug.message}
        setMessage={chatDebug.setMessage}
        onSend={chatDebug.sendMessage}
        onStop={chatDebug.stopStream}
      />

      <ChatTraceSidebar
        requestId={chatDebug.requestId}
        traceBusy={chatDebug.traceBusy}
        traceLoadState={chatDebug.traceLoadState}
        traceError={chatDebug.traceError}
        traceUpdatedAt={chatDebug.traceUpdatedAt}
        chainEvents={chatDebug.chainEvents}
        traceSnapshot={chatDebug.traceSnapshot}
        traceNodeCount={chatDebug.traceNodeCount}
        sseEvents={chatDebug.sseEvents}
        traceIssueCount={chatDebug.traceIssueCount}
        traceLatestNode={chatDebug.traceLatestNode}
        latestEvent={chatDebug.latestEvent}
        traceInputPreview={chatDebug.traceInputPreview}
        traceOutputPreview={chatDebug.traceOutputPreview}
        eventsEndRef={chatDebug.eventsEndRef}
        onRefreshTrace={() => void chatDebug.refetchTraceSnapshot()}
      />
    </div>
  )
}
