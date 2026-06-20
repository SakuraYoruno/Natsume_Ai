import React from 'react'
import { Play } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { isPentestTool } from '../../../shared/pentestMeta.js'
import { TOOL_AUTH_LEVEL_STYLES, TOOL_SIDE_EFFECT_STYLES } from '../../../shared/statusMeta.js'
import ToolExecutePanel from './ToolExecutePanel.jsx'

export default function ToolDetailPanel({
  executeTarget,
  onCloseExecute,
  onToggleExecute,
  selectedTool,
}) {
  if (!selectedTool) {
    return <EmptyStateCard title="请选择一个工具" hint="左侧点击任意工具后可查看详情和执行入口。" />
  }

  const sideEffect = TOOL_SIDE_EFFECT_STYLES[selectedTool.side_effect_level] || TOOL_SIDE_EFFECT_STYLES.READ
  const auth = TOOL_AUTH_LEVEL_STYLES[selectedTool.auth_level] || TOOL_AUTH_LEVEL_STYLES.NONE
  const executeOpen = executeTarget?.id === selectedTool.id

  return (
    <>
      <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-800">{selectedTool.name}</h2>
            <div className="mt-0.5 font-mono text-xs text-gray-400">{selectedTool.id}</div>
          </div>
          <div className="flex gap-2">
            <span className={`rounded px-2 py-1 text-xs font-medium ${sideEffect.bg} ${sideEffect.text}`}>
              {sideEffect.label}
            </span>
            <span className={`rounded px-2 py-1 text-xs ${auth.bg} ${auth.text}`}>
              {auth.label}
            </span>
          </div>
        </div>

        <p className="text-sm text-gray-600">{selectedTool.desc || '暂无描述'}</p>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="rounded bg-gray-50 p-2">
            <div className="mb-0.5 text-gray-400">版本</div>
            <div className="font-mono text-gray-700">{selectedTool.version || '—'}</div>
          </div>
          <div className="rounded bg-gray-50 p-2">
            <div className="mb-0.5 text-gray-400">Schema 摘要</div>
            <div className="truncate font-mono text-gray-700">
              {selectedTool.schema_hash ? `${selectedTool.schema_hash.slice(0, 16)}...` : '—'}
            </div>
          </div>
        </div>

        {selectedTool.domain_tags?.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {selectedTool.domain_tags.map((tag) => (
              <span key={tag} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600">
                {tag}
              </span>
            ))}
          </div>
        ) : null}

        <button
          onClick={() => onToggleExecute(selectedTool)}
          className={`inline-flex w-full items-center justify-center gap-1.5 rounded border px-4 py-2 text-sm font-medium transition-colors ${
            isPentestTool(selectedTool)
              ? 'border-orange-300 text-orange-700 hover:bg-orange-50'
              : 'border-blue-300 text-blue-600 hover:bg-blue-50'
          }`}
        >
          <Play size={14} />
          {executeOpen
            ? '收起执行面板'
            : isPentestTool(selectedTool)
              ? '查看 Pentest Lab 入口'
              : '执行该工具'}
        </button>
      </div>

      {executeOpen ? (
        <ToolExecutePanel tool={selectedTool} onClose={onCloseExecute} />
      ) : null}
    </>
  )
}
