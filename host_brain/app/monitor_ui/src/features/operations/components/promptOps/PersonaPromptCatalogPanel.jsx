import { Wand2 } from 'lucide-react'

import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import {
  getPromptContentTypeBadgeView,
  getPromptSourceBadgeView,
} from '../../promptOpsModel.js'

export function SourceBadge({ source }) {
  const view = getPromptSourceBadgeView(source, { resolveLabel: true })
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${view.className}`}>{view.label}</span>
}

export function TypeBadge({ value }) {
  const view = getPromptContentTypeBadgeView(value, { resolveLabel: true })
  return <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium ${view.className}`}>{view.label}</span>
}

export default function PersonaPromptCatalogPanel({
  bundles,
  catalogLoading,
  catalogRefreshing,
  entries,
  onRefreshCatalog,
  onSelect,
  selectedId,
}) {
  return (
    <div className="card p-4 space-y-4">
      <div className="rounded-xl border border-primary-100 bg-primary-50 p-3 text-sm text-primary-900">
        <div className="flex items-center gap-2 font-medium">
          <Wand2 size={15} />
          当前页聚焦首批高价值人格模板
        </div>
        <div className="mt-2 text-xs leading-6 text-primary-800">
          当前只做只读核对：优先观察语气、温度、关系感和节奏感是否与 YAML 设定一致。
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>{catalogLoading ? '正在加载目录…' : catalogRefreshing ? '正在刷新目录…' : `共 ${entries.length} 个模板`}</span>
        <button className="text-primary hover:underline" onClick={() => void onRefreshCatalog()}>刷新目录</button>
      </div>

      <div className="space-y-3 max-h-[72vh] overflow-y-auto">
        {bundles.map((bundle) => (
          <div key={bundle.bundle_id} className="rounded-xl border border-gray-200 bg-white">
            <div className="px-3 py-3 border-b border-gray-100">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-semibold text-gray-800">{bundle.title}</div>
                <span className="text-[11px] text-gray-400">{bundle.template_count} 项</span>
              </div>
              <div className="mt-1 text-xs text-gray-500 leading-5">{bundle.description}</div>
            </div>
            <div className="p-2 space-y-2">
              {(bundle.entries || []).map((entry) => {
                const active = entry.template_id === selectedId
                return (
                  <button
                    key={entry.template_id}
                    className={`w-full text-left rounded-lg border px-3 py-3 transition-colors ${
                      active ? 'border-primary bg-primary-50' : 'border-gray-200 hover:bg-gray-50'
                    }`}
                    onClick={() => onSelect(entry.template_id)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-gray-800">{entry.title}</div>
                        <div className="mt-1 text-[11px] font-mono text-gray-500 break-all">
                          {entry.template_id}
                        </div>
                      </div>
                      <SourceBadge source={entry.item?.source || 'default'} />
                    </div>
                    <div className="mt-2 text-xs text-gray-500 leading-5">{entry.description}</div>
                    <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-400">
                      <span>{entry.item?.scope}</span>
                      <span>·</span>
                      <span>{entry.item?.category}</span>
                      <span>·</span>
                      <TypeBadge value={entry.item?.content_type || 'text'} />
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        ))}
        {!catalogLoading && bundles.length === 0 && (
          <EmptyStateCard title="暂无人格模板" hint="等待目录同步完成后重试。" className="p-4" />
        )}
      </div>
    </div>
  )
}
