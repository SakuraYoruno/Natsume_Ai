import {
  Clock3,
  MessageSquareDashed,
  MoonStar,
  RefreshCw,
  Sparkles,
  Waves,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../shared/ui/KeyValueRow.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import BackgroundRuntimeCard from '../features/life/components/BackgroundRuntimeCard.jsx'
import InnerContinuityCard from '../features/life/components/InnerContinuityCard.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import useAppStore from '../store/useAppStore.js'
import {
  asArray,
  compactText,
  formatAbsenceBand,
  formatBoolean,
  formatDateTime,
  formatNumber,
  formatReconnectStyle,
  formatSlotStatus,
} from '../utils/digitalLife.js'

export default function InnerWorldDashboard() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const { runtimeSnapshot, loading, error, reload } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
  })

  const innerWorldState = runtimeSnapshot?.inner_world_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const runtimeSummary = runtimeSnapshot?.runtime_summary || {}
  const currentState = runtimeSummary?.current_state || {}
  const offlineSlot = runtimeSummary?.core_slots?.offline_state || {}
  const anticipationTopics = asArray(offlineState.anticipation_topics)
  const absenceBandLabel = formatAbsenceBand(offlineState.absence_band || currentState.absence_band)
  const reconnectStyleLabel = formatReconnectStyle(offlineSlot.offline_reconnect_style || currentState.offline_reconnect_style)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">默认模式网络</h1>
          <p className="text-sm text-gray-500">观察独处状态、离线演化、期待话题与内在漂移。</p>
        </div>
        <button className="btn-secondary" onClick={reload} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={MoonStar}
        eyebrow="默认模式网络专题"
        title={absenceBandLabel === '—' ? '近期在线' : absenceBandLabel}
        description={
          anticipationTopics.length > 0
            ? `当前有 ${anticipationTopics.length} 个待表达话题仍在悬挂中。`
            : '当前没有明显的待表达话题，内在世界相对平静。'
        }
        gradientClass="from-primary-50 via-white to-indigo-50"
        metaItems={[
          `离线时长：${offlineState.offline_hours != null ? `${formatNumber(offlineState.offline_hours, 2)} 小时` : '—'}`,
          `表达等待：${formatBoolean(offlineSlot.should_express_waiting)}`,
          `用户 ID：${innerWorldState.user_id || '—'}`,
        ]}
      />

      <BackgroundRuntimeCard runtimeSnapshot={runtimeSnapshot} />
      <InnerContinuityCard runtimeSnapshot={runtimeSnapshot} />
      {error ? <NoticeBanner variant="error" title="内在世界加载失败">{error}</NoticeBanner> : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          icon={Clock3}
          label="离线时长"
          value={offlineState.offline_hours != null ? `${formatNumber(offlineState.offline_hours, 2)} 小时` : '—'}
          hint={`分层 ${absenceBandLabel}`}
          tone="text-blue-600"
        />
        <MetricCard
          icon={MoonStar}
          label="孤独感"
          value={formatNumber(offlineState.loneliness_level ?? innerWorldState.loneliness, 3)}
          hint="独处牵引"
          tone="text-purple-600"
        />
        <MetricCard
          icon={Sparkles}
          label="期待感"
          value={formatNumber(offlineState.anticipation_level ?? innerWorldState.anticipation, 3)}
          hint={`${anticipationTopics.length} 个话题`}
          tone="text-pink-600"
        />
        <MetricCard
          icon={Waves}
          label="内在漂移"
          value={`${formatNumber(innerWorldState.drifted_valence, 2)} / ${formatNumber(innerWorldState.drifted_arousal, 2)}`}
          hint="效价 / 唤醒"
          tone="text-indigo-600"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Clock3 size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">离线状态</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="离线分层" value={absenceBandLabel} />
            <KeyValueRow
              label="离线时长"
              value={offlineState.offline_duration_ms ? `${formatNumber(offlineState.offline_duration_ms / 3600000, 2)} 小时` : '—'}
            />
            <KeyValueRow label="最后互动" value={formatDateTime(innerWorldState.last_interaction_ts_ms)} />
            <KeyValueRow label="最后心跳" value={formatDateTime(innerWorldState.last_tick_ts_ms)} />
            <KeyValueRow label="重连风格" value={reconnectStyleLabel} />
            <KeyValueRow label="唤醒提示" value={offlineSlot.awakening_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Waves size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">内在漂移</h2>
          </div>
          <div className="mt-4 space-y-3">
            <KeyValueRow label="漂移效价" value={formatNumber(innerWorldState.drifted_valence, 3)} />
            <KeyValueRow label="漂移唤醒" value={formatNumber(innerWorldState.drifted_arousal, 3)} />
            <KeyValueRow label="情绪基线" value={formatNumber(innerWorldState.mood_baseline, 3)} />
            <KeyValueRow label="累计离线小时" value={formatNumber(innerWorldState.total_offline_hours, 2)} />
            <KeyValueRow label="是否表达等待" value={formatBoolean(offlineSlot.should_express_waiting)} />
            <KeyValueRow label="槽位状态" value={formatSlotStatus(runtimeSnapshot?.slot_status?.inner_world_state)} />
          </div>
        </div>

        <div className="card p-5 lg:col-span-2">
          <div className="flex items-center gap-2">
            <MessageSquareDashed size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">期待话题</h2>
          </div>
          <div className="mt-4 space-y-2">
            {anticipationTopics.length > 0 ? (
              anticipationTopics.slice(0, 8).map((topic, index) => (
                <div
                  key={`${topic}-${index}`}
                  className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
                >
                  {compactText(topic, 180)}
                </div>
              ))
            ) : (
              <EmptyStateCard
                title="暂无待表达话题"
                hint="说明当前内在世界相对平静。"
                className="p-4"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
