import { useEffect, useMemo, useState } from 'react'
import { connectTraceLiveFeed } from '../../traceApi.js'
import { useTraceLiveSnapshotQuery } from '../../traceQueries.js'
import {
  buildTraceLiveFeedConnectParams,
  buildTraceLiveSnapshotParams,
  getTracePayloadItems,
  pruneTraceTimelineEntriesForSnapshot,
} from './traceHomeModel.js'
import { mergeLiveTraceTimelineEntries } from './traceLiveSurfaceModel.js'

export function useTraceHomeLiveFeed({
  liveFeedEnabled,
  liveTraceLimit,
  pollingTick,
  pollingInterval,
  recentKeyword,
}) {
  const [liveTraceItems, setLiveTraceItems] = useState([])
  const [liveTraceTimelineEntries, setLiveTraceTimelineEntries] = useState({})
  const [liveFeedError, setLiveFeedError] = useState(null)
  const [liveFeedConnected, setLiveFeedConnected] = useState(false)
  const [liveFeedLastTs, setLiveFeedLastTs] = useState(null)

  const liveSnapshotParams = useMemo(() => (
    buildTraceLiveSnapshotParams({ liveTraceLimit, recentKeyword })
  ), [liveTraceLimit, recentKeyword])

  const liveSnapshotQuery = useTraceLiveSnapshotQuery({
    queryParams: liveSnapshotParams,
    enabled: true,
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })

  useEffect(() => {
    if (!liveFeedEnabled) {
      setLiveFeedConnected(false)
      return undefined
    }

    let closed = false
    let connection
    setLiveFeedError(null)

    try {
      connection = connectTraceLiveFeed(
        buildTraceLiveFeedConnectParams({ liveTraceLimit, recentKeyword }),
        {
          onOpen: () => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedError(null)
          },
          onSnapshot: (payload) => {
            if (closed) return
            const items = getTracePayloadItems(payload)
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            if (items.length > 0) {
              setLiveTraceItems(items)
            }
            setLiveTraceTimelineEntries((previous) => pruneTraceTimelineEntriesForSnapshot(previous, items))
            setLiveFeedError(null)
          },
          onDelta: (payload) => {
            if (closed) return
            const items = getTracePayloadItems(payload)
            if (items.length === 0) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            setLiveTraceTimelineEntries((previous) => mergeLiveTraceTimelineEntries(previous, items))
          },
          onHeartbeat: (payload) => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
          },
          onError: (payload) => {
            if (closed) return
            setLiveFeedError(payload?.detail || '实时链路流异常')
          },
          onConnectionError: () => {
            if (closed) return
            setLiveFeedConnected(false)
          },
        }
      )
    } catch (connectionError) {
      setLiveFeedConnected(false)
      setLiveFeedError(connectionError.message)
      return undefined
    }

    return () => {
      closed = true
      connection?.close?.()
    }
  }, [liveFeedEnabled, recentKeyword, liveTraceLimit])

  useEffect(() => {
    const items = getTracePayloadItems(liveSnapshotQuery.liveSnapshot)
    if (items.length === 0) {
      if (!liveFeedConnected) {
        setLiveTraceItems([])
      }
      return
    }
    if (liveFeedConnected && liveTraceItems.length > 0) return
    setLiveTraceItems(items)
    if (!liveFeedLastTs && liveSnapshotQuery.liveSnapshot?.timestamp) {
      setLiveFeedLastTs(liveSnapshotQuery.liveSnapshot.timestamp)
    }
  }, [liveFeedConnected, liveFeedLastTs, liveSnapshotQuery.liveSnapshot, liveTraceItems.length])

  useEffect(() => {
    setLiveTraceItems([])
    setLiveTraceTimelineEntries({})
  }, [recentKeyword])

  return {
    liveFeedConnected,
    liveFeedError,
    liveFeedLastTs,
    liveTraceError: liveFeedError || liveSnapshotQuery.liveSnapshotError,
    liveTraceItems,
    liveTraceLoading: liveSnapshotQuery.loading || liveSnapshotQuery.refreshing,
    liveTraceTimelineEntries,
    refreshLiveSnapshot: liveSnapshotQuery.refetch,
  }
}
