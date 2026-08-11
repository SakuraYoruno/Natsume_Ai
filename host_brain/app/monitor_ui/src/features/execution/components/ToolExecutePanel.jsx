import React from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, X } from 'lucide-react'
import { getToolExecutionAvailability } from '../api.js'
import { isPentestTool } from '../../../shared/pentestMeta.js'

export default function ToolExecutePanel({ tool, onClose }) {
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

  const availability = getToolExecutionAvailability()

  return (
    <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50/70 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-amber-900">工具执行已迁移：{tool.name}</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭工具执行说明"
          className="inline-flex h-8 w-8 items-center justify-center rounded text-amber-600 hover:bg-amber-100"
        >
          <X size={15} />
        </button>
      </div>
      <p className="text-sm leading-6 text-amber-800">
        旧的人工直执行命令 <code>{availability.commandId || 'tool.execute'}</code> 已退役。
        工具目录仍可浏览，但真正执行必须由 {availability.replacementAuthority || '受治理 TaskGraph'} 创建任务、绑定权限并产生回执。
      </p>
      <Link
        to="/execution/task-runtime"
        className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white/85 px-4 py-2 text-sm font-semibold text-amber-800 hover:bg-amber-100"
      >
        <GitBranch size={15} />
        打开执行审查
      </Link>
    </div>
  )
}
