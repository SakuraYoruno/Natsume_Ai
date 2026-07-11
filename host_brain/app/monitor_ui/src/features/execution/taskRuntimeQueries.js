import { useCallback } from 'react'
import { OBS_QUERY_KEYS } from '../../shared/query/queryKeys.js'
import useObservabilityQuery from '../../shared/query/useObservabilityQuery.js'
import {
  fetchTaskRuntimeExecution,
  fetchTaskRuntimeExecutions,
} from './taskRuntimeApi.js'

const STALE_TIME_MS = 5_000

export function useTaskRuntimeIndexQuery({ pollingEnabled, pollingTick, pollingInterval } = {}) {
  const queryFn = useCallback(() => fetchTaskRuntimeExecutions({ limit: 100 }), [])
  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.taskRuntimeIndex(),
    queryFn,
    staleTimeMs: STALE_TIME_MS,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(15, Number(pollingInterval || 30)) * 1000,
    minPollingIntervalMs: 15_000,
  })
}

export function useTaskRuntimeExecutionQuery({
  graphId,
  revision,
  afterCursor = 0,
  pollingEnabled,
  pollingTick,
  pollingInterval,
} = {}) {
  const queryFn = useCallback(() => fetchTaskRuntimeExecution(graphId, {
    revision,
    after_cursor: afterCursor,
    event_limit: 200,
  }), [afterCursor, graphId, revision])
  return useObservabilityQuery({
    queryKey: OBS_QUERY_KEYS.taskRuntimeExecution(graphId, revision, afterCursor),
    queryFn,
    enabled: Boolean(graphId && revision),
    staleTimeMs: STALE_TIME_MS,
    pollingEnabled,
    pollingTick,
    pollingIntervalMs: Math.max(15, Number(pollingInterval || 30)) * 1000,
    minPollingIntervalMs: 15_000,
  })
}
