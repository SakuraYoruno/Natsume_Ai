import { GitMerge, ScrollText } from 'lucide-react'
import InsightSection from './InsightSection.jsx'
import {
  useCanonProposalStatsQuery,
  useStateArbiterStatsQuery,
} from '../../diagnosticQueries.js'
import { formatDateTime } from '../../../../shared/formatters.js'
import { SectionQueryMeta, StatCard } from './insightsGroupShared.jsx'

const RECENT_PROPOSAL_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

function StateArbiterStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useStateArbiterStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={GitMerge} title="状态仲裁关系查询统计" color="text-cyan-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total_resolutions', 'top_sources']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总查询次数" value={data?.total_resolutions ?? '—'} color="text-gray-700" />
        <StatCard label="冲突次数" value={data?.conflict_count ?? '—'} color="text-red-600" />
        <StatCard label="降级次数" value={data?.fallback_count ?? '—'} color="text-orange-500" />
        <StatCard label="关系快照" value={data?.session_override_count ?? '—'} color="text-blue-600" />
      </div>
      {(data?.top_sources ?? []).length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">状态源命中分布</div>
          <div className="space-y-2">
            {(data?.top_sources ?? []).map((s) => (
              <div key={s.source} className="flex items-center gap-2">
                <div className="text-xs font-mono text-gray-600 w-36 truncate">{s.source}</div>
                <div className="flex-1 bg-gray-100 rounded-full h-2">
                  <div className="bg-cyan-400 h-2 rounded-full" style={{ width: `${s.pct}%` }} />
                </div>
                <div className="text-xs text-gray-500 w-10 text-right">{s.hit_count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </InsightSection>
  )
}

function CanonProposalStatsSection({ windowMinutes, pollingEnabled = false, pollingInterval = 30, pollingTick = 0 }) {
  const queryState = useCanonProposalStatsQuery({ windowMinutes, pollingEnabled, pollingInterval, pollingTick })
  const { statsData: data } = queryState
  return (
    <InsightSection icon={ScrollText} title="主设定提案统计" color="text-indigo-600">
      <SectionQueryMeta queryState={queryState} windowMinutes={windowMinutes} sampleKeys={['total', 'recent_proposals', 'status_distribution']} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
        <StatCard label="总提案数" value={data?.total ?? '—'} color="text-gray-700" />
        <StatCard label="待审核" value={data?.pending ?? '—'} color={data?.pending > 0 ? 'text-orange-500' : 'text-gray-400'} />
        <StatCard label="已通过" value={data?.applied ?? '—'} color="text-green-600" />
        <StatCard label="已拒绝" value={data?.rejected ?? '—'} color="text-red-600" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">提案通过率</div>
          <div className="flex items-center gap-3">
            <div className="flex-1 bg-gray-100 rounded-full h-3">
              <div
                className="bg-indigo-400 h-3 rounded-full transition-all"
                style={{ width: `${Math.min(data?.approval_rate ?? 0, 100)}%` }}
              />
            </div>
            <span className={`text-sm font-bold ${(data?.approval_rate ?? 0) >= 70 ? 'text-green-600' : 'text-orange-500'}`}>
              {data?.approval_rate != null ? `${data.approval_rate}%` : '—'}
            </span>
          </div>
          {data?.pending > 0 && (
            <div className="mt-2 text-xs text-orange-500 flex items-center gap-1">
              ⚠ 有 {data.pending} 条提案待审核
            </div>
          )}
        </div>
        <div className="card p-4">
          <div className="text-xs font-semibold text-gray-600 mb-3">状态分布</div>
          <div className="space-y-2">
            {(data?.status_distribution ?? []).map((s) => {
              const colorMap = { pending: 'bg-orange-400', applied: 'bg-green-400', rejected: 'bg-red-400' }
              return (
                <div key={s.status} className="flex items-center gap-2">
                  <div className="text-xs text-gray-600 w-16 capitalize">{s.status}</div>
                  <div className="flex-1 bg-gray-100 rounded-full h-2">
                    <div className={`${colorMap[s.status] ?? 'bg-gray-400'} h-2 rounded-full`} style={{ width: `${s.pct}%` }} />
                  </div>
                  <div className="text-xs text-gray-500 w-12 text-right">{s.count} ({s.pct}%)</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {(data?.recent_proposals ?? []).length > 0 && (
        <div className="card overflow-hidden">
          <div className="px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase tracking-wide">
            最近提案（近 {windowMinutes} 分钟）
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['提案 ID', '目标文档', '状态', '提案者', '时间'].map((h) => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(data?.recent_proposals ?? []).map((p) => {
                const statusColor = { pending: 'text-orange-500', APPLIED: 'text-green-600', rejected: 'text-red-600' }
                return (
                  <tr key={p.proposal_id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-xs font-mono text-indigo-600 truncate max-w-32" title={p.proposal_id}>
                      {p.proposal_id?.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-600 truncate max-w-28" title={p.target_doc_id}>
                      {p.target_doc_id}
                    </td>
                    <td className={`px-4 py-2 text-xs font-medium ${statusColor[p.status] ?? 'text-gray-500'}`}>
                      {p.status}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-28">
                      {p.proposer_node_id ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400 whitespace-nowrap">
                      {formatDateTime(p.ts_ms, { options: RECENT_PROPOSAL_TIME_OPTIONS })}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </InsightSection>
  )
}

export default function InsightsStateGroup({
  windowMinutes,
  pollingEnabled = false,
  pollingInterval = 30,
  pollingTick = 0,
}) {
  const deepQueryProps = { pollingEnabled, pollingInterval, pollingTick }

  return (
    <>
      <StateArbiterStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
      <CanonProposalStatsSection windowMinutes={windowMinutes} {...deepQueryProps} />
    </>
  )
}
