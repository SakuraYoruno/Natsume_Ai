import React from 'react'
import { ArrowUpRight, Fingerprint, HeartPulse, LockKeyhole } from 'lucide-react'
import { Link } from 'react-router-dom'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'

function Tags({ items, empty = '无' }) {
  if (!items?.length) return <span className="text-gray-400">{empty}</span>
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => <span key={item} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[11px] text-gray-600">{item}</span>)}
    </div>
  )
}

export default function GovernanceHealthSection({ model, queryError = null }) {
  if (!model?.available) return null
  const { degradation, identity } = model

  return (
    <section className="space-y-3" data-testid="governance-health-section">
      <div>
        <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-gray-800">
          <HeartPulse size={15} className="text-rose-500" />治理健康与身份连续性
        </h2>
        <p className="mt-1 text-xs text-gray-500">只读展示降级、恢复证据和身份差异；本页没有恢复或身份确认 mutation。</p>
      </div>
      {queryError ? <NoticeBanner variant="warning">治理快照刷新失败：{queryError.message}</NoticeBanner> : null}
      {model.error ? <NoticeBanner variant="error">{model.error}</NoticeBanner> : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {degradation ? (
          <article className="card space-y-3 p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-gray-800"><LockKeyhole size={14} />降级状态</h3>
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${degradation.highRiskLatched ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>
                {degradation.status} · 高风险锁存 {degradation.highRiskLatched ? '开启' : '关闭'}
              </span>
            </div>
            <div className="text-xs"><div className="mb-1 font-semibold text-gray-500">原因码</div><Tags items={degradation.reasonCodes} /></div>
            <div className="text-xs"><div className="mb-1 font-semibold text-gray-500">限制</div><Tags items={degradation.restrictions} /></div>
            <div className="text-xs"><div className="mb-1 font-semibold text-gray-500">受影响能力</div><Tags items={degradation.affectedCapabilities} /></div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-gray-700">恢复证据</span>
                <span className="font-semibold text-gray-500">{degradation.recovery.status} · eligible {degradation.recovery.eligible ? 'yes' : 'no'}</span>
              </div>
              <div className="mt-2"><Tags items={[...degradation.evidenceRefs, ...degradation.recovery.evidenceRefs]} empty="尚无可验证恢复证据" /></div>
              {degradation.recovery.href ? (
                <Link className="mt-2 inline-flex items-center gap-1 font-semibold text-primary hover:underline" to={degradation.recovery.href}>
                  打开恢复 Review <ArrowUpRight size={11} />
                </Link>
              ) : null}
            </div>
          </article>
        ) : null}

        {identity ? (
          <article className="card space-y-3 p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-gray-800"><Fingerprint size={14} />Identity diff</h3>
              <span className="rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-700">{identity.status} · {identity.diff.severity}</span>
            </div>
            <div className="grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded bg-gray-50 p-2"><div className="text-gray-400">Release</div><div className="mt-1 break-all font-mono text-gray-700">{identity.releaseRef || '—'}</div></div>
              <div className="rounded bg-gray-50 p-2"><div className="text-gray-400">Snapshot</div><div className="mt-1 break-all font-mono text-gray-700">{identity.currentSnapshotRef || '—'}</div></div>
            </div>
            <div className="space-y-2">
              {identity.diff.items.map((item, index) => (
                <div key={`${item.section}:${item.field}:${index}`} className="rounded-lg border border-gray-100 px-3 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2"><span className="font-semibold text-gray-700">{item.section}.{item.field}</span><span className={item.severity === 'MAJOR' ? 'font-semibold text-red-600' : 'text-gray-500'}>{item.severity}</span></div>
                  <div className="mt-1 font-mono text-gray-500">{item.reasonCode}</div>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-violet-100 bg-violet-50 p-3 text-xs">
              <span className="font-semibold text-violet-800">Review: {identity.review.status}</span>
              {!identity.available ? (
                <span className="font-semibold text-amber-700">Review 状态未知 / 无法判定</span>
              ) : identity.review.required && identity.review.href ? (
                <Link className="inline-flex items-center gap-1 font-semibold text-violet-700 hover:underline" to={identity.review.href}>
                  打开重大差异 Review <ArrowUpRight size={11} />
                </Link>
              ) : identity.diff.severity === 'MAJOR' ? (
                <span className="font-semibold text-red-700">缺少 durable Review，已阻断</span>
              ) : <span className="text-violet-500">无需确认</span>}
            </div>
            <p className="text-[11px] text-gray-400">private_body_included=false；仅显示字段级差异与引用，不渲染人格或记忆正文。</p>
          </article>
        ) : null}
      </div>
    </section>
  )
}
