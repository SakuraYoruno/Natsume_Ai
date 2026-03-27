import React, { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Heart, Smile, Frown, Meh, Zap, TrendingUp, TrendingDown, Minus, Activity } from 'lucide-react'
import LineChart from '../components/charts/LineChart.jsx'
import { fetchVtsStats, fetchSubsystemsStats, fetchMemoryStats } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const MOOD_STYLES = {
  calm:     { icon: Meh,   color: 'text-blue-500',   bg: 'bg-blue-50',   label: '平静' },
  steady:   { icon: Meh,   color: 'text-gray-500',   bg: 'bg-gray-50',   label: '稳定' },
  engaged:  { icon: Smile, color: 'text-green-600',  bg: 'bg-green-50',  label: '活跃' },
  tired:    { icon: Frown, color: 'text-orange-500', bg: 'bg-orange-50', label: '疲惫' },
  critical: { icon: Frown, color: 'text-red-600',    bg: 'bg-red-50',    label: '临界' },
}

const EMOTION_COLORS = {
  HAPPY: '#22c55e', EXCITED: '#eab308', SAD: '#3b82f6', ANGRY: '#ef4444',
  NEUTRAL: '#9ca3af', SURPRISED: '#a855f7', CONFUSED: '#f97316', CALM: '#06b6d4',
  LOVE: '#ec4899', CURIOUS: '#8b5cf6', SHY: '#f472b6', BORED: '#6b7280',
}

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 }, { label: '6h', value: 360 },
  { label: '24h', value: 1440 }, { label: '7d', value: 10080 },
]

function VADGauge({ label, value, color }) {
  const pct = ((value + 1) / 2 * 100).toFixed(0) // [-1,1] → [0,100]
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-600">{label}</span>
        <span className={`font-mono font-bold ${color}`}>{value.toFixed(2)}</span>
      </div>
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500`} style={{ width: `${pct}%`, backgroundColor: color === 'text-green-600' ? '#22c55e' : color === 'text-blue-600' ? '#3b82f6' : '#a855f7' }} />
      </div>
      <div className="flex justify-between text-[10px] text-gray-400">
        <span>-1.0</span><span>0</span><span>+1.0</span>
      </div>
    </div>
  )
}

export default function EmotionDashboard() {
  const pollingEnabled = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)
  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [vtsData, setVtsData] = useState(null)
  const [memData, setMemData] = useState(null)

  // 模拟当前情感状态（实际应从后端获取）
  const [emotionState] = useState({
    valence: 0.35, arousal: 0.2, dominance: 0.1,
    mood: 'engaged', emotion_tag: 'HAPPY',
    bond_temperature: 0.72, bond_stage: 'CLOSE_FRIEND',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [vts, mem] = await Promise.all([
        fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
      ])
      setVtsData(vts)
      setMemData(mem)
    } catch {} finally { setLoading(false) }
  }, [windowMinutes])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const moodStyle = MOOD_STYLES[emotionState.mood] || MOOD_STYLES.steady
  const MoodIcon = moodStyle.icon
  const topEmotions = vtsData?.top_emotions || []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">情感仪表盘</h1>
          <p className="text-sm text-gray-500">VAD 三维 · 情感分布 · Bond 亲密温度</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="text-xs border rounded px-2 py-1.5" value={windowMinutes} onChange={e => setWindowMinutes(Number(e.target.value))}>
            {WINDOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 当前情感状态 */}
        <div className="card p-5 space-y-4">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1"><Heart size={14} className="text-pink-500" /> 当前状态</h2>
          <div className="text-center py-4">
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full ${moodStyle.bg}`}>
              <MoodIcon size={20} className={moodStyle.color} />
              <span className={`font-bold ${moodStyle.color}`}>{moodStyle.label}</span>
            </div>
            <div className="mt-2 text-xs text-gray-500">
              emotion_tag: <span className="font-mono text-gray-700">{emotionState.emotion_tag}</span>
            </div>
          </div>
          {/* VAD 仪表 */}
          <div className="space-y-3">
            <VADGauge label="Valence（效价）" value={emotionState.valence} color="text-green-600" />
            <VADGauge label="Arousal（唤醒度）" value={emotionState.arousal} color="text-blue-600" />
            <VADGauge label="Dominance（支配感）" value={emotionState.dominance} color="text-purple-600" />
          </div>
        </div>

        {/* 情感标签分布 */}
        <div className="card p-5 space-y-4">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1"><Activity size={14} className="text-indigo-500" /> 情感标签分布</h2>
          {topEmotions.length > 0 ? (
            <div className="space-y-2">
              {topEmotions.map(em => (
                <div key={em.emotion_tag} className="flex items-center gap-2">
                  <span className="text-xs text-gray-600 w-20 shrink-0 truncate">{em.emotion_tag}</span>
                  <div className="flex-1 h-4 bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{
                      width: `${em.pct}%`,
                      backgroundColor: EMOTION_COLORS[em.emotion_tag] || '#9ca3af',
                    }} />
                  </div>
                  <span className="text-xs text-gray-500 w-12 text-right">{em.pct}%</span>
                  <span className="text-xs text-gray-400 w-8 text-right">{em.count}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-gray-400 py-8 text-xs">暂无情感数据</div>
          )}
          {/* VTS 统计 */}
          {vtsData && (
            <div className="grid grid-cols-3 gap-2 text-center text-xs pt-2 border-t">
              <div><div className="text-lg font-bold text-green-600">{vtsData.apply_rate ?? '—'}%</div><div className="text-gray-500">表情应用率</div></div>
              <div><div className={`text-lg font-bold ${vtsData.degrade_rate > 10 ? 'text-orange-500' : 'text-gray-400'}`}>{vtsData.degrade_rate ?? 0}%</div><div className="text-gray-500">降级率</div></div>
              <div><div className="text-lg font-bold text-blue-600">{vtsData.talk_gate_allowed ?? 0}</div><div className="text-gray-500">对话放行</div></div>
            </div>
          )}
        </div>

        {/* Bond 亲密温度 */}
        <div className="card p-5 space-y-4">
          <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1"><Zap size={14} className="text-pink-500" /> Bond 关系</h2>
          <div className="text-center py-4">
            <div className="relative w-32 h-32 mx-auto">
              <svg viewBox="0 0 120 120" className="w-full h-full">
                <circle cx="60" cy="60" r="50" fill="none" stroke="#e5e7eb" strokeWidth="8" />
                <circle cx="60" cy="60" r="50" fill="none" stroke="#ec4899" strokeWidth="8"
                  strokeDasharray={`${emotionState.bond_temperature * 314} 314`}
                  strokeLinecap="round" transform="rotate(-90 60 60)" className="transition-all duration-1000" />
                <text x="60" y="55" textAnchor="middle" className="fill-gray-800 text-lg font-bold">{(emotionState.bond_temperature * 100).toFixed(0)}°</text>
                <text x="60" y="72" textAnchor="middle" className="fill-gray-500 text-[10px]">{emotionState.bond_stage}</text>
              </svg>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="card p-2 text-center">
              <div className="font-bold text-pink-600">{emotionState.bond_stage}</div>
              <div className="text-gray-500">当前阶段</div>
            </div>
            <div className="card p-2 text-center">
              <div className="font-bold text-gray-700">{emotionState.bond_temperature.toFixed(2)}</div>
              <div className="text-gray-500">亲密温度</div>
            </div>
          </div>
          {/* Memory 子系统摘要 */}
          {memData?.subsystems && (
            <div className="space-y-1 pt-2 border-t">
              <div className="text-xs font-medium text-gray-600 mb-1">记忆子系统</div>
              {memData.subsystems.map(s => (
                <div key={s.subsystem} className="flex items-center justify-between text-xs">
                  <span className="text-gray-600">{s.display_name}</span>
                  <span className={s.success_rate >= 90 ? 'text-green-600' : s.success_rate >= 70 ? 'text-orange-500' : 'text-red-500'}>
                    {s.success_rate ?? '—'}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
