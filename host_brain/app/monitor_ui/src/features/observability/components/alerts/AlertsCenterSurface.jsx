import React from 'react'
import {
  AlertTriangle,
  Bell,
  BellOff,
  RefreshCw,
  ShieldAlert,
  Siren,
  Wrench,
} from 'lucide-react'
import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import TimeWindowSelect from '../../../../shared/ui/TimeWindowSelect.jsx'
import AlertsListPanel from './AlertsListPanel.jsx'
import AlertsReasonFilterPanel from './AlertsReasonFilterPanel.jsx'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export default function AlertsCenterSurface({
  windowMinutes,
  onWindowMinutesChange,
  onRefresh,
  loading,
  refreshing,
  error,
  filterCode,
  onFilterCodeChange,
  runtimeSnapshot,
  promptStats,
  capabilityGapData,
  capabilityGapError,
  serviceAlerts,
  allAlerts,
  reasonBreakdown,
  filteredAlerts,
  criticalCount,
  warningCount,
  infoCount,
  isForgeRiskFiltered,
  immuneOrganModel,
  heroContent,
  heroMetaItems,
  sampleMeta,
  RuntimeCardComponent,
  OrganOverviewComponent,
}) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">免疫告警中心</h1>
          <p className="text-sm text-gray-500">统一收敛生命体运行时、器官异常和错误日志告警。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={onRefresh} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            {loading || refreshing ? '刷新中' : '刷新'}
          </button>
        </div>
      </div>

      <PageHero
        icon={Siren}
        eyebrow="数字生命体免疫告警总览"
        title={heroContent.title}
        description={heroContent.description}
        gradientClass="from-rose-50 via-white to-orange-50"
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

      {error ? (
        <NoticeBanner variant="error" title="告警数据加载失败">
          {error.message}
        </NoticeBanner>
      ) : null}

      {capabilityGapError ? (
        <NoticeBanner variant="warning" title="Capability Forge projection unavailable">
          {capabilityGapError}
        </NoticeBanner>
      ) : null}

      {isForgeRiskFiltered ? (
        <NoticeBanner variant="info" title="Forge Risk Drill-down">
          <span className="mr-2">{`filter=${filterCode}`}</span>
          <a href="#/observability/insights/global?focus=forge-risk" className="font-medium text-primary hover:underline">
            打开 Forge 全局洞察
          </a>
        </NoticeBanner>
      ) : null}

      {OrganOverviewComponent ? <OrganOverviewComponent model={immuneOrganModel} /> : null}

      {RuntimeCardComponent ? (
        <RuntimeCardComponent
          runtimeSnapshot={runtimeSnapshot}
          promptStats={promptStats}
          capabilityGapData={capabilityGapData}
          compact
        />
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <MetricCard icon={Bell} label="总告警" value={filteredAlerts.length} hint="当前过滤结果" tone="text-gray-900" />
        <MetricCard icon={ShieldAlert} label="严重" value={criticalCount} hint="优先处理" tone="text-red-600" />
        <MetricCard icon={AlertTriangle} label="警告" value={warningCount} hint="建议跟进" tone="text-orange-500" />
        <MetricCard icon={BellOff} label="提示" value={infoCount} hint="信息性事件" tone="text-blue-600" />
        <MetricCard icon={Wrench} label="器官异常" value={serviceAlerts.length} hint="来自 health probe" tone="text-purple-600" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <AlertsReasonFilterPanel
          reasonBreakdown={reasonBreakdown}
          filterCode={filterCode}
          allAlertsCount={allAlerts.length}
          onFilterCodeChange={onFilterCodeChange}
        />
        <AlertsListPanel filteredAlerts={filteredAlerts} />
      </div>
    </div>
  )
}
