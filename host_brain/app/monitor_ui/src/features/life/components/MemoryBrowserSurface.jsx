import React from 'react'
import { Archive, BookOpen, Brain, Database, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../shared/ui/PageHero.jsx'
import SectionHeading from '../../../shared/ui/SectionHeading.jsx'
import TimeWindowSelect from '../../../shared/ui/TimeWindowSelect.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import OrganSystemOverview from './lifeBody/OrganSystemOverview.jsx'
import {
  formatObservableCount,
  formatObservablePercent,
} from '../observabilityResourceState.js'

const MEMORY_RESOURCE_LABELS = {
  memoryData: '记忆子系统',
  episodicData: '情景写入',
  distillData: '长期蒸馏',
  canonDocs: 'Canon 文档',
  compressData: '上下文压缩',
  residentContext: '常驻记忆',
}

function ObservationMeta({ resourceState, source, measurementAvailable = true, unavailableLabel = '指标不可测' }) {
  if (!resourceState && !source) return null
  const resolvedSource = source || resourceState?.source || '未标注'
  const availability = resourceState
    ? resourceState.available
      ? measurementAvailable ? '可观测' : unavailableLabel
      : resourceState.availability === 'error' ? '请求失败' : '数据源暂不可用'
    : measurementAvailable ? '可观测' : unavailableLabel
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t pt-2 text-[11px] text-gray-400">
      <span>来源：<span className="font-mono">{resolvedSource}</span></span>
      <span>可用性：{availability}</span>
    </div>
  )
}

function ResidentDocsPanel({ worldDocs, personaDocs }) {
  return (
    <div className="card p-4">
      <SectionHeading icon={BookOpen} title="常驻设定文件" />
      {(worldDocs.length > 0 || personaDocs.length > 0) ? (
        <div className="space-y-3">
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">World Canon</div>
            {worldDocs.length > 0 ? (
              <div className="max-h-36 space-y-1 overflow-y-auto">
                {worldDocs.map((doc) => (
                  <div key={doc.doc_id} className="rounded bg-gray-50 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-gray-700">{doc.doc_id}</span>
                      <span className="shrink-0 text-gray-400">v{doc.version}</span>
                    </div>
                    {doc.preview ? <div className="mt-1 text-gray-500">{doc.preview}</div> : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-gray-400">暂无 World Canon 常驻文件。</div>
            )}
          </div>

          <div className="border-t pt-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Persona Profile</div>
            {personaDocs.length > 0 ? (
              <div className="max-h-28 space-y-1 overflow-y-auto">
                {personaDocs.map((doc) => (
                  <div key={doc.doc_id} className="rounded bg-gray-50 px-3 py-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-gray-700">{doc.doc_id}</span>
                      <span className="shrink-0 text-gray-400">v{doc.version}</span>
                    </div>
                    {doc.title ? <div className="mt-1 text-gray-600">{doc.title}</div> : null}
                    {doc.preview ? <div className="mt-1 text-gray-500">{doc.preview}</div> : null}
                    {doc.source_path ? <div className="mt-1 truncate text-[11px] text-gray-400">{doc.source_path}</div> : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-gray-400">暂无人设常驻文件。</div>
            )}
          </div>
        </div>
      ) : (
        <EmptyStateCard title="暂无常驻设定文件" hint="等待文档同步后会自动展示。" className="p-4" />
      )}
    </div>
  )
}

function CompressionPanel({ compressData, resourceState }) {
  return (
    <div className="card p-4">
      <SectionHeading icon={Archive} title="上下文压缩" />
      {compressData ? (
        <div className="grid grid-cols-3 gap-3 text-center text-xs">
          <div>
            <div className="text-lg font-bold text-blue-600">{formatObservableCount(compressData.observed_pre_save_hooks)}</div>
            <div className="text-gray-500">预存钩子观测数</div>
          </div>
          <div>
            <div className="text-lg font-bold text-green-600">{formatObservablePercent(compressData.avg_compression_ratio)}</div>
            <div className="text-gray-500">平均压缩比</div>
          </div>
          <div>
            <div className="text-lg font-bold text-purple-600">{formatObservableCount(compressData.total_tokens_saved)}</div>
            <div className="text-gray-500">节省词元</div>
          </div>
        </div>
      ) : (
        <EmptyStateCard title="暂无上下文压缩统计" hint="等待压缩链路产生样本后展示。" className="p-4" />
      )}
      <ObservationMeta
        resourceState={resourceState}
        source={compressData?.source_table}
        measurementAvailable={compressData?.outcome_measurement_available !== false}
        unavailableLabel="仅观测到预存钩子，压缩结果不可测"
      />
    </div>
  )
}

function DistillPanel({ distillData, resourceState }) {
  return (
    <div className="card p-4">
      <SectionHeading icon={Brain} title="长期蒸馏" />
      {distillData ? (
        <div className="grid grid-cols-2 gap-3 text-center text-xs sm:grid-cols-4">
          <div>
            <div className="text-lg font-bold text-indigo-600">{formatObservableCount(distillData.total_cycles)}</div>
            <div className="text-gray-500">蒸馏循环</div>
          </div>
          <div>
            <div className="text-lg font-bold text-green-600">{formatObservablePercent(distillData.success_rate)}</div>
            <div className="text-gray-500">成功率</div>
          </div>
          <div>
            <div className="text-lg font-bold text-gray-600">{formatObservableCount(distillData.observed_terminal_events)}</div>
            <div className="text-gray-500">终态审计事件</div>
          </div>
          <div>
            <div className="text-lg font-bold text-gray-600">{formatObservableCount(distillData.total_proposals_submitted)}</div>
            <div className="text-gray-500">提案数</div>
          </div>
        </div>
      ) : (
        <EmptyStateCard title="暂无蒸馏统计" hint="蒸馏任务运行后会出现统计。" className="p-4" />
      )}
      <ObservationMeta
        resourceState={resourceState}
        source={distillData?.source_table}
        measurementAvailable={
          distillData?.cycle_measurement_available !== false
          && distillData?.failure_measurement_available !== false
        }
        unavailableLabel="仅终态审计事件可观测，周期与失败率不可测"
      />
    </div>
  )
}

function EpisodicWritePanel({ episodicData, resourceState }) {
  return (
    <div className="card p-4 lg:col-span-2">
      <SectionHeading icon={Brain} title="情景记忆写入摘要" />
      {episodicData ? (
        <div className="grid grid-cols-2 gap-3 text-center text-xs sm:grid-cols-4">
          <div>
            <div className="text-lg font-bold text-blue-600">{formatObservableCount(episodicData.total_attempts)}</div>
            <div className="text-gray-500">写入尝试</div>
          </div>
          <div>
            <div className="text-lg font-bold text-green-600">{formatObservableCount(episodicData.written_count)}</div>
            <div className="text-gray-500">实际写入</div>
          </div>
          <div>
            <div className="text-lg font-bold text-purple-600">{formatObservablePercent(episodicData.write_rate)}</div>
            <div className="text-gray-500">写入率</div>
          </div>
          <div>
            <div className="text-lg font-bold text-orange-500">{formatObservableCount(episodicData.memory_guard_blocked_count)}</div>
            <div className="text-gray-500">守卫拦截</div>
          </div>
        </div>
      ) : (
        <EmptyStateCard title="暂无情景记忆写入统计" hint="待有写入后展示趋势。" className="p-4" />
      )}
      <ObservationMeta
        resourceState={resourceState}
        source={episodicData?.source_tables?.join(' + ')}
        measurementAvailable={episodicData?.extended_guard_measurement_available !== false}
        unavailableLabel="扩展 Guard 指标不可测"
      />
    </div>
  )
}

export default function MemoryBrowserSurface({
  compressData,
  distillData,
  episodicData,
  error,
  heroDescription,
  heroMetaItems,
  heroTitle,
  lastUpdatedAt,
  loading,
  memoryLoadState,
  memoryOrganModel,
  personaDocs,
  refreshing,
  resourceIssues = [],
  resourceStates = {},
  showNoSampleNotice,
  showUnavailableNotice,
  subsystemMetricCards,
  subsystemOperationCount,
  windowMinutes,
  windowOptions,
  worldDocs,
  onRefresh,
  onWindowMinutesChange,
}) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">海马体记忆库</h1>
          <p className="text-sm text-gray-500">情景记忆、蒸馏历史与主设定文档。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={onWindowMinutesChange} options={windowOptions} />
          <button className="btn-secondary" onClick={() => onRefresh?.()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Database}
        eyebrow="海马体记忆总览"
        title={heroTitle}
        description={heroDescription}
        gradientClass="from-primary-50 via-white to-cyan-50"
        metaItems={heroMetaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={memoryLoadState}
            windowMinutes={windowMinutes}
            sampleSize={subsystemOperationCount}
            sampleLabel="记忆观测事件"
            updatedAt={lastUpdatedAt}
            source="memory / recall / distill"
            definition="记忆页把子系统读写、蒸馏和常驻设定放在同一观察面；无样本与暂不可用会明确区分。"
          />
        )}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}

      {resourceIssues.map((issue) => (
        <NoticeBanner key={issue.key} variant={issue.availability === 'error' ? 'error' : 'warning'}>
          {MEMORY_RESOURCE_LABELS[issue.key] || issue.key}：{issue.message}
          {issue.source ? ` · 来源 ${issue.source}` : ''}
        </NoticeBanner>
      ))}

      {showUnavailableNotice ? (
        <NoticeBanner variant="warning">
          记忆观测接口当前未返回可用数据，页面会保留中性空态而不是把不可用误报成 0。
        </NoticeBanner>
      ) : null}

      {showNoSampleNotice ? (
        <NoticeBanner variant="info">
          当前还是空样本初始态。记忆链路尚未产生可观测数据时，卡片显示 0 或空列表属于预期。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={memoryOrganModel} />

      {subsystemMetricCards.length > 0 ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {subsystemMetricCards.map((card) => (
            <MetricCard
              key={card.key}
              label={card.label}
              value={card.value}
              hint={card.hint}
              tone={card.tone}
              variant="stacked"
            />
          ))}
        </div>
      ) : (
        <EmptyStateCard
          title="暂无子系统统计"
          hint="等第一批记忆读写发生后，这里会展示各子系统成功率与执行次数。"
          className="p-6"
        />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ResidentDocsPanel worldDocs={worldDocs} personaDocs={personaDocs} />
        <CompressionPanel compressData={compressData} resourceState={resourceStates.compressData} />
        <DistillPanel distillData={distillData} resourceState={resourceStates.distillData} />
        <EpisodicWritePanel episodicData={episodicData} resourceState={resourceStates.episodicData} />
      </div>
    </div>
  )
}
