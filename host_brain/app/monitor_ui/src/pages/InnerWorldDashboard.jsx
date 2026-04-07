import React, { useCallback, useEffect, useState } from 'react'
import {
  Clock3,
  MessageSquareDashed,
  MoonStar,
  RefreshCw,
  Sparkles,
  Waves,
} from 'lucide-react'
import { fetchDigitalLifeRuntimeSnapshot } from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import useAppStore from '../store/useAppStore.js'
import {
  asArray,
  compactText,
  formatBoolean,
  formatDateTime,
  formatNumber,
} from '../utils/digitalLife.js'

function StatCard({ icon: Icon, label, value, hint, tone = 'text-gray-900' }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <Icon size={14} />
        <span>{label}</span>
      </div>
      <div className={`mt-2 text-2xl font-bold ${tone}`}>{value ?? '—'}</div>
      <div className="mt-1 text-xs text-gray-400">{hint ?? '—'}</div>
    </div>
  )
}

function InfoRow({ label, value }) {
  return (
    <div className="flex items-start justify-between gap-3 text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="max-w-[60%] break-words text-right font-medium text-gray-800">{value ?? '—'}</span>
    </div>
  )
}

export default function InnerWorldDashboard() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [runtimeSnapshot, setRuntimeSnapshot] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchDigitalLifeRuntimeSnapshot()
      setRuntimeSnapshot(data)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) {
      return undefined
    }
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const innerWorldState = runtimeSnapshot?.inner_world_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const runtimeSummary = runtimeSnapshot?.runtime_summary || {}
  const currentState = runtimeSummary?.current_state || {}
  const offlineSlot = runtimeSummary?.core_slots?.offline_state || {}
  const anticipationTopics = asArray(offlineState.anticipation_topics)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Inner World</h1>
          <p className="text-sm text-gray-500">独处状态、离线演化、期待话题与内在漂移</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <DigitalLifeNav />

      <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-indigo-50 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              <MoonStar size={12} />
              内心世界专题页
            </div>
            <div className="mt-3 text-2xl font-bold text-gray-900">
              {offlineState.absence_band || currentState.absence_band || 'recent'}
            </div>
            <div className="mt-1 text-sm text-gray-600">
              {anticipationTopics.length > 0 ? `有 ${anticipationTopics.length} 个待表达主题正在悬挂` : '当前没有明显的待表达主题'}
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-gray-500">
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              离线 {offlineState.offline_hours != null ? `${formatNumber(offlineState.offline_hours, 2)}h` : '—'}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              should_express_waiting：{formatBoolean(offlineSlot.should_express_waiting)}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              user_id：{innerWorldState.user_id || '—'}
            </span>
          </div>
        </div>
      </div>

      {error && <div className="card p-4 text-sm text-red-500">加载失败：{error}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Clock3}
          label="离线时长"
          value={offlineState.offline_hours != null ? `${formatNumber(offlineState.offline_hours, 2)}h` : '—'}
          hint={`band=${offlineState.absence_band || '—'}`}
          tone="text-blue-600"
        />
        <StatCard
          icon={MoonStar}
          label="Loneliness"
          value={formatNumber(offlineState.loneliness_level ?? innerWorldState.loneliness, 3)}
          hint="独处牵引"
          tone="text-purple-600"
        />
        <StatCard
          icon={Sparkles}
          label="Anticipation"
          value={formatNumber(offlineState.anticipation_level ?? innerWorldState.anticipation, 3)}
          hint={`${anticipationTopics.length} 个主题`}
          tone="text-pink-600"
        />
        <StatCard
          icon={Waves}
          label="漂移"
          value={`${formatNumber(innerWorldState.drifted_valence, 2)} / ${formatNumber(innerWorldState.drifted_arousal, 2)}`}
          hint="valence / arousal"
          tone="text-indigo-600"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Clock3 size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">离线状态</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="离线分层" value={offlineState.absence_band || currentState.absence_band || '—'} />
            <InfoRow label="离线时长" value={offlineState.offline_duration_ms ? `${formatNumber(offlineState.offline_duration_ms / 3600000, 2)}h` : '—'} />
            <InfoRow label="最后互动" value={formatDateTime(innerWorldState.last_interaction_ts_ms)} />
            <InfoRow label="最后 tick" value={formatDateTime(innerWorldState.last_tick_ts_ms)} />
            <InfoRow label="重新连接风格" value={offlineSlot.offline_reconnect_style || currentState.offline_reconnect_style || '—'} />
            <InfoRow label="唤醒提示" value={offlineSlot.awakening_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Waves size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">内在漂移</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="漂移效价" value={formatNumber(innerWorldState.drifted_valence, 3)} />
            <InfoRow label="漂移唤醒" value={formatNumber(innerWorldState.drifted_arousal, 3)} />
            <InfoRow label="情绪基线" value={formatNumber(innerWorldState.mood_baseline, 3)} />
            <InfoRow label="总离线小时" value={formatNumber(innerWorldState.total_offline_hours, 2)} />
            <InfoRow label="是否表达等待" value={formatBoolean(offlineSlot.should_express_waiting)} />
            <InfoRow label="slot 状态" value={runtimeSnapshot?.slot_status?.inner_world_state || '—'} />
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
                <div key={`${topic}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
                  {compactText(topic, 180)}
                </div>
              ))
            ) : (
              <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-sm text-gray-400">
                当前没有待表达主题，说明内心世界仍偏安静。
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
