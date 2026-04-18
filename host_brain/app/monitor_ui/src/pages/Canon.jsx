import React, { useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveCanonProposal,
  rejectCanonProposal,
  rollbackCanonProposal,
  submitCanonProposal,
} from '../api/canon.js'
import {
  BookOpen,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  FilePlus,
  Lock,
  RefreshCw,
  RotateCcw,
  Search,
  XCircle,
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

const STATUS_STYLES = {
  pending: 'border-yellow-200 bg-yellow-100 text-yellow-700',
  APPLIED: 'border-green-200 bg-green-100 text-green-700',
  rejected: 'border-red-200 bg-red-100 text-red-700',
  rolled_back: 'border-gray-200 bg-gray-100 text-gray-600',
}

const PROPOSAL_STATUS_LABELS = {
  pending: '待审核',
  APPLIED: '已生效',
  rejected: '已拒绝',
  rolled_back: '已回滚',
}

const MANIFEST_STATUS_LABELS = {
  ACTIVE: '生效中',
  active: '生效中',
  UNDONE: '已撤销',
  GC: '已回收',
}

const PAGE_SIZE = 20

function formatDateTime(tsMs) {
  if (!tsMs) {
    return '—'
  }
  return new Date(tsMs).toLocaleString('zh-CN')
}

function StatusBadge({ status }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${
        STATUS_STYLES[status] || 'border-gray-200 bg-gray-100 text-gray-500'
      }`}
    >
      {PROPOSAL_STATUS_LABELS[status] || status || '—'}
    </span>
  )
}

function ProposalRow({ item, onApprove, onReject, onRollback }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <tr className="transition-colors hover:bg-gray-50">
        <td className="max-w-[140px] truncate px-4 py-2 font-mono text-xs text-gray-500" title={item.proposal_id}>
          {item.proposal_id || '—'}
        </td>
        <td className="max-w-[160px] truncate px-4 py-2 text-xs text-gray-700" title={item.target_doc_id}>
          {item.target_doc_id || '—'}
        </td>
        <td className="px-4 py-2">
          <StatusBadge status={item.status} />
        </td>
        <td className="max-w-[220px] truncate px-4 py-2 text-xs text-gray-500" title={item.summary}>
          {item.summary || '—'}
        </td>
        <td className="px-4 py-2 text-xs text-gray-400">{formatDateTime(item.ts_ms)}</td>
        <td className="px-4 py-2">
          <div className="flex items-center gap-1">
            {item.status === 'pending' ? (
              <>
                <button
                  className="flex items-center gap-1 rounded border border-green-200 bg-green-50 px-2 py-1 text-xs text-green-700 transition-colors hover:bg-green-100"
                  onClick={() => onApprove(item)}
                  title="审核通过"
                >
                  <CheckCircle size={12} />
                  通过
                </button>
                <button
                  className="flex items-center gap-1 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700 transition-colors hover:bg-red-100"
                  onClick={() => onReject(item)}
                  title="拒绝提案"
                >
                  <XCircle size={12} />
                  拒绝
                </button>
              </>
            ) : null}

            {item.status === 'APPLIED' ? (
              <button
                className="flex items-center gap-1 rounded border border-orange-200 bg-orange-50 px-2 py-1 text-xs text-orange-700 transition-colors hover:bg-orange-100"
                onClick={() => onRollback(item)}
                title="回滚到基础版本"
              >
                <RotateCcw size={12} />
                回滚
              </button>
            ) : null}

            <button
              className="rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              onClick={() => setExpanded((value) => !value)}
              title={expanded ? '收起详情' : '展开详情'}
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>
        </td>
      </tr>

      {expanded ? (
        <tr className="bg-gray-50">
          <td colSpan={6} className="px-6 py-3">
            <div className="space-y-1 text-xs text-gray-600">
              <div><span className="font-medium text-gray-700">提案 ID：</span>{item.proposal_id || '—'}</div>
              <div><span className="font-medium text-gray-700">目标文档：</span>{item.target_doc_id || '—'}</div>
              {item.proposer_node_id ? (
                <div><span className="font-medium text-gray-700">来源节点：</span>{item.proposer_node_id}</div>
              ) : null}
              {item.proposed_hash ? (
                <div><span className="font-medium text-gray-700">内容哈希：</span><span className="font-mono">{item.proposed_hash}</span></div>
              ) : null}
              {item.base_version != null ? (
                <div><span className="font-medium text-gray-700">版本变化：</span>v{item.base_version} → v{item.proposed_version}</div>
              ) : null}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  )
}

function SubmitModal({ onClose, onSubmit }) {
  const [form, setForm] = useState({
    proposal_id: `manual-${Date.now()}`,
    target_doc_id: '',
    proposed_title: '',
    proposed_body: '',
    proposed_tags: '',
    summary: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!form.target_doc_id.trim() || !form.proposed_title.trim() || !form.proposed_body.trim()) {
      setError('目标文档 ID、标题和正文为必填项。')
      return
    }

    setLoading(true)
    setError('')
    try {
      await onSubmit({
        ...form,
        proposed_tags: form.proposed_tags
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      })
      onClose()
    } catch (submitError) {
      setError(submitError.message || '提交失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="mx-4 w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h3 className="mb-4 flex items-center gap-2 text-base font-semibold text-gray-800">
          <FilePlus size={16} className="text-primary" />
          提交新主设定提案
        </h3>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">提案 ID</label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposal_id}
              onChange={(event) => setForm((current) => ({ ...current, proposal_id: event.target.value }))}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              目标文档 ID <span className="text-red-500">*</span>
            </label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.target_doc_id}
              onChange={(event) => setForm((current) => ({ ...current, target_doc_id: event.target.value }))}
              placeholder="例如：world_canon_001"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              标题 <span className="text-red-500">*</span>
            </label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposed_title}
              onChange={(event) => setForm((current) => ({ ...current, proposed_title: event.target.value }))}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              正文 <span className="text-red-500">*</span>
            </label>
            <textarea
              rows={4}
              className="w-full resize-none rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposed_body}
              onChange={(event) => setForm((current) => ({ ...current, proposed_body: event.target.value }))}
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">标签（逗号分隔）</label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposed_tags}
              onChange={(event) => setForm((current) => ({ ...current, proposed_tags: event.target.value }))}
              placeholder="例如：角色, 世界观, 设定"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">摘要（不超过 200 字）</label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.summary}
              onChange={(event) => setForm((current) => ({ ...current, summary: event.target.value.slice(0, 200) }))}
            />
          </div>

          {error ? <NoticeBanner variant="error">{error}</NoticeBanner> : null}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex items-center gap-1.5 rounded bg-primary px-4 py-1.5 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
            >
              {loading ? <RefreshCw size={12} className="animate-spin" /> : null}
              提交提案
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function DocCard({ doc }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <span className="truncate text-sm font-medium text-gray-800">{doc.title || doc.doc_id}</span>
            <span className="text-xs font-mono text-gray-400">v{doc.version}</span>
            <span
              className={`rounded border px-1.5 py-0.5 text-xs ${
                doc.status === 'ACTIVE' || doc.status === 'active'
                  ? 'border-green-200 bg-green-50 text-green-700'
                  : 'border-gray-200 bg-gray-100 text-gray-500'
              }`}
            >
              {MANIFEST_STATUS_LABELS[doc.status] || doc.status || '—'}
            </span>
          </div>
          <div className="mb-1 text-xs text-gray-500">
            ID：<span className="font-mono">{doc.doc_id}</span> · 更新时间：{formatDateTime(doc.ts_ms)}
          </div>
          {Array.isArray(doc.tags) && doc.tags.length > 0 ? (
            <div className="mb-1 flex flex-wrap gap-1">
              {doc.tags.map((tag) => (
                <span key={tag} className="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-xs text-blue-600">
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <button
          className="flex-shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>

      {expanded ? (
        <div className="mt-2 border-t border-gray-100 pt-2">
          <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded bg-gray-50 p-2 text-xs text-gray-600">
            {doc.body || '（无正文）'}
          </pre>
          <div className="mt-1 text-xs text-gray-400">
            哈希：<span className="font-mono">{doc.hash?.slice(0, 16) || '—'}</span>
            {doc.updated_by ? <> · 更新者：{doc.updated_by}</> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function Canon() {
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [activeTab, setActiveTab] = useState('proposals')
  const [docKeyword, setDocKeyword] = useState('')
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
  } = useCanonDocumentsQuery()
  const {
    vaultData,
    vaultError,
    loading: vaultLoading,
    refreshing: vaultRefreshing,
    stale: vaultStale,
    lastUpdatedAt: vaultUpdatedAt,
    refetch: refetchVaultStats,
  } = useCanonVaultStatsQuery({ limit: 10 })

  const activeError = activeTab === 'proposals' ? proposalsError : documentsError
  const activeLoadState = resolveLoadState({
    loading: activeTab === 'proposals'
      ? proposalsLoading || proposalsRefreshing
      : documentsLoading || documentsRefreshing,
    error: activeError,
    hasData: activeTab === 'proposals' ? Boolean(proposalsData) : Boolean(documentsData),
    hasSamples: activeTab === 'proposals' ? total > 0 : documents.length > 0,
    available: activeTab === 'proposals' ? Boolean(proposalsData) : Boolean(documentsData),
    stale: activeTab === 'proposals' ? proposalsStale : documentsStale,
  })
  const vaultLoadState = resolveLoadState({
    loading: vaultLoading || vaultRefreshing,
    error: vaultError,
    hasData: Boolean(vaultData),
    hasSamples: Number(vaultData?.total_manifests ?? 0) > 0,
    available: Boolean(vaultData),
    stale: vaultStale,
  })

  const handleApprove = async (item) => {
    if (!window.confirm(`确认通过提案 ${item.proposal_id || '—'}？`)) {
      return
    }
    try {
      await approveCanonProposal(item.proposal_id, {
        proposed_title: item.summary || item.proposal_id,
        proposed_tags: [],
        proposed_body: item.summary || '',
      })
      await Promise.allSettled([refetchProposals(), refetchDocuments(), refetchVaultStats()])
      toast.success('提案已通过')
    } catch (actionError) {
      toast.error(`审核失败：${actionError.message}`)
    }
  }

  const handleReject = async (item) => {
    const reason = window.prompt('请输入拒绝原因（可选）：')
    if (reason === null) {
      return
    }
    try {
      await rejectCanonProposal(item.proposal_id, { reason })
      await Promise.allSettled([refetchProposals(), refetchVaultStats()])
      toast.success('提案已拒绝')
    } catch (actionError) {
      toast.error(`拒绝失败：${actionError.message}`)
    }
  }

  const handleRollback = async (item) => {
    const reason = window.prompt(`确认回滚提案 ${item.proposal_id || '—'}？\n请输入回滚原因（可选）：`)
    if (reason === null) {
      return
    }
    try {
      await rollbackCanonProposal(item.proposal_id, { rollback_reason: reason })
      await Promise.allSettled([refetchProposals(), refetchDocuments(), refetchVaultStats()])
      toast.success('提案已回滚')
    } catch (actionError) {
      toast.error(`回滚失败：${actionError.message}`)
    }
  }

  const handleSubmit = async (formData) => {
    await submitCanonProposal(formData)
    await Promise.allSettled([refetchProposals(), refetchVaultStats()])
    toast.success('提案已提交')
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const filteredDocuments = useMemo(() => {
    const keyword = docKeyword.trim().toLowerCase()
    if (!keyword) {
      return documents
    }
    return documents.filter((doc) => (
      (doc.title || '').toLowerCase().includes(keyword)
      || (doc.doc_id || '').toLowerCase().includes(keyword)
      || (doc.tags || []).some((tag) => String(tag).toLowerCase().includes(keyword))
    ))
  }, [docKeyword, documents])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <PageHero
          icon={BookOpen}
          eyebrow="设定管理"
          title={activeTab === 'proposals' ? '提案审核' : '文档浏览'}
          description="管理世界设定提案、主设定文档与变更清单。"
          gradientClass="from-primary-50 via-white to-violet-50"
          metaItems={[
            `提案数：${total}`,
            `文档数：${documents.length}`,
            `清单：${vaultData?.total_manifests ?? 0}`,
          ]}
          aside={(
            <SampleMeta
              className="max-w-md"
              loadState={activeLoadState}
              sampleSize={activeTab === 'proposals' ? total : documents.length}
              sampleLabel={activeTab === 'proposals' ? '提案' : '文档'}
              updatedAt={activeTab === 'proposals' ? proposalsUpdatedAt : documentsUpdatedAt}
              source={activeTab === 'proposals' ? 'canon proposals' : 'canon documents'}
              definition={
                activeTab === 'proposals'
                  ? '提案审核与主设定文档现在拆成独立查询；当前标签页的失败、无样本和过期状态会单独表达。'
                  : '主设定文档与提案列表分离取数，切换标签页不会再把另一块数据的失败混在一起。'
              }
            />
          )}
        />

        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-50"
            onClick={() => {
              void Promise.allSettled([
                activeTab === 'proposals' ? refetchProposals() : refetchDocuments(),
                refetchVaultStats(),
              ])
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

      {activeTab === 'proposals' ? (
        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3">
            <span className="text-xs text-gray-500">状态过滤：</span>
            {['', 'pending', 'APPLIED', 'rejected'].map((status) => (
              <button
                key={status}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  statusFilter === status ? 'bg-primary text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
                onClick={() => {
                  setStatusFilter(status)
                  setPage(1)
                }}
              >
                {PROPOSAL_STATUS_LABELS[status] || status || '全部'}
              </button>
            ))}
            <span className="ml-auto text-xs text-gray-400">共 {total} 条</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  {['提案 ID', '目标文档', '状态', '摘要', '时间', '操作'].map((title) => (
                    <th key={title} className="px-4 py-2 text-left text-xs font-medium text-gray-500">
                      {title}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {proposalsLoading || proposalsRefreshing ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-500">
                      <span className="inline-flex items-center gap-2">
                        <RefreshCw size={12} className="animate-spin" />
                        加载中…
                      </span>
                    </td>
                  </tr>
                ) : proposals.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-6">
                      <EmptyStateCard title="暂无提案" hint="提交或同步后会展示在这里。" className="p-6" />
                    </td>
                  </tr>
                ) : (
                  proposals.map((item) => (
                    <ProposalRow key={item.proposal_id} item={item} onApprove={handleApprove} onReject={handleReject} onRollback={handleRollback} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {totalPages > 1 ? (
            <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3">
              <span className="text-xs text-gray-400">第 {page} / {totalPages} 页</span>
              <div className="flex gap-1">
                <button
                  className="rounded border border-gray-200 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => current - 1)}
                >
                  上一页
                </button>
                <button
                  className="rounded border border-gray-200 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  disabled={page >= totalPages}
                  onClick={() => setPage((current) => current + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded border border-gray-300 py-1.5 pl-8 pr-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              placeholder="按标题、文档 ID 或标签搜索文档"
              value={docKeyword}
              onChange={(event) => setDocKeyword(event.target.value)}
            />
          </div>

          {documentsLoading || documentsRefreshing ? (
            <NoticeBanner variant="info">文档加载中…</NoticeBanner>
          ) : filteredDocuments.length === 0 ? (
            <EmptyStateCard title="暂无主设定文档" hint="当前没有匹配的文档结果。" className="p-8" />
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {filteredDocuments.map((doc) => (
                <DocCard key={doc.doc_id} doc={doc} />
              ))}
            </div>
          )}
        </div>
      )}

      {showSubmitModal ? <SubmitModal onClose={() => setShowSubmitModal(false)} onSubmit={handleSubmit} /> : null}
    </div>
  )
}
