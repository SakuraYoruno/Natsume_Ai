import { useMemo, useState } from 'react'
import { BookOpen, Search } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../shared/ui/LoadingNotice.jsx'
import { formatDateTime } from '../../../../shared/formatters.js'
import {
  CANON_MANIFEST_STATUS_CLASSES,
  CANON_MANIFEST_STATUS_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../../../shared/statusMeta.js'

function DocCard({ doc }) {
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <BookOpen size={15} className="text-primary" />
            <h3 className="truncate text-sm font-semibold text-gray-800">{doc.title || doc.doc_id || '未命名文档'}</h3>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-gray-400">
            <span className="font-mono">{doc.doc_id || '—'}</span>
            <span>·</span>
            <span className="font-mono">v{doc.version}</span>
          </div>
        </div>
        <span
          className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
            resolveStatusClass(CANON_MANIFEST_STATUS_CLASSES, doc.status, 'border-green-200 bg-green-50 text-green-700')
          }`}
        >
          {resolveStatusLabel(CANON_MANIFEST_STATUS_LABELS, doc.status, 'ACTIVE')}
        </span>
      </div>

      {(doc.tags || []).length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {(doc.tags || []).map((tag) => (
            <span key={tag} className="rounded-full bg-primary-50 px-2 py-0.5 text-xs text-primary">
              {tag}
            </span>
          ))}
        </div>
      ) : null}

      <div className="rounded-lg bg-gray-50 p-3">
        <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-6 text-gray-600">
          {doc.body || '（无正文）'}
        </pre>
        <div className="mt-1 text-xs text-gray-400">
          哈希：<span className="font-mono">{doc.hash?.slice(0, 16) || '—'}</span>
          {doc.updated_by ? <> · 更新者：{doc.updated_by}</> : null}
          {doc.updated_at_ms ? <> · 更新时间：{formatDateTime(doc.updated_at_ms)}</> : null}
        </div>
      </div>
    </div>
  )
}

export default function CanonDocumentsSection({
  documents,
  loading,
  refreshing,
}) {
  const [docKeyword, setDocKeyword] = useState('')

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

      {loading || refreshing ? (
        <LoadingNotice>正在同步主设定文档…</LoadingNotice>
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
  )
}
