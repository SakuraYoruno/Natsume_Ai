import React, { useMemo, useState } from 'react'
import { Archive, BookOpen, Brain, Database, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import OrganSystemOverview from '../components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import SectionHeading from '../components/SectionHeading.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import { LOAD_STATES, resolveLoadState } from '../models/loadState.js'
import { buildMemoryOrganSystemModel } from '../models/organSystemModel.js'
import { useMemoryOverviewQuery } from '../queries/memoryQueries.js'
import useAppStore from '../store/useAppStore.js'
import { getSuccessRateTone, summarizeSuccessRateSamples } from '../utils/successRate.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

function formatDocStatus(status) {
  if (status === 'ACTIVE') return '生效中'
  return status || '—'
}

export default function MemoryBrowser() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [windowMinutes, setWindowMinutes] = useState(360)
  const {
    memoryData,
    episodicData,
    distillData,
    canonDocs,
    recallData,
    compressData,
    residentContext,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    refetch,
  } = useMemoryOverviewQuery({
    windowMinutes,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const subsystems = memoryData?.subsystems || []
  const docs = canonDocs?.documents || canonDocs?.items || []
  const slots = recallData?.slots || []
  const worldDocs = residentContext?.world_canon_documents || docs
  const personaDocs = residentContext?.persona_resident_documents || []
  const styleDocs = residentContext?.style_canon_documents || []
  const activeStyleBindings = residentContext?.active_style_bindings || []
  const latestStylePackRows = residentContext?.latest_style_pack_rows || []
  const latestMemoryPersonaEvidence = residentContext?.latest_memory_persona_evidence || []
  const latestMemoryRecallSummary = residentContext?.latest_memory_recall_summary || ''
  const promptContextAvailable = Boolean(residentContext?.prompt_context_available)
  const residentDocCount = worldDocs.length + personaDocs.length + styleDocs.length
  const hasMemoryObservabilityData = Boolean(
    memoryData || episodicData || distillData || canonDocs || recallData || compressData || residentContext
  )
  const subsystemOperationCount = subsystems.reduce((sum, item) => sum + Number(item?.total || 0), 0)
  const {
    averageRate: avgSuccessRate,
    sampledCount: sampledSubsystemCount,
    configuredCount: configuredSubsystemCount,
  } = summarizeSuccessRateSamples(subsystems)
  const hasSubsystemSamples = avgSuccessRate != null

  const hasMemoryTraffic = (
    sampledSubsystemCount > 0
    || worldDocs.length > 0
    || personaDocs.length > 0
    || styleDocs.length > 0
    || slots.length > 0
    || activeStyleBindings.length > 0
    || latestMemoryPersonaEvidence.length > 0
    || (episodicData?.total_writes ?? 0) > 0
    || (distillData?.total_cycles ?? 0) > 0
    || (compressData?.total_compressions ?? 0) > 0
  )
  const memoryLoadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: hasMemoryObservabilityData,
    hasSamples: hasMemoryTraffic,
    available: hasMemoryObservabilityData,
  })
  const memoryOrganModel = useMemo(() => buildMemoryOrganSystemModel({
    loadState: memoryLoadState,
    updatedAt: lastUpdatedAt,
    hasData: hasMemoryObservabilityData,
    hasSamples: hasMemoryTraffic,
    averageSuccessRate: avgSuccessRate,
    sampledSubsystemCount,
    configuredSubsystemCount,
    recallPackCount: recallData?.total_packs,
    residentDocCount,
    promptContextAvailable,
    distillCycles: distillData?.total_cycles,
    episodicWrites: episodicData?.total_writes,
  }), [
    avgSuccessRate,
    configuredSubsystemCount,
    distillData?.total_cycles,
    episodicData?.total_writes,
    hasMemoryObservabilityData,
    hasMemoryTraffic,
    lastUpdatedAt,
    memoryLoadState,
    promptContextAvailable,
    recallData?.total_packs,
    residentDocCount,
    sampledSubsystemCount,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">海马体记忆库</h1>
          <p className="text-sm text-gray-500">情景记忆、蒸馏历史、主设定文档与召回包预算。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
            <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Database}
        eyebrow="海马体记忆总览"
        title={configuredSubsystemCount > 0 ? `${configuredSubsystemCount} 个记忆子系统` : '记忆链路待采样'}
        description={
          hasSubsystemSamples
            ? `近窗口平均成功率 ${avgSuccessRate}% · ${sampledSubsystemCount}/${configuredSubsystemCount} 个子系统有样本 · 常驻设定 ${residentDocCount} 份`
            : '当前窗口还没有足够的记忆流量样本。'
        }
        gradientClass="from-primary-50 via-white to-cyan-50"
        metaItems={[
          `窗口：${windowMinutes} 分钟`,
          `召回包：${recallData?.total_packs ?? 0}`,
          `风格常驻：${activeStyleBindings.length}`,
          `情景写入：${episodicData?.total_writes ?? 0}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={memoryLoadState}
            windowMinutes={windowMinutes}
            sampleSize={subsystemOperationCount}
            sampleLabel="记忆操作"
            updatedAt={lastUpdatedAt}
            source="memory / recall / distill"
            definition="记忆页把子系统读写、召回包、蒸馏和常驻设定放在同一观察面；无样本与暂不可用会明确区分。"
          />
        )}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}

      {!error && !loading && !refreshing && memoryLoadState === LOAD_STATES.UNAVAILABLE ? (
        <NoticeBanner variant="warning">
          记忆观测接口当前未返回可用数据，页面会保留中性占位而不是把不可用误报成 0。
        </NoticeBanner>
      ) : null}

      {!error && !loading && !refreshing && memoryLoadState === LOAD_STATES.NO_SAMPLE ? (
        <NoticeBanner variant="info">
          当前还是空样本初始态。记忆链路尚未产生可观测数据时，卡片显示 0 或空列表属于预期。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={memoryOrganModel} />

      {hasSubsystemSamples ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {subsystems.map((subsystem) => (
            <MetricCard
              key={subsystem.subsystem}
              label={subsystem.display_name}
              value={subsystem.success_rate != null ? `${subsystem.success_rate}%` : '—'}
              hint={`${subsystem.total ?? 0} 次操作`}
              tone={getSuccessRateTone(subsystem.success_rate)}
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
        <div className="card p-4">
          <SectionHeading icon={Database} title="召回包预算" />
          {slots.length > 0 ? (
            <>
              <div className="space-y-2">
                {slots.map((slot) => (
                  <div key={slot.slot} className="flex items-center gap-2">
                    <span className="w-32 shrink-0 truncate text-xs text-gray-600">{slot.slot}</span>
                    <div className="h-3 flex-1 overflow-hidden rounded-full bg-gray-100">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${Math.min(100, (slot.avg_tokens ?? 0) / 8)}%` }}
                      />
                    </div>
                    <span className="w-16 text-right text-xs text-gray-500">
                      {slot.avg_tokens != null ? Number(slot.avg_tokens).toFixed(0) : '—'} 词元
                    </span>
                  </div>
                ))}
              </div>

              {recallData ? (
                <div className="mt-3 flex gap-4 border-t pt-3 text-xs text-gray-500">
                  <span>总召回包：{recallData.total_packs ?? 0}</span>
                  <span>截断率：{recallData.truncation_rate ?? '—'}%</span>
                  <span>冲突率：{recallData.conflict_rate ?? '—'}%</span>
                </div>
              ) : null}
            </>
          ) : (
            <EmptyStateCard title="暂无召回包预算样本" hint="当前窗口还没有预算统计数据。" className="p-4" />
          )}
        </div>

        <div className="card p-4">
          <SectionHeading icon={BookOpen} title="常驻设定文件" />
          {(worldDocs.length > 0 || personaDocs.length > 0 || styleDocs.length > 0) ? (
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

              <div className="border-t pt-3">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">Style Canon</div>
                {styleDocs.length > 0 ? (
                  <div className="max-h-36 space-y-1 overflow-y-auto">
                    {styleDocs.map((doc) => (
                      <div key={doc.doc_id} className="rounded bg-gray-50 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-gray-700">{doc.doc_id}</span>
                          <span className="shrink-0 text-gray-400">v{doc.version}</span>
                        </div>
                        {doc.preview ? <div className="mt-1 text-gray-500">{doc.preview}</div> : null}
                        {doc.source_path ? <div className="mt-1 truncate text-[11px] text-gray-400">{doc.source_path}</div> : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400">暂无语言风格常驻文件。</div>
                )}
              </div>
            </div>
          ) : (
            <EmptyStateCard title="暂无常驻设定文件" hint="等待文档同步后会自动展示。" className="p-4" />
          )}
        </div>

        <div className="card p-4">
          <SectionHeading icon={Brain} title="人格 / 风格常驻注入" />
          {(activeStyleBindings.length > 0 || latestStylePackRows.length > 0 || latestMemoryPersonaEvidence.length > 0 || latestMemoryRecallSummary) ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-3 text-center text-xs">
                <div>
                  <div className="text-lg font-bold text-indigo-600">{activeStyleBindings.length}</div>
                  <div className="text-gray-500">活跃绑定节点</div>
                </div>
                <div>
                  <div className="text-lg font-bold text-blue-600">{latestStylePackRows.length}</div>
                  <div className="text-gray-500">最近 Style 行</div>
                </div>
                <div>
                  <div className="text-lg font-bold text-purple-600">{latestMemoryPersonaEvidence.length}</div>
                  <div className="text-gray-500">人格证据</div>
                </div>
              </div>

              {activeStyleBindings.length > 0 ? (
                <div>
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">当前节点绑定</div>
                  <div className="max-h-32 space-y-1 overflow-y-auto">
                    {activeStyleBindings.map((row) => (
                      <div key={`${row.node_id}-${row.canon_id || 'style'}`} className="rounded bg-gray-50 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-gray-700">{row.node_id}</span>
                          <span className="shrink-0 text-gray-400">{row.canon_id || 'style_pack'}</span>
                        </div>
                        {row.preview ? <div className="mt-1 text-gray-500">{row.preview}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {latestStylePackRows.length > 0 ? (
                <div className="border-t pt-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">最近一次 Prompt Style Pack</div>
                  <div className="max-h-28 space-y-1 overflow-y-auto">
                    {latestStylePackRows.map((row, index) => (
                      <div key={`${row.node_id}-${index}`} className="rounded bg-gray-50 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-gray-700">{row.node_id}</span>
                          <span className="shrink-0 text-gray-400">{row.citation || row.canon_id || 'style_pack'}</span>
                        </div>
                        {row.preview ? <div className="mt-1 text-gray-500">{row.preview}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {latestMemoryRecallSummary ? (
                <NoticeBanner variant="info">{latestMemoryRecallSummary}</NoticeBanner>
              ) : null}

              {latestMemoryPersonaEvidence.length > 0 ? (
                <div className="border-t pt-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">最近一次人格证据</div>
                  <div className="max-h-32 space-y-1 overflow-y-auto">
                    {latestMemoryPersonaEvidence.map((row, index) => (
                      <div key={`${row.evidence_ref || row.source_ref || 'evidence'}-${index}`} className="rounded bg-gray-50 px-3 py-2 text-xs">
                        <div className="flex items-center justify-between gap-2 text-gray-500">
                          <span>{row.kind || 'episodic'}</span>
                          <span>{row.emotion_color_tag || (row.suppression_flag ? 'suppressed' : 'persona')}</span>
                        </div>
                        {row.content ? <div className="mt-1 text-gray-700">{row.content}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {!promptContextAvailable ? (
                <NoticeBanner variant="info">
                  静态常驻配置已展示；最近一次对话样本尚未进入 prompt context，所以这里还没有动态人格证据。
                </NoticeBanner>
              ) : null}
            </div>
          ) : (
            <EmptyStateCard
              title="暂无人格 / 风格注入快照"
              hint="当常驻风格绑定或最近一次对话样本产生后，这里会展示 style pack 与人格证据。"
              className="p-4"
            />
          )}
        </div>

        <div className="card p-4">
          <SectionHeading icon={Archive} title="上下文压缩" />
          {compressData ? (
            <div className="grid grid-cols-3 gap-3 text-center text-xs">
              <div>
                <div className="text-lg font-bold text-blue-600">{compressData.total_compressions ?? 0}</div>
                <div className="text-gray-500">压缩次数</div>
              </div>
              <div>
                <div className="text-lg font-bold text-green-600">{compressData.avg_compress_rate ?? '—'}%</div>
                <div className="text-gray-500">平均压缩率</div>
              </div>
              <div>
                <div className="text-lg font-bold text-purple-600">{compressData.total_tokens_saved ?? 0}</div>
                <div className="text-gray-500">节省词元</div>
              </div>
            </div>
          ) : (
            <EmptyStateCard title="暂无上下文压缩统计" hint="等待压缩链路产生样本后展示。" className="p-4" />
          )}
        </div>

        <div className="card p-4">
          <SectionHeading icon={Brain} title="长期蒸馏" />
          {distillData ? (
            <div className="grid grid-cols-3 gap-3 text-center text-xs">
              <div>
                <div className="text-lg font-bold text-indigo-600">{distillData.total_cycles ?? 0}</div>
                <div className="text-gray-500">蒸馏循环</div>
              </div>
              <div>
                <div className="text-lg font-bold text-green-600">{distillData.success_rate ?? '—'}%</div>
                <div className="text-gray-500">成功率</div>
              </div>
              <div>
                <div className="text-lg font-bold text-gray-600">{distillData.total_proposals ?? 0}</div>
                <div className="text-gray-500">提案数</div>
              </div>
            </div>
          ) : (
            <EmptyStateCard title="暂无蒸馏统计" hint="蒸馏任务运行后会出现统计。" className="p-4" />
          )}
        </div>

        <div className="card p-4 lg:col-span-2">
          <SectionHeading icon={Brain} title="情景记忆写入摘要" />
          {episodicData ? (
            <div className="grid grid-cols-2 gap-3 text-center text-xs sm:grid-cols-4">
              <div>
                <div className="text-lg font-bold text-blue-600">{episodicData.total_writes ?? 0}</div>
                <div className="text-gray-500">写入次数</div>
              </div>
              <div>
                <div className="text-lg font-bold text-orange-500">{episodicData.guard_block_rate ?? '—'}%</div>
                <div className="text-gray-500">守卫拦截率</div>
              </div>
              <div>
                <div className="text-lg font-bold text-purple-600">{episodicData.distill_eligible_rate ?? '—'}%</div>
                <div className="text-gray-500">蒸馏资格率</div>
              </div>
              <div>
                <div className="text-lg font-bold text-gray-700">{episodicData.dream_block_count ?? 0}</div>
                <div className="text-gray-500">梦境拦截</div>
              </div>
            </div>
          ) : (
            <EmptyStateCard title="暂无情景记忆写入统计" hint="待有写入后展示趋势。" className="p-4" />
          )}
        </div>
      </div>
    </div>
  )
}
