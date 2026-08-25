import { useState } from 'react'
import { FilePlus } from 'lucide-react'

export default function CanonSubmitModal({ onClose, onSubmit }) {
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
            <label className="mb-1 block text-xs font-medium text-gray-600">摘要</label>
            <input
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary-200"
              value={form.summary}
              onChange={(event) => setForm((current) => ({ ...current, summary: event.target.value }))}
            />
          </div>

          {error ? <div className="rounded border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div> : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-50"
              onClick={onClose}
              disabled={loading}
            >
              取消
            </button>
            <button
              type="submit"
              className="rounded bg-primary px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary-600 disabled:opacity-50"
              disabled={loading}
            >
              {loading ? '提交中…' : '提交提案'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
