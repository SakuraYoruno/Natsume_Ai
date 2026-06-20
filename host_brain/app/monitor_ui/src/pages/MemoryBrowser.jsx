import React, { useMemo, useState } from 'react'
import { Archive, BookOpen, Brain, Database, RefreshCw } from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import SectionHeading from '../shared/ui/SectionHeading.jsx'
import TimeWindowSelect from '../shared/ui/TimeWindowSelect.jsx'
import { useMemoryOverviewQuery } from '../features/life/memoryQueries.js'
import { buildMemoryBrowserViewModel, MEMORY_BROWSER_WINDOW_OPTIONS } from '../features/life/memoryBrowserModel.js'
import useAppStore from '../store/useAppStore.js'

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

  const {
    worldDocs,
    personaDocs,
    subsystemOperationCount,
    hasSubsystemSamples,
    memoryLoadState,
    memoryOrganModel,
    subsystemMetricCards,
    heroTitle,
    heroDescription,
    heroMetaItems,
    showUnavailableNotice,
    showNoSampleNotice,
  } = useMemo(() => buildMemoryBrowserViewModel({
    memoryData,
    episodicData,
    distillData,
    canonDocs,
    compressData,
    residentContext,
    loading,
    refreshing,
    error,
    lastUpdatedAt,
    windowMinutes,
  }), [
    canonDocs,
    compressData,
    distillData,
    episodicData,
    error,
    lastUpdatedAt,
    loading,
    memoryData,
    refreshing,
    residentContext,
    windowMinutes,
  ])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">海马体记忆库</h1>
          <p className="text-sm text-gray-500">情景记忆、蒸馏历史与主设定文档。</p>
        </div>
        <div className="flex items-center gap-2">
          <TimeWindowSelect value={windowMinutes} onChange={setWindowMinutes} options={MEMORY_BROWSER_WINDOW_OPTIONS} />
          <button className="btn-secondary" onClick={() => refetch()} disabled={loading || refreshing}>
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
            sampleLabel="记忆操作"
            updatedAt={lastUpdatedAt}
            source="memory / recall / distill"
            definition="记忆页把子系统读写、蒸馏和常驻设定放在同一观察面；无样本与暂不可用会明确区分。"
          />
        )}
      />

      {error ? <NoticeBanner variant="error">{error.message}</NoticeBanner> : null}

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

      {hasSubsystemSamples ? (
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
