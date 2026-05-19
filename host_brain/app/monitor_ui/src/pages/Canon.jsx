import React, { useState } from 'react'
import toast from 'react-hot-toast'
import { submitCanonProposal } from '../api/canon.js'
import {
  BookOpen,
  FilePlus,
  Lock,
  RefreshCw,
} from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import SampleMeta from '../components/obs/SampleMeta.jsx'
import PageHero from '../components/PageHero.jsx'
import SectionHeading from '../components/SectionHeading.jsx'
import { resolveLoadState } from '../models/loadState.js'
import {
  useCanonDocumentsQuery,
  useCanonProposalsQuery,
  useCanonVaultStatsQuery,
} from '../queries/canonQueries.js'

const CanonProposalsSection = React.lazy(() => import('../components/canon/CanonProposalsSection.jsx'))
const CanonDocumentsSection = React.lazy(() => import('../components/canon/CanonDocumentsSection.jsx'))
const CanonSubmitModal = React.lazy(() => import('../components/canon/CanonSubmitModal.jsx'))

const MANIFEST_STATUS_LABELS = {
  ACTIVE: '生效中',
  active: '生效中',
  UNDONE: '已撤销',
  GC: '已回收',
}

const PAGE_SIZE = 20

export default function Canon() {
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [activeTab, setActiveTab] = useState('proposals')
  const proposalsActive = activeTab === 'proposals'
  const documentsActive = activeTab === 'documents'

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
    pageSize: PAGE_SIZE,
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

  const activeError = proposalsActive ? proposalsError : documentsError
  const activeLoadState = resolveLoadState({
    loading: proposalsActive
      ? proposalsLoading || proposalsRefreshing
      : documentsLoading || documentsRefreshing,
    error: activeError,
    hasData: proposalsActive ? Boolean(proposalsData) : Boolean(documentsData),
    hasSamples: proposalsActive ? total > 0 : documents.length > 0,
    available: proposalsActive ? Boolean(proposalsData) : Boolean(documentsData),
    stale: proposalsActive ? proposalsStale : documentsStale,
  })
  const vaultLoadState = resolveLoadState({
    loading: vaultLoading || vaultRefreshing,
    error: vaultError,
    hasData: Boolean(vaultData),
    hasSamples: Number(vaultData?.total_manifests ?? 0) > 0,
    available: Boolean(vaultData),
    stale: vaultStale,
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
          title={proposalsActive ? '提案审核' : '文档浏览'}
          description="管理世界设定提案、主设定文档与变更清单。"
          gradientClass="from-primary-50 via-white to-violet-50"
          metaItems={[
            proposalsActive ? `提案数：${total}` : `文档数：${documents.length}`,
            `清单：${vaultData?.total_manifests ?? 0}`,
            proposalsActive
              ? `状态过滤：${statusFilter || '全部'}`
              : `当前标签：主设定文档`,
          ]}
          aside={(
            <SampleMeta
              className="max-w-md"
              loadState={activeLoadState}
              sampleSize={proposalsActive ? total : documents.length}
              sampleLabel={proposalsActive ? '提案' : '文档'}
              updatedAt={proposalsActive ? proposalsUpdatedAt : documentsUpdatedAt}
              source={proposalsActive ? 'canon proposals' : 'canon documents'}
              definition={
                proposalsActive
                  ? '提案审核与主设定文档已拆成独立模块；未打开的标签页不会再提前装配重型表格与卡片。'
                  : '主设定文档现在按标签页独立加载；切回提案页时才会装配审核表格与操作逻辑。'
              }
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
              className={
                proposalsLoading
                || proposalsRefreshing
                || documentsLoading
                || documentsRefreshing
                || vaultLoading
                || vaultRefreshing
                  ? 'animate-spin'
                  : ''
              }
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
            <MetricCard label="总清单" value={vaultData.total_manifests ?? 0} variant="stacked" />
            <MetricCard label="生效中" value={vaultData.active_count ?? 0} variant="stacked" tone="text-green-600" />
            <MetricCard label="已撤销" value={vaultData.undone_count ?? 0} variant="stacked" tone="text-orange-500" />
            <MetricCard label="已回收" value={vaultData.gc_count ?? 0} variant="stacked" tone="text-gray-500" />
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

          {vaultData.total_manifests > 0 ? (
            <div className="mb-3">
              <div className="mb-1 flex justify-between text-xs text-gray-400">
                <span>生效中 {Math.round((vaultData.active_count / vaultData.total_manifests) * 100)}%</span>
                <span>已撤销 {Math.round((vaultData.undone_count / vaultData.total_manifests) * 100)}%</span>
                <span>已回收 {Math.round((vaultData.gc_count / vaultData.total_manifests) * 100)}%</span>
              </div>
              <div className="flex h-2 overflow-hidden rounded">
                <div className="bg-green-500" style={{ width: `${(vaultData.active_count / vaultData.total_manifests) * 100}%` }} />
                <div className="bg-orange-400" style={{ width: `${(vaultData.undone_count / vaultData.total_manifests) * 100}%` }} />
                <div className="bg-gray-300" style={{ width: `${(vaultData.gc_count / vaultData.total_manifests) * 100}%` }} />
              </div>
            </div>
          ) : null}

          {Array.isArray(vaultData.recent_manifests) && vaultData.recent_manifests.length > 0 ? (
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
                  {vaultData.recent_manifests.map((item, index) => (
                    <tr key={`${item.manifest_id}-${index}`} className="hover:bg-gray-50">
                      <td className="max-w-[120px] truncate px-3 py-1.5 font-mono text-violet-600" title={item.manifest_id}>
                        {item.manifest_id || '—'}
                      </td>
                      <td className="px-3 py-1.5 text-gray-500">{item.artifact_type || '—'}</td>
                      <td className="px-3 py-1.5">
                        <span
                          className={`rounded border px-1.5 py-0.5 font-medium ${
                            item.status === 'ACTIVE'
                              ? 'border-green-200 bg-green-50 text-green-700'
                              : item.status === 'UNDONE'
                                ? 'border-orange-200 bg-orange-50 text-orange-700'
                                : 'border-gray-200 bg-gray-100 text-gray-500'
                          }`}
                        >
                          {MANIFEST_STATUS_LABELS[item.status] || item.status || '—'}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-center">
                        {item.is_reversible ? <span className="text-green-600">✓</span> : <span className="text-gray-300">—</span>}
                      </td>
                      <td className="max-w-[160px] truncate px-3 py-1.5 text-gray-400" title={item.summary}>
                        {item.summary || '—'}
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
        {[
          { key: 'proposals', label: '提案列表' },
          { key: 'documents', label: '主设定文档' },
        ].map((item) => (
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
            <NoticeBanner variant="info" title="按需加载中">
              正在装配提案审核分区…
            </NoticeBanner>
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
            pageSize={PAGE_SIZE}
            refetchProposals={refetchProposals}
            refetchDocuments={refetchDocuments}
            refetchVaultStats={refetchVaultStats}
          />
        </React.Suspense>
      ) : (
        <React.Suspense
          fallback={(
            <NoticeBanner variant="info" title="按需加载中">
              正在装配主设定文档分区…
            </NoticeBanner>
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
