import { useCallback, useEffect, useState } from 'react'
import { fetchDigitalLifeRuntimeSnapshot, fetchPromptBuilderStats } from '../api/obs.js'

export default function useDigitalLifeRuntimeSignals(options = {}) {
  const {
    windowMinutes = 60,
    pollingEnabled = false,
    pollingInterval = 15,
    minIntervalSeconds = 15,
  } = options

  const [runtimeSnapshot, setRuntimeSnapshot] = useState(null)
  const [promptStats, setPromptStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async (cancelToken = { cancelled: false }) => {
    setLoading(true)
    setError(null)
    try {
      const [runtimeRes, promptRes] = await Promise.allSettled([
        fetchDigitalLifeRuntimeSnapshot(),
        fetchPromptBuilderStats({ window_minutes: windowMinutes }),
      ])
      if (cancelToken.cancelled) return

      if (runtimeRes.status === 'fulfilled') {
        setRuntimeSnapshot(runtimeRes.value)
      } else {
        setError(runtimeRes.reason?.message || 'runtime snapshot load failed')
      }
      if (promptRes.status === 'fulfilled') {
        setPromptStats(promptRes.value)
      }
    } finally {
      if (!cancelToken.cancelled) {
        setLoading(false)
      }
    }
  }, [windowMinutes])

  useEffect(() => {
    const cancelToken = { cancelled: false }

    load(cancelToken).catch(() => {})
    if (!pollingEnabled) {
      return () => {
        cancelToken.cancelled = true
      }
    }

    const timer = setInterval(() => {
      load(cancelToken).catch(() => {})
    }, Math.max(pollingInterval, minIntervalSeconds) * 1000)

    return () => {
      cancelToken.cancelled = true
      clearInterval(timer)
    }
  }, [load, pollingEnabled, pollingInterval, minIntervalSeconds])

  const reload = useCallback(async () => {
    await load({ cancelled: false })
  }, [load])

  return { runtimeSnapshot, promptStats, loading, error, reload }
}
