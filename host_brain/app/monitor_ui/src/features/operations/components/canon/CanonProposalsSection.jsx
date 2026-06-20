import React, { useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveCanonProposal,
  rejectCanonProposal,
  rollbackCanonProposal,
} from '../../api.js'
import { formatDateTime } from '../../../../shared/formatters.js'
import {
  CANON_PROPOSAL_STATUS_CLASSES,
  CANON_PROPOSAL_STATUS_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../../../shared/statusMeta.js'
import {
  CheckCircle,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  RotateCcw,
  XCircle,
} from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'

function StatusBadge({ status }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${
        resolveStatusClass(CANON_PROPOSAL_STATUS_CLASSES, status, 'border-gray-200 bg-gray-100 text-gray-500')
      }`}
    >
      {resolveStatusLabel(CANON_PROPOSAL_STATUS_LABELS, status)}
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

export default function CanonProposalsSection({
  proposals,
  total,
  loading,
  refreshing,
  statusFilter,
  setStatusFilter,
  page,
  setPage,
  pageSize = 20,
  refetchProposals,
  refetchDocuments,
  refetchVaultStats,
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

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

  return (
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
            {resolveStatusLabel(CANON_PROPOSAL_STATUS_LABELS, status, '全部')}
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
            {loading || refreshing ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-500">
                  <span className="inline-flex items-center gap-2">
                    <RefreshCw size={12} className="animate-spin" />
                    同步中…
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
                <ProposalRow
                  key={item.proposal_id}
                  item={item}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  onRollback={handleRollback}
                />
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
  )
}
