import React, { useState } from 'react'
import toast from 'react-hot-toast'
import { submitCanonProposal } from '../features/operations/api.js'
import {
  BookOpen,
  FilePlus,
  RefreshCw,
} from 'lucide-react'
import LoadingNotice from '../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
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
const CanonVaultStatsSection = React.lazy(() => import('../features/operations/components/canon/CanonVaultStatsSection.jsx'))

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

      <React.Suspense fallback={null}>
        <CanonVaultStatsSection
          recentManifestRows={recentManifestRows}
          vaultData={vaultData}
          vaultError={vaultError}
          vaultLoadState={vaultLoadState}
          vaultMetricCards={vaultMetricCards}
          vaultProgress={vaultProgress}
          vaultUpdatedAt={vaultUpdatedAt}
        />
      </React.Suspense>
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
