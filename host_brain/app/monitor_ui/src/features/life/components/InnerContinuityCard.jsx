import { Brain, Clock3, Sparkles } from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../../../shared/ui/KeyValueRow.jsx'
import {
  asArray,
  compactText,
  formatAbsenceBand,
  formatDateTime,
  formatNumber,
} from '../../../utils/digitalLife.js'

function resolveNumber(primary, fallback = 0) {
  if (primary == null || primary === '') return Number(fallback || 0)
  const value = Number(primary)
  return Number.isFinite(value) ? value : Number(fallback || 0)
}

export default function InnerContinuityCard({ runtimeSnapshot }) {
  const innerWorldState = runtimeSnapshot?.inner_world_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}

  const score = resolveNumber(offlineState.continuity_score, innerWorldState.continuity_score)
  const eventCount = Math.max(
    0,
    Math.floor(resolveNumber(offlineState.spontaneous_event_count, innerWorldState.spontaneous_event_count)),
  )
  const lastNote = String(offlineState.last_spontaneous_note || innerWorldState.last_spontaneous_note || '').trim()
  const lastSource = String(offlineState.last_spontaneous_source || innerWorldState.last_spontaneous_source || '').trim().toLowerCase()
  const lastTs = resolveNumber(offlineState.last_spontaneous_ts_ms, innerWorldState.last_spontaneous_ts_ms)
  const stream = asArray(offlineState.inner_stream).length > 0
    ? asArray(offlineState.inner_stream)
    : asArray(innerWorldState.inner_stream)
  const recentEvents = stream
    .filter((item) => item && typeof item === 'object')
    .slice(-6)
    .reverse()

  const scorePct = Math.max(0, Math.min(100, Math.round(score * 100)))

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Brain size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">内心连续性</h2>
        </div>
        <div className="inline-flex items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">
          <Sparkles size={12} />
          连续性 {scorePct}%
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-3">
        <div className="h-2 overflow-hidden rounded-full bg-gray-200">
          <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${scorePct}%` }} />
        </div>
        <div className="mt-3 space-y-2">
          <KeyValueRow label="自发活动次数" value={eventCount} />
          <KeyValueRow label="最后活动时间" value={formatDateTime(lastTs)} />
          <KeyValueRow label="生成来源" value={lastSource === 'llm' ? 'LLM' : (lastSource ? 'Fallback' : '—')} />
          <KeyValueRow label="最后活动内容" value={lastNote || '—'} />
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-2 flex items-center gap-2 text-xs text-gray-500">
          <Clock3 size={13} />
          <span>最近内心流</span>
        </div>
        <div className="space-y-2">
          {recentEvents.length > 0 ? (
            recentEvents.map((event, index) => {
              const note = compactText(event?.note || '', 180)
              const tsLabel = formatDateTime(event?.ts_ms)
              const bandLabel = formatAbsenceBand(event?.absence_band)
              const hourText = formatNumber(event?.offline_hours, 2)
              const sourceLabel = String(event?.source || '').toLowerCase() === 'llm' ? 'LLM' : 'Fallback'
              return (
                <div
                  key={`${event?.ts_ms || 'evt'}-${index}`}
                  className="rounded-xl border border-gray-200 bg-white px-3 py-2"
                >
                  <div className="text-xs text-gray-500">{tsLabel} · {bandLabel} · {hourText}h · {sourceLabel}</div>
                  <div className="mt-1 text-sm text-gray-700">{note || '—'}</div>
                </div>
              )
            })
          ) : (
            <EmptyStateCard
              title="尚未出现自发内心活动"
              hint="后台 tick 继续推进后，这里会持续出现内心流事件。"
              className="p-4"
            />
          )}
        </div>
      </div>
    </div>
  )
}
