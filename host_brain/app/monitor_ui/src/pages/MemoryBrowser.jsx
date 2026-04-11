import React, { useCallback, useEffect, useState } from 'react'
import { Archive, BookOpen, Brain, Database, RefreshCw } from 'lucide-react'
import {
  fetchCanonDocuments,
  fetchContextCompressorStats,
  fetchDistillationStats,
  fetchEpisodicStoreStats,
  fetchMemoryStats,
  fetchRecallPackStats,
  fetchResidentMemoryContext,
} from '../api/obs.js'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import SectionHeading from '../components/SectionHeading.jsx'
import TimeWindowSelect from '../components/TimeWindowSelect.jsx'
import useAppStore from '../store/useAppStore.js'

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

  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [memoryData, setMemoryData] = useState(null)
  const [episodicData, setEpisodicData] = useState(null)
  const [distillData, setDistillData] = useState(null)
  const [canonDocs, setCanonDocs] = useState(null)
  const [recallData, setRecallData] = useState(null)
  const [compressData, setCompressData] = useState(null)
  const [residentContext, setResidentContext] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [memory, episodic, distill, canon, recall, compress, resident] = await Promise.all([
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchEpisodicStoreStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchDistillationStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchCanonDocuments().catch(() => null),
        fetchRecallPackStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchContextCompressorStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchResidentMemoryContext().catch(() => null),
      ])

      setMemoryData(memory)
      setEpisodicData(episodic)
      setDistillData(distill)
      setCanonDocs(canon)
      setRecallData(recall)
      setCompressData(compress)
      setResidentContext(resident)
    } catch (loadError) {
      setError(`加载失败：${loadError.message}`)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) {
      return undefined
    }
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

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

  const avgSuccessRate = subsystems.length > 0
    ? Math.round(
      subsystems.reduce((sum, item) => sum + Number(item?.success_rate || 0), 0) / subsystems.length
    )
    : null

  const hasMemoryTraffic = (
    subsystems.length > 0
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">海马体记忆库</h1>
          <p className="text-sm text-gray-500">情景记忆、蒸馏历史、主设定文档与召回包预算。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <PageHero
        icon={Database}
        eyebrow="海马体记忆总览"
        title={`${subsystems.length} 个子系统`}
        description={
          avgSuccessRate != null
            ? `近窗口平均成功率 ${avgSuccessRate}% · 常驻设定 ${residentDocCount} 份 · 召回槽位 ${slots.length} 个`
            : '当前窗口还没有足够的记忆流量样本。'
        }
        gradientClass="from-primary-50 via-white to-cyan-50"
        metaItems={[
          `窗口：${windowMinutes} 分钟`,
          `召回包：${recallData?.total_packs ?? 0}`,
          `风格常驻：${activeStyleBindings.length}`,
          `情景写入：${episodicData?.total_writes ?? 0}`,
        ]}
      />

      {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

      {!error && !hasMemoryTraffic ? (
        <NoticeBanner variant="info">
          当前还是空样本初始态。记忆链路尚未产生可观测数据时，卡片显示 0 或空列表属于预期。
        </NoticeBanner>
      ) : null}

      {subsystems.length > 0 ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {subsystems.map((subsystem) => (
            <MetricCard
              key={subsystem.subsystem}
              label={subsystem.display_name}
              value={subsystem.success_rate != null ? `${subsystem.success_rate}%` : '—'}
              hint={`${subsystem.total ?? 0} 次操作`}
              tone={
                subsystem.success_rate >= 90
                  ? 'text-green-600'
                  : subsystem.success_rate >= 70
                    ? 'text-orange-500'
                    : 'text-red-500'
              }
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
