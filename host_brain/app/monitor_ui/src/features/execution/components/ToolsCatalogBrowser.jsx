import React from 'react'
import { Search } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { LoadingNotice } from '../../../shared/ui/LoadingNotice.jsx'
import { TOOL_AUTH_LEVEL_STYLES, TOOL_SIDE_EFFECT_STYLES } from '../../../shared/statusMeta.js'

function ToolCard({ tool, selected, onClick }) {
  const sideEffect = TOOL_SIDE_EFFECT_STYLES[tool.side_effect_level] || TOOL_SIDE_EFFECT_STYLES.READ
  const auth = TOOL_AUTH_LEVEL_STYLES[tool.auth_level] || TOOL_AUTH_LEVEL_STYLES.NONE

  return (
    <button
      type="button"
      onClick={() => onClick(tool)}
      className={`grid w-full grid-cols-1 gap-2 px-3 py-2.5 text-left transition-colors md:grid-cols-[minmax(0,1fr)_8rem_7.5rem] md:items-center ${
        selected
          ? 'bg-blue-50'
          : 'bg-white hover:bg-gray-50'
      }`}
    >
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-gray-800">{tool.name}</div>
        <div className="mt-0.5 truncate font-mono text-xs text-gray-500">{tool.id}</div>
        <div className="mt-1 line-clamp-1 text-xs text-gray-500">{tool.desc || '暂无描述'}</div>
      </div>
      <div className="flex flex-wrap gap-1 md:justify-start">
        <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${sideEffect.bg} ${sideEffect.text}`}>
          {sideEffect.label}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-xs ${auth.bg} ${auth.text}`}>{auth.label}</span>
      </div>
      <div className="flex min-w-0 flex-wrap gap-1 md:justify-end">
        {tool.domain_tags?.length > 0 ? (
          tool.domain_tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
              {tag}
            </span>
          ))
        ) : (
          <span className="text-xs text-gray-400">无标签</span>
        )}
      </div>
    </button>
  )
}

export default function ToolsCatalogBrowser({
  filter,
  filteredTools,
  loading,
  onFilterChange,
  onQueryChange,
  onSelectTool,
  query,
  readCount,
  selectedTool,
  tools,
  writeCount,
}) {
  return (
    <>
      <div className="rounded-xl border border-white/80 bg-white/86 p-3 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-gray-300 px-3 py-2 pl-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="搜索工具名或描述..."
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
            />
          </div>
          <div className="flex shrink-0 overflow-hidden rounded-lg border border-gray-200 bg-gray-50 p-1 text-sm">
            {[
              { key: 'ALL', label: `全部 (${tools.length})` },
              { key: 'READ', label: `只读（${readCount}）` },
              { key: 'WRITE', label: `写入（${writeCount}）` },
            ].map(({ key, label }) => (
              <button
                type="button"
                key={key}
                onClick={() => onFilterChange(key)}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
                  filter === key ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:bg-white/70 hover:text-gray-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
        <div className="grid grid-cols-1 gap-2 border-b border-gray-100 bg-gray-50 px-3 py-2 text-xs font-semibold text-gray-500 md:grid-cols-[minmax(0,1fr)_8rem_7.5rem]">
          <span>工具</span>
          <span>权限</span>
          <span className="md:text-right">标签</span>
        </div>
        <div className="max-h-[620px] divide-y divide-gray-100 overflow-y-auto">
          {loading && filteredTools.length === 0 ? (
            <LoadingNotice>正在同步工具目录...</LoadingNotice>
          ) : filteredTools.length === 0 ? (
            <EmptyStateCard title="暂无工具" hint="调整筛选条件后再试试。" className="p-6" />
          ) : (
            filteredTools.map((tool) => (
              <ToolCard
                key={tool.id}
                tool={tool}
                selected={selectedTool?.id === tool.id}
                onClick={onSelectTool}
              />
            ))
          )}
        </div>
      </div>
    </>
  )
}
