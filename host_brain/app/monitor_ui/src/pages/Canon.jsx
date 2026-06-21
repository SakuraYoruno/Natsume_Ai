import React, { useState } from 'react'
import toast from 'react-hot-toast'
import { submitCanonProposal } from '../features/operations/api.js'
import {
  BookOpen,
  FilePlus,
  Lock,
  RefreshCw,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../shared/ui/LoadingNotice.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import SectionHeading from '../shared/ui/SectionHeading.jsx'
import {
  CANON_PAGE_SIZE,
  CANON_TABS,
  buildCanonViewModel,
  getCanonTabState,
} from '../features/operations/canonModel.js'
import {
  useCanonDocumentsQuery,
  useCanonProposalsQuery,
  useCanonVaultStatsQuery,
} from '../features/operations/canonQueries.js'

const CanonProposalsSection = React.lazy(() => import('../features/operations/components/canon/CanonProposalsSection.jsx'))
const CanonDocumentsSection = React.lazy(() => import('../features/operations/components/canon/CanonDocumentsSection.jsx'))
const CanonSubmitModal = React.lazy(() => import('../features/operations/components/canon/CanonSubmitModal.jsx'))

export default function Canon() {
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [activeTab, setActiveTab] = useState('proposals')
  const { proposalsActive, documentsActive } = getCanonTabState(activeTab)

  const {
    proposalsData,
    proposals,
    total,
    proposalsError,
    loading: proposalsLoading,
    refreshing: proposalsRefreshing,
    stale: proposalsStale,
    lastUpdatedAt: proposalsUpdatedAt,
    refetch: refetchProposals,
  } = useCanonProposalsQuery({
    status: statusFilter,
    page,
    pageSize: CANON_PAGE_SIZE,
    enabled: proposalsActive,
  })
  const {
    documentsData,
    documents,
    documentsError,
    loading: documentsLoading,
    refreshing: documentsRefreshing,
    stale: documentsStale,
    lastUpdatedAt: documentsUpdatedAt,
    refetch: refetchDocuments,
  } = useCanonDocumentsQuery({
    enabled: documentsActive,
  })
  const {
    vaultData,
    vaultError,
    loading: vaultLoading,
    refreshing: vaultRefreshing,
    stale: vaultStale,
    lastUpdatedAt: vaultUpdatedAt,
    refetch: refetchVaultStats,
  } = useCanonVaultStatsQuery({ limit: 10 })

  const {
    activeError,
    vaultLoadState,
    heroView,
    refreshing,
    vaultMetricCards,
    vaultProgress,
    recentManifestRows,
  } = buildCanonViewModel({
    activeTab,
    statusFilter,
    proposalsData,
    proposals,
    total,
    proposalsError,
    proposalsLoading,
    proposalsRefreshing,
    proposalsStale,
    proposalsUpdatedAt,
    documentsData,
    documents,
    documentsError,
    documentsLoading,
    documentsRefreshing,
    documentsStale,
    documentsUpdatedAt,
    vaultData,
    vaultError,
    vaultLoading,
    vaultRefreshing,
    vaultStale,
  })

  const handleSubmit = async (formData) => {
    await submitCanonProposal(formData)
    await Promise.allSettled([refetchProposals(), refetchVaultStats()])
    toast.success('提案已提交')
  }

  const refreshActiveTab = () => Promise.allSettled([
    proposalsActive ? refetchProposals() : refetchDocuments(),
    refetchVaultStats(),
  ])

  return (
    <div className="space-y-4">
      <h1 className="sr-only">设定管理</h1>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <PageHero
          icon={BookOpen}
          eyebrow="设定管理"
          title={heroView.title}
          description="管理世界设定提案、主设定文档与变更清单。"
          gradientClass="from-primary-50 via-white to-violet-50"
          metaItems={heroView.metaItems}
          aside={(
            <SampleMeta
              className="max-w-md"
              loadState={heroView.sampleMeta.loadState}
              sampleSize={heroView.sampleMeta.sampleSize}
              sampleLabel={heroView.sampleMeta.sampleLabel}
              updatedAt={heroView.sampleMeta.updatedAt}
              source={heroView.sampleMeta.source}
              definition={heroView.sampleMeta.definition}
            />
          )}
        />

        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-50"
            onClick={() => {
              void refreshActiveTab()
            }}
          >
            <RefreshCw
              size={13}
              className={refreshing ? 'animate-spin' : ''}
            />
            刷新
          </button>
          <button
            className="flex items-center gap-1.5 rounded bg-primary px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-600"
            onClick={() => setShowSubmitModal(true)}
          >
            <FilePlus size={13} />
            提交提案
          </button>
        </div>
      </div>

      {!vaultData && vaultError ? (
        <NoticeBanner variant="warning" title="变更清单统计暂不可用">
          {vaultError}
        </NoticeBanner>
      ) : null}

      {vaultData ? (
        <div className="card border border-gray-200 p-4">
          <SectionHeading icon={Lock} title="变更清单统计" tone="text-violet-600" />
          <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {vaultMetricCards.map((card) => (
              <MetricCard key={card.label} label={card.label} value={card.value} variant="stacked" tone={card.tone} />
            ))}
          </div>

          <div className="mb-3">
            <SampleMeta
              loadState={vaultLoadState}
              sampleSize={vaultData.total_manifests ?? 0}
              sampleLabel="清单"
              updatedAt={vaultUpdatedAt}
              source="vault manifest"
              definition="变更清单统计独立于提案/文档加载；如果这里只失败，不会再误伤当前标签页的主数据状态。"
            />
          </div>

          {vaultError ? (
            <NoticeBanner variant="warning" title="变更清单统计暂不可用">
              {vaultError}
            </NoticeBanner>
          ) : null}

          {vaultProgress ? (
            <div className="mb-3">
              <div className="mb-1 flex justify-between text-xs text-gray-400">
                {vaultProgress.map((item) => (
                  <span key={item.label}>{item.label} {item.pctLabel}</span>
                ))}
              </div>
              <div className="flex h-2 overflow-hidden rounded">
                {vaultProgress.map((item) => (
                  <div key={item.label} className={item.className} style={{ width: item.width }} />
                ))}
              </div>
            </div>
          ) : null}

          {recentManifestRows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-b border-gray-100 bg-gray-50">
                  <tr>
                    {['清单 ID', '类型', '状态', '可逆', '摘要'].map((title) => (
                      <th key={title} className="px-3 py-1.5 text-left font-medium text-gray-500">
                        {title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {recentManifestRows.map((item) => (
                    <tr key={item.key} className="hover:bg-gray-50">
                      <td className="max-w-[120px] truncate px-3 py-1.5 font-mono text-violet-600" title={item.manifestTitle}>
                        {item.manifestId}
                      </td>
                      <td className="px-3 py-1.5 text-gray-500">{item.artifactType}</td>
                      <td className="px-3 py-1.5">
                        <span
                          className={`rounded border px-1.5 py-0.5 font-medium ${item.statusClassName}`}
                        >
                          {item.statusLabel}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-center">
                        {item.reversible ? <span className="text-green-600">✓</span> : <span className="text-gray-300">—</span>}
                      </td>
                      <td className="max-w-[160px] truncate px-3 py-1.5 text-gray-400" title={item.summaryTitle}>
                        {item.summary}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyStateCard title="暂无变更清单记录" hint="等待设定变更写入后展示。" className="p-6" />
          )}
        </div>
      ) : null}

      <div className="flex border-b border-gray-200">
        {CANON_TABS.map((item) => (
          <button
            key={item.key}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === item.key ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
            onClick={() => setActiveTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {activeError ? <NoticeBanner variant="error">{activeError}</NoticeBanner> : null}

      {proposalsActive ? (
        <React.Suspense
          fallback={(
            <LoadingNotice>
              正在装配提案审核分区…
            </LoadingNotice>
          )}
        >
          <CanonProposalsSection
            proposals={proposals}
            total={total}
            loading={proposalsLoading}
            refreshing={proposalsRefreshing}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            page={page}
            setPage={setPage}
            pageSize={CANON_PAGE_SIZE}
            refetchProposals={refetchProposals}
            refetchDocuments={refetchDocuments}
            refetchVaultStats={refetchVaultStats}
          />
        </React.Suspense>
      ) : (
        <React.Suspense
          fallback={(
            <LoadingNotice>
              正在装配主设定文档分区…
            </LoadingNotice>
          )}
        >
          <CanonDocumentsSection
            documents={documents}
            loading={documentsLoading}
            refreshing={documentsRefreshing}
          />
        </React.Suspense>
      )}

      {showSubmitModal ? (
        <React.Suspense fallback={null}>
          <CanonSubmitModal onClose={() => setShowSubmitModal(false)} onSubmit={handleSubmit} />
        </React.Suspense>
      ) : null}
    </div>
  )
}
