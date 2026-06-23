import { LOAD_STATES } from '../../../../models/loadState.js'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'

function resolveReplayLoadState({
  activeReplayRequestId,
  replayLoading,
  replayRefreshing,
  replayPayload,
  replayError,
}) {
  if (!activeReplayRequestId) return LOAD_STATES.UNAVAILABLE
  if (replayLoading || replayRefreshing) return LOAD_STATES.LOADING
  if (replayPayload) return LOAD_STATES.READY
  if (replayError) return LOAD_STATES.ERROR
  return LOAD_STATES.UNAVAILABLE
}

export default function ImmuneGateReplayPanel({
  activeReplayRequestId,
  replayLoading,
  replayRefreshing,
  replayPayload,
  replayError,
  replayUpdatedAt,
}) {
  if (!replayLoading && !replayRefreshing && !replayPayload && !activeReplayRequestId) return null

  return (
    <div className="rounded-xl border border-violet-200 bg-violet-50/40 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-violet-800">免疫回放</div>
        {replayPayload?.request_id ? (
          <a
            href={`#/observability/trace/${encodeURIComponent(replayPayload.request_id)}`}
            className="btn-secondary border-blue-200 px-2 py-0.5 text-[11px] text-blue-700"
          >
            打开链路
          </a>
        ) : null}
      </div>
      <div className="mb-2">
        <SampleMeta
          loadState={resolveReplayLoadState({
            activeReplayRequestId,
            replayLoading,
            replayRefreshing,
            replayPayload,
            replayError,
          })}
          sampleSize={replayPayload?.summary?.event_count ?? 0}
          sampleLabel="回放事件"
          updatedAt={replayUpdatedAt}
          source={activeReplayRequestId ? `request ${activeReplayRequestId}` : 'immune replay'}
          definition="指定 request 的 immune 决策时间线，用于回看风险判定、命中规则和阶段演化。"
        />
      </div>
      {replayLoading && !replayPayload ? (
        <div className="text-xs text-gray-500">正在加载回放时间线…</div>
      ) : !replayLoading && !replayRefreshing && activeReplayRequestId && !replayPayload && !replayError ? (
        <div className="text-xs text-gray-500">当前请求还没有可展示的免疫回放。</div>
      ) : null}
      {replayPayload ? (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
              请求 ID: <span className="font-mono">{replayPayload.request_id}</span>
            </div>
            <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
              会话 ID: <span className="font-mono">{replayPayload.session_id || '-'}</span>
            </div>
            <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
              首次→末次: {replayPayload.summary?.first_decision || '-'} → {replayPayload.summary?.last_decision || '-'}
            </div>
            <div className="rounded border border-violet-100 bg-white/90 px-2 py-1">
              事件数/最高分: {replayPayload.summary?.event_count ?? 0} / {replayPayload.summary?.max_score ?? '-'}
            </div>
          </div>
          {replayPayload.input_preview ? (
            <div className="rounded border border-violet-100 bg-white/90 px-2 py-1.5 text-xs text-gray-700">
              输入预览: {replayPayload.input_preview}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-1.5">
            {(replayPayload.summary?.reason_codes || []).slice(0, 8).map((reasonCode) => (
              <span key={`replay-reason-${reasonCode}`} className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                {reasonCode}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(replayPayload.summary?.matched_rules || []).slice(0, 6).map((rule) => (
              <span key={`replay-rule-${rule}`} className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700">
                {rule}
              </span>
            ))}
          </div>
          <div className="max-h-56 space-y-1.5 overflow-y-auto rounded border border-violet-100 bg-white/90 p-2">
            {(replayPayload.decisions || []).map((item, index) => (
              <div key={`${item.ts_ms}-${item.event_type}-${index}`} className="rounded border border-gray-100 bg-gray-50 px-2 py-1.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-gray-700">{item.event_type}</div>
                  <div className="text-gray-500">{item.decision} / {item.risk_level || 'none'}</div>
                </div>
                <div className="mt-1 text-gray-500">
                  {item.stage || '-'} · score={item.score ?? '-'} · {item.reason_codes?.join(', ') || '-'}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
