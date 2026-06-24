import React, { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { exportSessionsCsv } from '../features/observability/sessionsApi.js'
import SessionsConsoleSurface from '../features/observability/components/sessions/SessionsConsoleSurface.jsx'
import DigitalLifeRuntimeCard from '../features/life/components/DigitalLifeRuntimeCard.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { useSessionsListQuery } from '../features/observability/sessionsQueries.js'
import {
  SESSIONS_PAGE_SIZE,
  buildSessionCardView,
  buildSessionTracePath,
  buildSessionsExportParams,
  buildSessionsQueryView,
  buildSessionsTriageRailModel,
  getNextSessionsPage,
  getPreviousSessionsPage,
} from '../features/observability/sessionsModel.js'
import useAppStore from '../store/useAppStore.js'

export default function Sessions() {
  const navigate = useNavigate()
  const setSelectedRequestId = useAppStore((state) => state.setSelectedRequestId)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [page, setPage] = useState(1)
  const [exporting, setExporting] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [windowMinutes, setWindowMinutes] = useState(1440)

  const { runtimeSnapshot, promptStats } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes,
  })

  const {
    sessionsData,
    listError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useSessionsListQuery({
    page,
    pageSize: SESSIONS_PAGE_SIZE,
    keyword,
    status,
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    sessions,
    suppressedCount,
    total,
    currentPage,
    totalPages,
    summary,
    pulseNodes,
    heroTitle,
    heroDescription,
    heroMetaItems,
    showNoSampleNotice,
    sampleMeta,
  } = useMemo(() => buildSessionsQueryView({
    sessionsData,
    listError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    page,
    pageSize: SESSIONS_PAGE_SIZE,
    keyword,
    status,
    windowMinutes,
  }), [
    keyword,
    lastUpdatedAt,
    listError,
    loading,
    page,
    refreshing,
    sessionsData,
    stale,
    status,
    windowMinutes,
  ])
  const triageRail = useMemo(() => buildSessionsTriageRailModel({
    sessions,
    summary,
    total,
    keyword,
    status,
    windowMinutes,
    loading,
    refreshing,
    listError,
  }), [
    keyword,
    listError,
    loading,
    refreshing,
    sessions,
    status,
    summary,
    total,
    windowMinutes,
  ])

  const handleSearch = useCallback(() => {
    if (page !== 1) {
      setPage(1)
      return
    }
    void refetch()
  }, [page, refetch])

  const handleExport = useCallback(async () => {
    setExporting(true)
    try {
      await exportSessionsCsv(buildSessionsExportParams({ windowMinutes, keyword, status }))
      toast.success('CSV 导出已开始')
    } catch (exportError) {
      toast.error(`导出失败：${exportError.message}`)
    } finally {
      setExporting(false)
    }
  }, [keyword, status, windowMinutes])

  const handleStatusChange = useCallback((nextStatus) => {
    setStatus(nextStatus)
    setPage(1)
  }, [])

  const handleClearFilters = useCallback(() => {
    setKeyword('')
    setStatus('')
    setPage(1)
  }, [])

  const sampleMetaView = useMemo(() => ({
    loadState: sampleMeta.loadState,
    windowMinutes: sampleMeta.windowMinutes,
    sampleSize: sampleMeta.sampleSize,
    sampleLabel: sampleMeta.sampleLabel,
    updatedAt: sampleMeta.updatedAt,
    source: sampleMeta.source,
    definition: sampleMeta.definition,
  }), [sampleMeta])

  const sessionCards = useMemo(
    () => sessions.map((item) => ({ item, view: buildSessionCardView(item) })),
    [sessions]
  )

  const openSessionTrace = useCallback((requestId) => {
    if (!requestId) return
    setSelectedRequestId(requestId)
    navigate(buildSessionTracePath(requestId))
  }, [navigate, setSelectedRequestId])

  return (
    <SessionsConsoleSurface
      windowMinutes={windowMinutes}
      exporting={exporting}
      loading={loading}
      refreshing={refreshing}
      heroTitle={heroTitle}
      heroDescription={heroDescription}
      heroMetaItems={heroMetaItems}
      sampleMeta={sampleMetaView}
      pulseNodes={pulseNodes}
      listError={listError}
      suppressedCount={suppressedCount}
      showNoSampleNotice={showNoSampleNotice}
      runtimeCard={<DigitalLifeRuntimeCard runtimeSnapshot={runtimeSnapshot} promptStats={promptStats} compact />}
      triageRail={triageRail}
      sessions={sessions}
      sessionCards={sessionCards}
      summary={summary}
      keyword={keyword}
      status={status}
      total={total}
      sessionsData={sessionsData}
      currentPage={currentPage}
      totalPages={totalPages}
      onWindowMinutesChange={setWindowMinutes}
      onExport={handleExport}
      onRefresh={refetch}
      onKeywordChange={setKeyword}
      onStatusChange={handleStatusChange}
      onClearFilters={handleClearFilters}
      onSearch={handleSearch}
      onOpenSession={openSessionTrace}
      onPreviousPage={() => setPage(getPreviousSessionsPage(currentPage))}
      onNextPage={() => setPage(getNextSessionsPage(currentPage, totalPages))}
    />
  )
}
