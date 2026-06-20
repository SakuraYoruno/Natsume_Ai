import React from 'react'
import { ShieldAlert } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../../../shared/ui/NoticeBanner.jsx'
import RenderValue from '../../../../../shared/ui/RenderValue.jsx'
import { TraceMiniStat as MiniStat, TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { TagList, TextPreviewCard } from '../TraceRequestSummaryPanels.jsx'
import { formatDateTime } from '../TraceRequestDetailModel.jsx'

export default function ImmuneReplayPanel({ replay, loading, error, requestId, onReload }) {
  const summary = replay?.summary || {}
  const decisions = Array.isArray(replay?.decisions) ? replay.decisions : []
  const reasonCodes = Array.isArray(summary?.reason_codes) ? summary.reason_codes : []
  const matchedRules = Array.isArray(summary?.matched_rules) ? summary.matched_rules : []

  return (
    <SectionCard
      icon={ShieldAlert}
      title="Immune 回放"
      description="按当前 request 回放免疫决策链，快速定位误拦截与规则命中。"
      aside={(
        <button className="btn-secondary text-xs" onClick={onReload} disabled={loading}>
          {loading ? '同步中...' : '刷新回放'}
        </button>
      )}
    >
      {loading ? <LoadingNotice>正在拉取 immune replay…</LoadingNotice> : null}
      {error ? <NoticeBanner variant="error" title="回放拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && !replay ? (
        <EmptyStateCard title="当前请求暂无 immune 回放" hint="若未命中免疫流程，这里会保持为空。" className="p-4" />
      ) : null}
      {replay ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MiniStat label="request_id" value={requestId || '-'} />
            <MiniStat label="会话" value={replay?.session_id || '-'} />
            <MiniStat label="首/末决策" value={`${summary?.first_decision || '-'} → ${summary?.last_decision || '-'}`} />
            <MiniStat label="事件数 / max_score" value={`${summary?.event_count ?? 0} / ${summary?.max_score ?? '-'}`} />
          </div>

          {replay?.input_preview ? (
            <TextPreviewCard
              label="输入预览"
              text={String(replay.input_preview || '')}
              emptyLabel="暂无输入预览"
            />
          ) : null}

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <TagList label="Reason Codes" items={reasonCodes} emptyLabel="暂无 reason code" tone="warn" />
            <TagList label="Matched Rules" items={matchedRules} emptyLabel="暂无规则命中" />
          </div>

          <div className="rounded-xl border border-primary-100/70 bg-white/74 p-4">
            <div className="mb-2 text-xs font-medium text-gray-500">决策时间线</div>
            {decisions.length === 0 ? (
              <div className="text-sm text-gray-400">暂无决策事件</div>
            ) : (
              <div className="space-y-2">
                {decisions.map((item, index) => (
                  <div key={`${item.ts_ms}-${item.event_type}-${index}`} className="rounded-lg border border-primary-100/70 bg-white/85 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                        <RenderValue value={item.event_type || '-'} />
                      </span>
                      <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs text-orange-700">
                        <RenderValue value={item.decision || '-'} />{item.risk_level ? <RenderValue value={`/${String(item.risk_level)}`} /> : ''}
                      </span>
                      <span className="text-xs text-gray-500">score={item.score ?? '-'}</span>
                      <span className="ml-auto text-xs text-gray-400">{formatDateTime(item.ts_ms)}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      <RenderValue value={item.stage || '-'} />{item.phase ? <RenderValue value={` · ${String(item.phase)}`} /> : ''}{item.direction ? <RenderValue value={` · ${String(item.direction)}`} /> : ''}
                    </div>
                    {Array.isArray(item.reason_codes) && item.reason_codes.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.reason_codes.slice(0, 4).map((code, reasonIndex) => (
                          <span key={`${item.ts_ms}-reason-${reasonIndex}`} className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                            <RenderValue value={code} />
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {Array.isArray(item.matched_rules) && item.matched_rules.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.matched_rules.slice(0, 3).map((rule, ruleIndex) => (
                          <span key={`${item.ts_ms}-rule-${ruleIndex}`} className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700">
                            <RenderValue value={rule} />
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </SectionCard>
  )
}
