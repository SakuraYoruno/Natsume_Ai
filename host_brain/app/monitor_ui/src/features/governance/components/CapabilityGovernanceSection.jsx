import React from 'react'
import { ArrowUpRight, Gauge, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'

function RefList({ refs }) {
  if (!refs.length) return <span className="text-gray-400">无公开证据引用</span>
  return <span className="break-all text-gray-500">{refs.join(' · ')}</span>
}

export default function CapabilityGovernanceSection({ model, queryError = null }) {
  if (!model?.available) return null

  return (
    <section className="space-y-3" data-testid="capability-governance-section">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-gray-800">
            <Gauge size={15} className="text-indigo-500" />
            能力成熟度与静态风险
          </h2>
          <p className="mt-1 text-xs text-gray-500">联邦只读评分；升级只能进入 MASTER Review，不提供自动升级操作。</p>
        </div>
        <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${model.automaticUpgradeAvailable ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
          自动升级：{model.automaticUpgradeAvailable ? '异常 / 已阻断' : '关闭'}
        </span>
      </div>

      {queryError ? <NoticeBanner variant="warning">治理快照刷新失败：{queryError.message}</NoticeBanner> : null}
      {model.error ? <NoticeBanner variant="error">{model.error}</NoticeBanner> : null}

      {model.capabilities.length > 0 ? (
        <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
          <div className="hidden grid-cols-[minmax(0,1.4fr)_0.55fr_0.55fr_0.55fr_minmax(0,1fr)] gap-3 border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-semibold text-gray-500 md:grid">
            <span>能力 / 来源</span><span>成熟度</span><span>静态上限</span><span>静态风险</span><span>升级候选</span>
          </div>
          <div className="divide-y divide-gray-100">
            {model.capabilities.map((item) => (
              <div key={`${item.sourceKind}:${item.capabilityId}`} className="grid grid-cols-1 gap-2 px-4 py-3 text-sm md:grid-cols-[minmax(0,1.4fr)_0.55fr_0.55fr_0.55fr_minmax(0,1fr)] md:items-center md:gap-3">
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs font-semibold text-gray-800">{item.capabilityId}</div>
                  <div className="mt-1 text-xs text-gray-400">{item.sourceKind}</div>
                </div>
                <div>
                  <span className="rounded bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700">{item.maturityCode} · {item.maturityLabel}</span>
                </div>
                <div className="text-xs font-semibold text-gray-600">{item.staticMaturityCeilingCode}</div>
                <div className={`inline-flex items-center gap-1 text-xs font-semibold ${item.canonicalRisk === null ? 'text-gray-500' : item.canonicalRisk >= 4 ? 'text-red-600' : item.canonicalRisk >= 3 ? 'text-amber-600' : 'text-emerald-600'}`}>
                  <ShieldCheck size={12} />{item.canonicalRisk === null ? 'UNKNOWN' : `R${item.canonicalRisk}`}
                </div>
                <div className="min-w-0 text-xs">
                  {item.upgradeCandidate.href ? (
                    <Link className="inline-flex items-center gap-1 font-semibold text-primary hover:underline" to={item.upgradeCandidate.href}>
                      {item.upgradeCandidate.status}<ArrowUpRight size={11} />
                    </Link>
                  ) : <span className="text-gray-400">{item.upgradeCandidate.status}</span>}
                  <div className="mt-1 truncate" title={item.evidenceRefs.join(' · ')}><RefList refs={item.evidenceRefs} /></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  )
}
