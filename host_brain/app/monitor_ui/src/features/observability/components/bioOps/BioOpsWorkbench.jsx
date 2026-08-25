import { useState } from 'react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { BIO_OPS_STATUS_LABELS, resolveStatusLabel } from '../../../../shared/statusMeta.js'
import { StatusPill } from './BioOpsSurfaces.jsx'

export function CapabilityWorkbench({ sections }) {
  const items = Array.isArray(sections) ? sections.filter(Boolean) : []
  const [selectedKey, setSelectedKey] = useState(items[0]?.key || '')
  const selected = items.find((item) => item.key === selectedKey) || items[0]
  if (!selected) {
    return <EmptyStateCard title="暂无 BioOps 能力层数据" hint="等待 BioOps catalog 注册或日循环产物" />
  }
  const ActivePanel = selected.Panel
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="overflow-hidden rounded-lg border border-white/80 bg-white/86 shadow-sm">
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="text-sm font-semibold text-gray-900">能力层</div>
          <div className="mt-0.5 text-xs text-gray-500">{items.length} 个分区 · 当前 {selected.title}</div>
        </div>
        <div className="max-h-[560px] divide-y divide-gray-100 overflow-y-auto">
          {items.map((item) => {
            const active = item.key === selected.key
            return (
              <button
                type="button"
                key={item.key}
                className={`w-full px-4 py-3 text-left transition-colors ${
                  active ? 'bg-primary-50' : 'bg-white hover:bg-gray-50'
                }`}
                onClick={() => setSelectedKey(item.key)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-gray-900">{item.title}</div>
                    <div className="mt-0.5 truncate text-xs text-gray-500">{item.hint}</div>
                  </div>
                  <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs font-semibold text-gray-600">
                    {item.count ?? '—'}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(item.statusCounts || {}).slice(0, 3).map(([status, count]) => (
                    <span key={status} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600">
                      {resolveStatusLabel(BIO_OPS_STATUS_LABELS, status, status)}:{count}
                    </span>
                  ))}
                </div>
              </button>
            )
          })}
        </div>
      </div>
      <div className="min-w-0 space-y-3">
        <div className="rounded-lg border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-gray-900">{selected.title}</h3>
              <p className="mt-1 text-sm leading-6 text-gray-600">{selected.subtitle}</p>
            </div>
            <StatusPill status={selected.status || 'REPORT_ONLY'} />
          </div>
        </div>
        <ActivePanel summary={selected.summary} />
      </div>
    </div>
  )
}
