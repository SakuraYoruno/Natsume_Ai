import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'
import { executeQuery, getQuerySnapshot, serializeQueryKey, subscribeQuery } from './queryCache.js'
import useAppStore from '../store/useAppStore.js'

function normalizeError(error) {
  if (!error) return null
  if (error instanceof Error) return error
  return new Error(String(error))
}

export default function useObservabilityQuery({
  queryKey,
  queryFn,
  enabled = true,
  staleTimeMs = 0,
  retryCount = 0,
  retryDelayMs = 1_200,
  pollingEnabled = false,
  pollingTick = 0,
  pollingIntervalMs = 0,
  minPollingIntervalMs = 10_000,
}) {
  const cacheKey = serializeQueryKey(queryKey)
  const queryFnRef = useRef(queryFn)
  const retryTimerRef = useRef(null)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const effectivePollingEnabled = Boolean(pollingEnabled && pageVisible)

  useEffect(() => {
    queryFnRef.current = queryFn
  }, [queryFn])

  useEffect(() => () => {
    if (retryTimerRef.current) {
      globalThis.clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  const subscribe = useCallback((listener) => subscribeQuery(cacheKey, listener), [cacheKey])
  const getSnapshot = useCallback(() => getQuerySnapshot(cacheKey), [cacheKey])
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  const runQuery = useCallback((force = false) => (
    executeQuery({
      cacheKey,
      queryFn: () => queryFnRef.current(),
      staleTimeMs,
      force,
    })
  ), [cacheKey, staleTimeMs])

  useEffect(() => {
    if (!enabled) return
    runQuery(false).catch(() => {})
  }, [enabled, runQuery])

  useEffect(() => {
    if (retryTimerRef.current) {
      globalThis.clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }

    if (!enabled || retryCount <= 0) {
      return undefined
    }
    if (snapshot.status !== 'error' || !snapshot.error) {
      return undefined
    }
    if ((snapshot.failureCount || 0) > retryCount) {
      return undefined
    }

    retryTimerRef.current = globalThis.setTimeout(() => {
      retryTimerRef.current = null
      runQuery(true).catch(() => {})
    }, Math.max(250, retryDelayMs))

    return () => {
      if (retryTimerRef.current) {
        globalThis.clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }
  }, [enabled, retryCount, retryDelayMs, runQuery, snapshot.error, snapshot.failureCount, snapshot.status])

  useEffect(() => {
    if (!enabled || !effectivePollingEnabled || pollingTick <= 0) {
      return undefined
    }
    runQuery(true).catch(() => {})
    return undefined
  }, [effectivePollingEnabled, enabled, pollingTick, runQuery])

  useEffect(() => {
    if (!enabled || !effectivePollingEnabled || !pollingIntervalMs || pollingTick > 0) {
      return undefined
    }
    const intervalMs = Math.max(minPollingIntervalMs, pollingIntervalMs)
    const timer = globalThis.setInterval(() => {
      runQuery(true).catch(() => {})
    }, intervalMs)
    return () => globalThis.clearInterval(timer)
  }, [effectivePollingEnabled, enabled, minPollingIntervalMs, pollingIntervalMs, pollingTick, runQuery])

  const isStale = Boolean(snapshot.updatedAt && staleTimeMs > 0 && (Date.now() - snapshot.updatedAt) > staleTimeMs)

  return {
    data: snapshot.data,
    error: normalizeError(snapshot.error),
    loading: snapshot.status === 'loading',
    refreshing: snapshot.status === 'refreshing',
    stale: isStale || snapshot.status === 'stale',
    status: snapshot.status,
    failureCount: snapshot.failureCount || 0,
    lastUpdatedAt: snapshot.updatedAt,
    refetch: useCallback(() => runQuery(true), [runQuery]),
  }
}
