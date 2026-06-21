import React from 'react'
import { ChevronRight, GitBranch, Layers, RefreshCw } from 'lucide-react'
import { FULL_CHAIN_SCHEMA } from './traceFlowModel.js'

export default function TraceAdvancedDrilldownPanel({
  advancedViewsExpanded,
  groupStats,
  navigate,
  setAdvancedViewsExpanded,
  traceData,
  traceInfo,
}) {
  return (
    <div className="card overflow-hidden rounded-2xl">
      <button
        className="w-full px-4 py-4 text-left transition-colors hover:bg-primary-50/45"
        onClick={() => setAdvancedViewsExpanded((value) => !value)}
      >
        <div className="flex items-center gap-3">
          <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
            <GitBranch size={16} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-gray-900">高级钻取视图</div>
            <div className="text-xs text-gray-500">只有需要按分组拆开排查时再展开。</div>
          </div>
          <span className="text-xs font-medium text-blue-600">{advancedViewsExpanded ? '收起' : '展开'}</span>
        </div>
      </button>

      {advancedViewsExpanded && (
        <div className="space-y-4 border-t border-primary-100/70 bg-white/75 p-4">
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            {groupStats.map((g) => {
              const Icon = g.icon
              const isActive = g.activeCount > 0
              return (
                <button
                  key={g.id}
                  className="text-left rounded-xl border-2 p-4 transition-all hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer"
                  style={{
                    borderColor: isActive ? g.color : g.border,
                    background: isActive ? g.bg : '#FFFFFF',
                    boxShadow: isActive ? `0 0 0 1px ${g.color}22, 0 2px 8px ${g.color}11` : undefined,
                  }}
                  onClick={() => navigate(`/observability/trace/group/${g.id}`)}
                >
                  <div className="flex items-start justify-between mb-3">
                    <span
                      className="flex items-center justify-center w-9 h-9 rounded-lg"
                      style={{ background: isActive ? g.color : g.bg, color: isActive ? '#fff' : g.color }}
                    >
                      <Icon size={18} />
                    </span>
                    <div className="flex items-center gap-1.5">
                      {g.hasError && (
                        <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-200 font-medium">
                          异常
                        </span>
                      )}
                      {isActive && (
                        <span
                          className="text-xs px-1.5 py-0.5 rounded-full font-medium"
                          style={{ background: g.bg, color: g.color, border: `1px solid ${g.border}` }}
                        >
                          {g.activeCount}/{g.totalNodeCount}
                        </span>
                      )}
                      <ChevronRight size={14} className="text-gray-400" />
                    </div>
                  </div>
                  <div className="font-semibold text-gray-900 text-sm mb-1">{g.label}</div>
                  <div className="text-xs text-gray-500 leading-relaxed line-clamp-2">{g.desc}</div>
                  {g.id === 'immune' && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {g.immuneMeta?.decision || g.immuneMeta?.riskLevel ? (
                        <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700">
                          {(g.immuneMeta?.decision || 'IMMUNE').toUpperCase()}
                          {g.immuneMeta?.riskLevel ? `/${String(g.immuneMeta.riskLevel).toLowerCase()}` : ''}
                        </span>
                      ) : null}
                      {(g.immuneMeta?.reasonTags || []).slice(0, 2).map((reasonCode) => (
                        <span key={`${g.id}-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                          {reasonCode}
                        </span>
                      ))}
                    </div>
                  )}
                  {traceData && (
                    <div className="mt-3 h-1 overflow-hidden rounded-full bg-primary-100/70">
                      <div
                        className="h-1 rounded-full transition-all"
                        style={{ width: `${(g.activeCount / Math.max(g.totalNodeCount, 1)) * 100}%`, background: g.color }}
                      />
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_18rem] gap-4">
            <button
              className="flex w-full items-center gap-4 rounded-xl border-2 border-dashed border-primary-200 p-4 text-left transition-all hover:border-blue-400 hover:bg-blue-50"
              onClick={() => navigate('/observability/trace/full')}
            >
              <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 shrink-0">
                <Layers size={20} />
              </span>
              <div className="flex-1">
                <div className="font-semibold text-gray-800 text-sm">双端全链路拓扑图</div>
                <div className="text-xs text-gray-500 mt-0.5">需要单独放大排查静态拓扑图时再进入。</div>
              </div>
              <ChevronRight size={16} className="text-gray-400 shrink-0" />
            </button>

            <div className="grid grid-cols-2 gap-3">
              {[
                { label: '总节点数', value: FULL_CHAIN_SCHEMA.nodes.length, color: 'text-blue-600' },
                { label: '总边数', value: FULL_CHAIN_SCHEMA.edges.length, color: 'text-indigo-600' },
                { label: '激活节点', value: traceData ? (traceData.nodes ?? []).length : '—', color: 'text-green-600' },
                { label: '总耗时', value: traceInfo?.latency != null ? `${traceInfo.latency}ms` : '—', color: 'text-orange-500' },
              ].map(({ label, value, color }) => (
                <div key={label} className="rounded-xl border border-primary-100/70 bg-white/90 p-4 text-center">
                  <div className={`text-2xl font-bold ${color}`}>{value}</div>
                  <div className="text-xs text-gray-500 mt-1">{label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
