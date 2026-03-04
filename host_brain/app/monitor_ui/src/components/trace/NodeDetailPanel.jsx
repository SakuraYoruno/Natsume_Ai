import React, { useState } from 'react'
import { X, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'
import StatusBadge from '../StatusBadge.jsx'

// ===== JSON 格式化展示（折叠/展开） =====
function JsonViewer({ data, maxPreviewLen = 300 }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  if (data == null) return <span className="text-gray-400 text-xs italic">null</span>

  const str = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const isLong = str.length > maxPreviewLen
  const preview = isLong && !expanded ? str.slice(0, maxPreviewLen) + '…' : str

  const handleCopy = () => {
    navigator.clipboard.writeText(str).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="relative font-mono text-xs bg-gray-50 rounded border border-gray-200 overflow-hidden">
      {/* 复制按钮 */}
      <button
        className="absolute top-1.5 right-1.5 p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors"
        onClick={handleCopy}
        title="复制"
      >
        {copied ? <Check size={11} className="text-green-500" /> : <Copy size={11} />}
      </button>

      <pre className="p-2 pr-8 whitespace-pre-wrap break-all text-gray-700 leading-relaxed max-h-64 overflow-y-auto">
        {preview}
      </pre>

      {isLong && (
        <button
          className="flex items-center gap-1 px-2 py-1 w-full text-xs text-primary hover:bg-gray-100 border-t border-gray-200 transition-colors"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          {expanded ? '收起' : `展开全部 (${str.length} 字符)`}
        </button>
      )}
    </div>
  )
}

// ===== 信息行 =====
function InfoRow({ label, value }) {
  if (value == null || value === '') return null
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="text-gray-500 shrink-0 w-20 text-right">{label}</span>
      <span className="text-gray-800 break-all">{String(value)}</span>
    </div>
  )
}

// ===== 节点详情侧边面板 =====
export default function NodeDetailPanel({ node, onClose }) {
  if (!node) return null

  const statusForBadge = {
    OK: 'healthy', UP: 'healthy', success: 'healthy', healthy: 'healthy',
    DEGRADED: 'degraded', degraded: 'degraded',
    FAIL: 'error', DOWN: 'error', error: 'error',
    RUNNING: 'running', running: 'running',
  }[node.status] ?? 'unknown'

  const duration = node.latency_ms != null
    ? `${node.latency_ms}ms`
    : (node.duration_ms != null ? `${node.duration_ms}ms` : null)

  return (
    <div className="w-80 shrink-0 card flex flex-col overflow-hidden h-full">
      {/* 面板头 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-semibold text-gray-800 text-sm truncate">
            {node.display_name || node.label || node.node_id}
          </span>
          <StatusBadge status={statusForBadge} />
        </div>
        <button className="btn-ghost p-1 shrink-0" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      {/* 面板内容 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* 基础信息 */}
        <div className="space-y-1.5">
          <InfoRow label="节点 ID"   value={node.node_id} />
          <InfoRow label="类型"      value={node.node_type} />
          <InfoRow label="侧"        value={node.side} />
          <InfoRow label="耗时"      value={duration} />
          <InfoRow label="Provider"  value={node.provider} />
          <InfoRow label="模型"      value={node.model_id} />
          <InfoRow label="拒绝码"    value={node.reason_code} />
          <InfoRow label="时间戳"    value={node.ts_ms ? new Date(node.ts_ms).toLocaleString() : null} />
        </div>

        {/* 错误信息 */}
        {node.error && (
          <div>
            <div className="text-xs font-medium text-red-500 mb-1">错误</div>
            <div className="text-xs text-red-600 bg-red-50 rounded p-2 border border-red-200">
              {node.error}
            </div>
          </div>
        )}

        {/* Input */}
        {(node.input_data != null || node.input != null) && (
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1.5 flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />
              Input
            </div>
            <JsonViewer data={node.input_data ?? node.input} />
          </div>
        )}

        {/* Output */}
        {(node.output_data != null || node.output != null) && (
          <div>
            <div className="text-xs font-medium text-gray-500 mb-1.5 flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-green-400 inline-block" />
              Output
            </div>
            <JsonViewer data={node.output_data ?? node.output} />
          </div>
        )}
      </div>
    </div>
  )
}
