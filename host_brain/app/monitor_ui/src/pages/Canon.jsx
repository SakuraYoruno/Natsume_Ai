import React, { useState, useEffect, useCallback } from 'react'
import {
  fetchCanonProposals,
  fetchCanonDocuments,
  submitCanonProposal,
  approveCanonProposal,
  rejectCanonProposal,
  rollbackCanonProposal,
  fetchVaultStats,
} from '../api/obs.js'
import {
  BookOpen,
  FilePlus,
  CheckCircle,
  XCircle,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Lock,
  RotateCcw,
  Search,
} from 'lucide-react'

// ── 状态徽章 ──────────────────────────────────────────────────────────────
const STATUS_BADGE = {
  pending:  'bg-yellow-100 text-yellow-700 border-yellow-200',
  APPLIED:  'bg-green-100  text-green-700  border-green-200',
  rejected: 'bg-red-100    text-red-700    border-red-200',
  rolled_back: 'bg-gray-100 text-gray-600  border-gray-200',
}

function StatusBadge({ status }) {
  const cls = STATUS_BADGE[status] || 'bg-gray-100 text-gray-500 border-gray-200'
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}

// ── 提案行 ────────────────────────────────────────────────────────────────
function ProposalRow({ item, onApprove, onReject, onRollback }) {
  const [expanded, setExpanded] = useState(false)
  const ts = item.ts_ms ? new Date(item.ts_ms).toLocaleString('zh-CN') : '—'

  return (
    <>
      <tr className="hover:bg-gray-50 transition-colors">
        <td className="px-4 py-2 text-xs text-gray-500 font-mono max-w-[120px] truncate" title={item.proposal_id}>
          {item.proposal_id?.slice(0, 16)}…
        </td>
        <td className="px-4 py-2 text-xs text-gray-700 max-w-[120px] truncate" title={item.target_doc_id}>
          {item.target_doc_id || '—'}
        </td>
        <td className="px-4 py-2"><StatusBadge status={item.status} /></td>
        <td className="px-4 py-2 text-xs text-gray-500 max-w-[200px] truncate" title={item.summary}>
          {item.summary || '—'}
        </td>
        <td className="px-4 py-2 text-xs text-gray-400">{ts}</td>
        <td className="px-4 py-2">
          <div className="flex items-center gap-1">
            {item.status === 'pending' && (
              <>
                <button
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-green-50 text-green-700 border border-green-200 hover:bg-green-100 transition-colors"
                  onClick={() => onApprove(item)}
                  title="审核通过"
                >
                  <CheckCircle size={12} /> 通过
                </button>
                <button
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-red-50 text-red-700 border border-red-200 hover:bg-red-100 transition-colors"
                  onClick={() => onReject(item)}
                  title="拒绝提案"
                >
                  <XCircle size={12} /> 拒绝
                </button>
              </>
            )}
            {/* TODO-81: 回滚按钮（仅 APPLIED 状态） */}
            {item.status === 'APPLIED' && (
              <button
                className="flex items-center gap-1 px-2 py-1 rounded text-xs bg-orange-50 text-orange-700 border border-orange-200 hover:bg-orange-100 transition-colors"
                onClick={() => onRollback(item)}
                title="回滚到 base_version"
              >
                <RotateCcw size={12} /> 回滚
              </button>
            )}
            <button
              className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              onClick={() => setExpanded(v => !v)}
              title={expanded ? '收起' : '展开详情'}
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-50">
          <td colSpan={6} className="px-6 py-3">
            <div className="text-xs text-gray-600 space-y-1">
              <div><span className="font-medium text-gray-700">提案 ID：</span>{item.proposal_id}</div>
              <div><span className="font-medium text-gray-700">目标文档：</span>{item.target_doc_id}</div>
              {item.proposer_node_id && (
                <div><span className="font-medium text-gray-700">提案来源：</span>{item.proposer_node_id}</div>
              )}
              {item.proposed_hash && (
                <div><span className="font-medium text-gray-700">内容哈希：</span><span className="font-mono">{item.proposed_hash}</span></div>
              )}
              {item.base_version != null && (
                <div><span className="font-medium text-gray-700">基础版本：</span>v{item.base_version} → v{item.proposed_version}</div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ── 提交提案弹窗 ──────────────────────────────────────────────────────────
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

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.target_doc_id.trim() || !form.proposed_title.trim() || !form.proposed_body.trim()) {
      setError('目标文档 ID、标题和正文为必填项')
      return
    }
    setLoading(true)
    setError('')
    try {
      await onSubmit({
        ...form,
        proposed_tags: form.proposed_tags.split(',').map(t => t.trim()).filter(Boolean),
      })
      onClose()
    } catch (err) {
      setError(err.message || '提交失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 p-6">
        <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
          <FilePlus size={16} className="text-primary" /> 提交新 Canon 提案
        </h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">提案 ID</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposal_id}
              onChange={e => setForm(f => ({ ...f, proposal_id: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">目标文档 ID <span className="text-red-500">*</span></label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.target_doc_id}
              onChange={e => setForm(f => ({ ...f, target_doc_id: e.target.value }))}
              placeholder="如：world_canon_001"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">标题 <span className="text-red-500">*</span></label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposed_title}
              onChange={e => setForm(f => ({ ...f, proposed_title: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">正文 <span className="text-red-500">*</span></label>
            <textarea
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200 resize-none"
              rows={4}
              value={form.proposed_body}
              onChange={e => setForm(f => ({ ...f, proposed_body: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">标签（逗号分隔）</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.proposed_tags}
              onChange={e => setForm(f => ({ ...f, proposed_tags: e.target.value }))}
              placeholder="如：character,world,lore"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">摘要（不超过 200 字）</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.summary}
              onChange={e => setForm(f => ({ ...f, summary: e.target.value.slice(0, 200) }))}
            />
          </div>
          {error && (
            <div className="flex items-center gap-1.5 text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
              <AlertCircle size={12} /> {error}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-1.5 rounded border border-gray-300 text-sm text-gray-600 hover:bg-gray-50">
              取消
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-1.5 rounded bg-primary text-white text-sm font-medium hover:bg-primary-600 disabled:opacity-50 flex items-center gap-1.5"
            >
              {loading && <RefreshCw size={12} className="animate-spin" />}
              提交提案
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── 文档卡片 ──────────────────────────────────────────────────────────────
function DocCard({ doc }) {
  const [expanded, setExpanded] = useState(false)
  const ts = doc.ts_ms ? new Date(doc.ts_ms).toLocaleString('zh-CN') : '—'
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white hover:shadow-sm transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-medium text-sm text-gray-800 truncate">{doc.title || doc.doc_id}</span>
            <span className="text-xs text-gray-400 font-mono">v{doc.version}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded border ${doc.status === 'active' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'}`}>
              {doc.status}
            </span>
          </div>
          <div className="text-xs text-gray-500 mb-1">
            ID: <span className="font-mono">{doc.doc_id}</span> · 更新于 {ts}
          </div>
          {doc.tags?.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-1">
              {doc.tags.map(tag => (
                <span key={tag} className="text-xs bg-blue-50 text-blue-600 border border-blue-100 rounded px-1.5 py-0.5">{tag}</span>
              ))}
            </div>
          )}
        </div>
        <button
          className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 flex-shrink-0"
          onClick={() => setExpanded(v => !v)}
        >
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>
      {expanded && (
        <div className="mt-2 pt-2 border-t border-gray-100">
          <pre className="text-xs text-gray-600 whitespace-pre-wrap break-words bg-gray-50 rounded p-2 max-h-40 overflow-y-auto">
            {doc.body || '（无正文）'}
          </pre>
          <div className="mt-1 text-xs text-gray-400">
            哈希：<span className="font-mono">{doc.hash?.slice(0, 16)}…</span>
            {doc.updated_by && <> · 更新者：{doc.updated_by}</>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────
export default function Canon() {
  const [proposals, setProposals] = useState([])
  const [documents, setDocuments] = useState([])
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [docsLoading, setDocsLoading] = useState(false)
  const [error, setError] = useState('')
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [activeTab, setActiveTab] = useState('proposals') // 'proposals' | 'documents'
  const [vaultData, setVaultData] = useState(null)        // TODO-44: Vault 统计
  const [docKeyword, setDocKeyword] = useState('')         // TODO-85: 文档搜索关键词
  const PAGE_SIZE = 20

  const loadProposals = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await fetchCanonProposals({ status: statusFilter || undefined, page, page_size: PAGE_SIZE })
      setProposals(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      setError(err.message || '加载提案失败')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, page])

  const loadDocuments = useCallback(async () => {
    setDocsLoading(true)
    try {
      const data = await fetchCanonDocuments()
      setDocuments(data.items || [])
    } catch (err) {
      setError(err.message || '加载文档失败')
    } finally {
      setDocsLoading(false)
    }
  }, [])

  // TODO-44: 加载 Vault 统计
  const loadVaultStats = useCallback(async () => {
    try {
      const data = await fetchVaultStats({ limit: 10 })
      setVaultData(data)
    } catch {
      // 降级：不影响主流程
    }
  }, [])

  useEffect(() => {
    if (activeTab === 'proposals') loadProposals()
    else loadDocuments()
    loadVaultStats()
  }, [activeTab, loadProposals, loadDocuments, loadVaultStats])

  const handleApprove = async (item) => {
    if (!window.confirm(`确认审核通过提案 ${item.proposal_id?.slice(0, 16)}…？`)) return
    try {
      await approveCanonProposal(item.proposal_id, {
        proposed_title: item.summary || item.proposal_id,
        proposed_tags: [],
        proposed_body: item.summary || '',
      })
      loadProposals()
    } catch (err) {
      alert(`审核失败：${err.message}`)
    }
  }

  const handleReject = async (item) => {
    const reason = window.prompt('请输入拒绝原因（可选）：')
    if (reason === null) return
    try {
      await rejectCanonProposal(item.proposal_id, { reason })
      loadProposals()
    } catch (err) {
      alert(`拒绝失败：${err.message}`)
    }
  }

  // TODO-81: 回滚已 APPLIED 的提案
  const handleRollback = async (item) => {
    const reason = window.prompt(`确认回滚提案 ${item.proposal_id?.slice(0, 16)}… 到 base_version？\n请输入回滚原因（可选）：`)
    if (reason === null) return
    try {
      await rollbackCanonProposal(item.proposal_id, { rollback_reason: reason })
      loadProposals()
    } catch (err) {
      alert(`回滚失败：${err.message}`)
    }
  }

  const handleSubmit = async (formData) => {
    await submitCanonProposal(formData)
    loadProposals()
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <BookOpen size={18} className="text-primary" /> Canon 管理
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">World Canon 提案审核 · SSOT 文档管理</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
            onClick={() => activeTab === 'proposals' ? loadProposals() : loadDocuments()}
          >
            <RefreshCw size={13} className={loading || docsLoading ? 'animate-spin' : ''} /> 刷新
          </button>
          <button
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-primary text-white text-sm font-medium hover:bg-primary-600 transition-colors"
            onClick={() => setShowSubmitModal(true)}
          >
            <FilePlus size={13} /> 提交提案
          </button>
        </div>
      </div>

      {/* TODO-44: Vault 统计侧边栏 */}
      {vaultData && (
        <div className="card p-4 border border-gray-200">
          <div className="flex items-center gap-2 mb-3">
            <Lock size={14} className="text-violet-600" />
            <h2 className="text-sm font-semibold text-violet-600">ArtifactVault Manifest 统计</h2>
            {vaultData.reversible_rate != null && (
              <span className="ml-auto text-xs text-gray-400">可逆比例 {vaultData.reversible_rate}%</span>
            )}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <div className="text-center">
              <div className="text-xl font-bold text-gray-700">{vaultData.total_manifests}</div>
              <div className="text-xs text-gray-400 mt-0.5">总 Manifest</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-green-600">{vaultData.active_count}</div>
              <div className="text-xs text-gray-400 mt-0.5">ACTIVE</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-orange-500">{vaultData.undone_count}</div>
              <div className="text-xs text-gray-400 mt-0.5">UNDONE</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-gray-400">{vaultData.gc_count}</div>
              <div className="text-xs text-gray-400 mt-0.5">GC'd</div>
            </div>
          </div>
          {/* 状态分布进度条 */}
          {vaultData.total_manifests > 0 && (
            <div className="mb-3">
              <div className="flex text-xs text-gray-400 mb-1 justify-between">
                <span>ACTIVE {Math.round(vaultData.active_count / vaultData.total_manifests * 100)}%</span>
                <span>UNDONE {Math.round(vaultData.undone_count / vaultData.total_manifests * 100)}%</span>
                <span>GC {Math.round(vaultData.gc_count / vaultData.total_manifests * 100)}%</span>
              </div>
              <div className="flex h-2 rounded overflow-hidden">
                <div className="bg-green-500" style={{ width: `${vaultData.active_count / vaultData.total_manifests * 100}%` }} />
                <div className="bg-orange-400" style={{ width: `${vaultData.undone_count / vaultData.total_manifests * 100}%` }} />
                <div className="bg-gray-300" style={{ width: `${vaultData.gc_count / vaultData.total_manifests * 100}%` }} />
              </div>
            </div>
          )}
          {/* 最近 Manifest 列表 */}
          {vaultData.recent_manifests?.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 border-b border-gray-100">
                  <tr>
                    {['Manifest ID', '类型', '状态', '可逆', '摘要'].map((h) => (
                      <th key={h} className="px-3 py-1.5 text-left font-medium text-gray-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {vaultData.recent_manifests.map((m, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-3 py-1.5 font-mono text-violet-600 truncate max-w-[100px]" title={m.manifest_id}>
                        {m.manifest_id?.slice(0, 12)}…
                      </td>
                      <td className="px-3 py-1.5 text-gray-500">{m.artifact_type || '—'}</td>
                      <td className="px-3 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded border font-medium ${
                          m.status === 'ACTIVE' ? 'bg-green-50 text-green-700 border-green-200' :
                          m.status === 'UNDONE' ? 'bg-orange-50 text-orange-700 border-orange-200' :
                          'bg-gray-100 text-gray-500 border-gray-200'
                        }`}>{m.status}</span>
                      </td>
                      <td className="px-3 py-1.5 text-center">
                        {m.is_reversible
                          ? <span className="text-green-600">✓</span>
                          : <span className="text-gray-300">—</span>
                        }
                      </td>
                      <td className="px-3 py-1.5 text-gray-400 truncate max-w-[120px]" title={m.summary}>
                        {m.summary || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {vaultData.total_manifests === 0 && (
            <div className="text-center py-4 text-gray-400 text-sm">暂无 Vault Manifest 记录</div>
          )}
        </div>
      )}

      {/* Tab 切换 */}
      <div className="flex border-b border-gray-200">
        {[
          { key: 'proposals', label: '提案列表' },
          { key: 'documents', label: 'SSOT 文档' },
        ].map(({ key, label }) => (
          <button
            key={key}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === key
                ? 'border-primary text-primary'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
            onClick={() => setActiveTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded px-4 py-2">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {/* 提案列表 */}
      {activeTab === 'proposals' && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          {/* 过滤栏 */}
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-3">
            <span className="text-xs text-gray-500">状态过滤：</span>
            {['', 'pending', 'APPLIED', 'rejected'].map(s => (
              <button
                key={s}
                className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                  statusFilter === s
                    ? 'bg-primary text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
                onClick={() => { setStatusFilter(s); setPage(1) }}
              >
                {s || '全部'}
              </button>
            ))}
            <span className="ml-auto text-xs text-gray-400">共 {total} 条</span>
          </div>

          {/* 表格 */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-100">
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">提案 ID</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">目标文档</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">状态</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">摘要</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">时间</th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {loading ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-400">加载中…</td></tr>
                ) : proposals.length === 0 ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-400">暂无提案</td></tr>
                ) : (
                  proposals.map(item => (
                    <ProposalRow key={item.proposal_id} item={item} onApprove={handleApprove} onReject={handleReject} onRollback={handleRollback} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* 分页 */}
          {totalPages > 1 && (
            <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
              <span className="text-xs text-gray-400">第 {page} / {totalPages} 页</span>
              <div className="flex gap-1">
                <button
                  className="px-2.5 py-1 rounded border border-gray-200 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  disabled={page <= 1}
                  onClick={() => setPage(p => p - 1)}
                >上一页</button>
                <button
                  className="px-2.5 py-1 rounded border border-gray-200 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => p + 1)}
                >下一页</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* SSOT 文档列表 */}
      {activeTab === 'documents' && (
        <div>
          {/* TODO-85: 文档搜索过滤 */}
          <div className="mb-3 relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-primary-200"
              placeholder="按标题或标签搜索文档…"
              value={docKeyword}
              onChange={e => setDocKeyword(e.target.value)}
            />
          </div>
          {docsLoading ? (
            <div className="text-center py-8 text-sm text-gray-400">加载中…</div>
          ) : documents.length === 0 ? (
            <div className="text-center py-8 text-sm text-gray-400">暂无 World Canon 文档</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {documents
                .filter(doc => {
                  if (!docKeyword.trim()) return true
                  const kw = docKeyword.trim().toLowerCase()
                  return (
                    (doc.title || '').toLowerCase().includes(kw) ||
                    (doc.doc_id || '').toLowerCase().includes(kw) ||
                    (doc.tags || []).some(t => t.toLowerCase().includes(kw))
                  )
                })
                .map(doc => <DocCard key={doc.doc_id} doc={doc} />)
              }
            </div>
          )}
        </div>
      )}

      {/* 提交提案弹窗 */}
      {showSubmitModal && (
        <SubmitModal onClose={() => setShowSubmitModal(false)} onSubmit={handleSubmit} />
      )}
    </div>
  )
}
