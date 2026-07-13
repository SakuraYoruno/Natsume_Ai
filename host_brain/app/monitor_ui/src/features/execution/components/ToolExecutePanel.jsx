import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Play, X } from 'lucide-react'
import { executeTool } from '../api.js'
import JsonBlock from '../../../shared/ui/JsonBlock.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { isPentestTool } from '../../../shared/pentestMeta.js'
import { buildToolExecutionGate, buildToolExecutionRequest } from '../toolsCatalogModel.js'

function gateToneClass(tone) {
  return {
    green: 'border-green-100 bg-green-50 text-green-700',
    amber: 'border-amber-100 bg-amber-50 text-amber-700',
    orange: 'border-orange-100 bg-orange-50 text-orange-700',
    red: 'border-red-100 bg-red-50 text-red-700',
    gray: 'border-gray-100 bg-gray-50 text-gray-600',
  }[tone] || 'border-gray-100 bg-gray-50 text-gray-600'
}

export default function ToolExecutePanel({ tool, onClose }) {
  const [form, setForm] = useState({
    intent_id: `intent::manual::${Date.now()}`,
    session_id: 'manual_session',
    args_json: '{}',
    approval_token_ref: '',
  })
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const gate = useMemo(() => buildToolExecutionGate(tool, form), [form, tool])
  const executionRequest = useMemo(() => buildToolExecutionRequest(tool, form), [form, tool])

  if (isPentestTool(tool)) {
    return (
      <div className="space-y-3 rounded-lg border border-orange-200 bg-orange-50/80 p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-orange-800">Pentest 工具需要受控执行</h3>
          <button onClick={onClose} className="inline-flex h-7 w-7 items-center justify-center rounded text-orange-500 hover:bg-orange-100">
            <X size={15} />
          </button>
        </div>
        <p className="text-sm text-orange-700">
          {tool.id} 属于 Pentest 能力清单。旧 Pentest Lab 已退役；本页只展示元信息，安全态和证据请在 Security Immune 复核。
        </p>
        <Link
          to="/security/immune"
          className="inline-flex w-full items-center justify-center gap-1.5 rounded border border-orange-300 bg-white/85 px-4 py-2 text-sm font-semibold text-orange-700 hover:bg-orange-50"
        >
          打开 Security Immune
        </Link>
      </div>
    )
  }

  const handleExecute = async () => {
    setResult(null)
    if (!executionRequest.ok) {
      setResult({ ok: false, error: executionRequest.error })
      return
    }

    setLoading(true)
    try {
      const data = await executeTool(executionRequest.request)
      setResult({ ok: true, data })
    } catch (error) {
      setResult({ ok: false, error: error.message || '执行失败' })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">执行工具：{tool.name}</h3>
        <button onClick={onClose} className="inline-flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-50 hover:text-gray-600">
          <X size={15} />
        </button>
      </div>

      <div className={`rounded-lg border px-3 py-2 text-sm ${gateToneClass(gate.tone)}`}>
        <div className="font-semibold">Execution Gate：{gate.title}</div>
        <div className="mt-1 text-xs leading-5">{gate.detail}</div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-gray-500">意图 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.intent_id}
            onChange={(event) => setForm((prev) => ({ ...prev, intent_id: event.target.value }))}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-500">会话 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.session_id}
            onChange={(event) => setForm((prev) => ({ ...prev, session_id: event.target.value }))}
          />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-gray-500">参数（JSON）</label>
        <textarea
          className="h-20 w-full resize-none rounded border border-gray-300 px-2 py-1.5 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.args_json}
          onChange={(event) => setForm((prev) => ({ ...prev, args_json: event.target.value }))}
        />
      </div>

      {tool.side_effect_level === 'WRITE' ? (
        <div>
          <label className="mb-1 block text-xs text-gray-500">
            审批令牌引用 <span className="text-orange-500">（写入工具必填）</span>
          </label>
          <input
            className="w-full rounded border border-orange-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-orange-300"
            value={form.approval_token_ref}
            onChange={(event) => setForm((prev) => ({ ...prev, approval_token_ref: event.target.value }))}
            placeholder="approval_token_ref"
          />
        </div>
      ) : null}

      <button
        onClick={handleExecute}
        disabled={loading || !executionRequest.ok}
        className="inline-flex w-full items-center justify-center gap-1.5 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        <Play size={14} />
        {loading ? '执行中...' : '执行'}
      </button>

      {!executionRequest.ok ? (
        <div className="rounded border border-orange-100 bg-orange-50 px-3 py-2 text-xs leading-5 text-orange-700">
          {executionRequest.error}
        </div>
      ) : null}

      {result ? (
        result.ok ? (
          <NoticeBanner variant="success" title="执行完成">
            <JsonBlock
              data={result.data}
              variant="light"
              className="mt-1 border-green-200 bg-white/70 font-mono text-green-900"
            />
          </NoticeBanner>
        ) : (
          <NoticeBanner variant="error" title="执行失败">
            {result.error}
          </NoticeBanner>
        )
      ) : null}
    </div>
  )
}
