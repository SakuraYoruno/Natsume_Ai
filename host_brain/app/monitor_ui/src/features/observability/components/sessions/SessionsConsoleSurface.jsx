import React from 'react'
import { Clock3, Download, ExternalLink, RefreshCw, Search } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import TimeWindowSelect from '../../../../shared/ui/TimeWindowSelect.jsx'
import {
  SESSIONS_PAGE_SIZE,
  SESSIONS_WINDOW_OPTIONS,
  getSessionsWindowLabel,
} from '../../sessionsModel.js'
import { DASH } from '../../../../utils/digitalLife.js'

function FlowSection({ title, hint, children }) {
  return (
    <section className="card relative overflow-hidden p-4">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_36%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_32%)]" />
      <div className="relative">
        <div className="mb-2 flex items-center gap-2">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        </div>
        {hint ? <p className="mb-3 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mb-4 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        {children}
      </div>
    </section>
  )
}

function SessionCard({ item, view, onOpen }) {
  return (
    <button
      className={`group w-full rounded-2xl border ${view.tone.border} ${view.tone.bg} p-4 text-left shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-md`}
      onClick={() => onOpen(item.request_id)}
    >
      <div className="flex items-start gap-3">
        <span className={`mt-1 h-3.5 w-3.5 shrink-0 rounded-full ${view.tone.ring} ring-4 ring-white/70`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-gray-900">{view.preview}</div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
                <span className="font-mono">{view.requestLabel}</span>
                <span>{view.sessionLabel}</span>
                <span>{view.timeLabel}</span>
              </div>
            </div>
            <StatusBadge status={view.normalizedStatus} label={view.statusLabel} />
          </div>

          <div className="mt-3 text-sm leading-6 text-gray-700">{view.preview}</div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className={`inline-flex items-center rounded-full border px-2.5 py-1 font-medium ${view.tone.badge}`}>
              神经链路回放
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              耗时 {view.latencyLabel}
            </span>
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-white/80 px-2.5 py-1 text-gray-600">
              {view.reasonLabel}
            </span>
          </div>
        </div>
        <ExternalLink size={14} className="mt-1 text-gray-300 transition-colors group-hover:text-gray-500" />
      </div>
    </button>
  )
}

function SessionSkeleton() {
  return (
    <div className="grid gap-3 xl:grid-cols-2" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-2xl border border-gray-200 bg-white/85 p-4">
          <div className="h-3 w-3/4 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-3 h-3 w-1/2 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-4 h-12 w-full animate-pulse rounded-xl bg-gray-100" />
          <div className="mt-3 flex gap-2">
            <div className="h-6 w-24 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-20 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-28 animate-pulse rounded-full bg-gray-100" />
          </div>
        </div>
      ))}
    </div>
  )
}

function SessionsHeader({
  windowMinutes,
  exporting,
  loading,
  refreshing,
  onWindowMinutesChange,
  onExport,
  onRefresh,
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">神经会话沉积</h1>
        <p className="text-sm text-gray-500">把最近请求看成生命回声流，先看节律，再进入单条链路。</p>
      </div>
      <div className="flex items-center gap-2">
        <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={SESSIONS_WINDOW_OPTIONS} />
        <button className="btn-secondary" onClick={onExport} disabled={exporting}>
          <Download size={14} className={exporting ? 'animate-pulse' : ''} />
          {exporting ? '导出中' : '导出 CSV'}
        </button>
        <button className="btn-secondary" onClick={onRefresh} disabled={loading || refreshing}>
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
          {loading || refreshing ? '刷新中' : '刷新'}
        </button>
      </div>
    </div>
  )
}

function SessionsNotices({ listError, suppressedCount, showNoSampleNotice }) {
  return (
    <>
      {listError ? (
        <NoticeBanner variant="error" title="会话流加载失败">
          {listError}
        </NoticeBanner>
      ) : null}

      {!listError && suppressedCount > 0 ? (
        <NoticeBanner variant="info" title="已隐藏噪声记录">
          当前页已自动隐藏 {suppressedCount} 条测试会话、纯问号空态消息或不可读摘要记录。
        </NoticeBanner>
      ) : null}

      {showNoSampleNotice ? (
        <NoticeBanner variant="info" title="当前窗口暂无会话沉积">
          当前筛选条件下还没有可展示的请求样本。
        </NoticeBanner>
      ) : null}
    </>
  )
}

function SessionsVitalsSection({ sessions, summary }) {
  return (
    <FlowSection title="体内会话体征" hint="将会话质量、异常密度和耗时放入同一体征层。">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="当前页样本" value={sessions.length} hint="用于快速浏览" tone="text-gray-900" />
        <MetricCard label="顺流" value={summary.healthyCount} hint="当前页" tone="text-green-600" />
        <MetricCard label="回撤/降级" value={summary.degradedCount} hint="当前页" tone="text-orange-500" />
        <MetricCard
          label="平均耗时"
          value={summary.avgLatency != null ? `${summary.avgLatency} ms` : DASH}
          hint="当前页样本"
          tone="text-blue-600"
        />
      </div>
    </FlowSection>
  )
}

function SessionsFilterSection({
  keyword,
  status,
  windowMinutes,
  onKeywordChange,
  onStatusChange,
  onSearch,
}) {
  return (
    <FlowSection title="会话筛选与检索" hint="先在体内筛查，再下钻到单条神经链路。">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_180px_160px_auto]">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-xl border border-gray-200 py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
            placeholder="搜索 request_id、session_id 或输入摘要"
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onSearch()
            }}
          />
        </div>

        <select
          className="rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary-100"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="rejected">拒绝</option>
          <option value="error">异常</option>
        </select>

        <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
          时间窗：{getSessionsWindowLabel(windowMinutes)}
        </div>

        <button className="btn-primary justify-center" onClick={onSearch}>
          搜索
        </button>
      </div>
    </FlowSection>
  )
}

function SessionsStreamSection({
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

function SessionsPagination({ currentPage, totalPages, onPreviousPage, onNextPage }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500">
      <span>当前页 {currentPage} / {totalPages}</span>
      <div className="flex items-center gap-2">
        <button
          className="btn-secondary px-3 py-1.5 text-xs"
          disabled={currentPage <= 1}
          onClick={onPreviousPage}
        >
          上一页
        </button>
        <span className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-600">
          {currentPage} / {totalPages}
        </span>
        <button
          className="btn-secondary px-3 py-1.5 text-xs"
          disabled={currentPage >= totalPages}
          onClick={onNextPage}
        >
          下一页
        </button>
      </div>
    </div>
  )
}

export default function SessionsConsoleSurface({
  windowMinutes,
  exporting,
  loading,
  refreshing,
  heroTitle,
  heroDescription,
  heroMetaItems,
  sampleMeta,
  pulseNodes,
  listError,
  suppressedCount,
  showNoSampleNotice,
  runtimeCard,
  sessions,
  sessionCards,
  summary,
  keyword,
  status,
  total,
  sessionsData,
  currentPage,
  totalPages,
  onWindowMinutesChange,
  onExport,
  onRefresh,
  onKeywordChange,
  onStatusChange,
  onSearch,
  onOpenSession,
  onPreviousPage,
  onNextPage,
}) {
  return (
    <div className="space-y-6">
      <SessionsHeader
        windowMinutes={windowMinutes}
        exporting={exporting}
        loading={loading}
        refreshing={refreshing}
        onWindowMinutesChange={onWindowMinutesChange}
        onExport={onExport}
        onRefresh={onRefresh}
      />

      <PageHero
        icon={Clock3}
        eyebrow="神经会话回声面板"
        title={heroTitle}
        description={heroDescription}
        gradientClass="from-sky-50 via-white to-indigo-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            windowMinutes={sampleMeta.windowMinutes}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      <LivingPulseStrip
        title="会话脉冲"
        subtitle="把本页请求聚成一条可读脉冲流，识别是平稳推进还是出现回撤与异常。"
        nodes={pulseNodes}
      />

      <SessionsNotices
        listError={listError}
        suppressedCount={suppressedCount}
        showNoSampleNotice={showNoSampleNotice}
      />

      {runtimeCard}

      <SessionsVitalsSection sessions={sessions} summary={summary} />

      <SessionsFilterSection
        keyword={keyword}
        status={status}
        windowMinutes={windowMinutes}
        onKeywordChange={onKeywordChange}
        onStatusChange={onStatusChange}
        onSearch={onSearch}
      />

      <SessionsStreamSection
        total={total}
        loading={loading}
        sessionsData={sessionsData}
        listError={listError}
        refreshing={refreshing}
        sessions={sessions}
        sessionCards={sessionCards}
        onOpenSession={onOpenSession}
      />

      <SessionsPagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPreviousPage={onPreviousPage}
        onNextPage={onNextPage}
      />
    </div>
  )
}
