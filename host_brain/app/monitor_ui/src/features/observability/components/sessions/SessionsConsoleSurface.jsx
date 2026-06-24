import { Clock3 } from 'lucide-react'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import SessionsFilterSection from './SessionsFilterSection.jsx'
import SessionsHeader from './SessionsHeader.jsx'
import SessionsNotices from './SessionsNotices.jsx'
import SessionsPagination from './SessionsPagination.jsx'
import SessionsStreamSection from './SessionsStreamSection.jsx'
import SessionsTriageRail from './SessionsTriageRail.jsx'
import SessionsVitalsSection from './SessionsVitalsSection.jsx'

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
  triageRail,
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
  onClearFilters,
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

      <SessionsTriageRail
        model={triageRail}
        onOpenSession={onOpenSession}
        onSetStatus={onStatusChange}
        onClearFilters={onClearFilters}
        onRefresh={onRefresh}
      />

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
