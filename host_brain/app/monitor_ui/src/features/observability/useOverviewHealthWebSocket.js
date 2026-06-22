import { useCallback, useEffect, useRef, useState } from 'react'

import { connectHealthWebSocket } from './overviewApi.js'

export function useOverviewHealthWebSocket({ pageVisible }) {
  const [liveHealthData, setLiveHealthData] = useState(null)
  const [liveHealthUpdatedAt, setLiveHealthUpdatedAt] = useState(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [wsAlerts, setWsAlerts] = useState([])
  const wsRef = useRef(null)
  const wsReconnectTimer = useRef(null)
  const wsReconnectDelay = useRef(1000)

  const scheduleReconnect = useCallback((connectWs) => {
    setWsConnected(false)
    if (!pageVisible) return
    const delay = wsReconnectDelay.current
    wsReconnectDelay.current = Math.min(delay * 2, 30000)
    wsReconnectTimer.current = setTimeout(connectWs, delay)
  }, [pageVisible])

  const connectWs = useCallback(() => {
    if (!pageVisible) {
      return
    }
    wsRef.current?.close()
    clearTimeout(wsReconnectTimer.current)

    try {
      const conn = connectHealthWebSocket(
        (msg) => {
          if (msg.type === 'health_snapshot' && msg.data) {
            setLiveHealthData(msg.data)
            setLiveHealthUpdatedAt(Date.now())
          } else if (msg.services) {
            setLiveHealthData(msg)
            setLiveHealthUpdatedAt(Date.now())
          }
          if (Array.isArray(msg.alerts) && msg.alerts.length > 0) {
            setWsAlerts((previous) => [...msg.alerts, ...previous].slice(0, 20))
          }
        },
        () => scheduleReconnect(connectWs),
        () => scheduleReconnect(connectWs),
      )
      wsRef.current = conn
      setWsConnected(true)
      wsReconnectDelay.current = 1000
    } catch (_) {
      setWsConnected(false)
    }
  }, [pageVisible, scheduleReconnect])

  useEffect(() => {
    if (!pageVisible) {
      wsRef.current?.close()
      clearTimeout(wsReconnectTimer.current)
      setWsConnected(false)
      return undefined
    }
    connectWs()
    return () => {
      wsRef.current?.close()
      clearTimeout(wsReconnectTimer.current)
    }
  }, [connectWs, pageVisible])

  return {
    liveHealthData,
    liveHealthUpdatedAt,
    wsAlerts,
    wsConnected,
  }
}
