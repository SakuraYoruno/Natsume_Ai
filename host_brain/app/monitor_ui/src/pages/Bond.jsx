import React, { useState, useEffect, useCallback } from 'react'
import {
  fetchBondUsers,
  fetchBondStage,
  fetchBondHealth,
  submitBondEvent,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import useAppStore from '../store/useAppStore.js'

// ── 阶段颜色映射 ──────────────────────────────────────────────────────────
const STAGE_COLORS = {
  STRANGER:    '#94a3b8',
  ACQUAINTANCE:'#60a5fa',
  FRIEND:      '#34d399',
  CLOSE_FRIEND:'#a78bfa',
  COMPANION:   '#f472b6',
}

const STAGE_LABELS = {
  STRANGER:    '陌生人',
  ACQUAINTANCE:'熟人',
  FRIEND:      '朋友',
  CLOSE_FRIEND:'挚友',
  COMPANION:   '伴侣',
}

const TREND_ICONS = { RISING: '↑', STABLE: '→', DECLINING: '↓' }
const TREND_COLORS = { RISING: 'text-green-600', STABLE: 'text-gray-500', DECLINING: 'text-red-500' }

// ── 简易饼图（SVG） ───────────────────────────────────────────────────────
function PieChart({ data }) {
  if (!data || data.length === 0) return <div className="text-gray-400 text-sm text-center py-8">暂无数据</div>

  const total = data.reduce((s, d) => s + d.count, 0)
  if (total === 0) return <div className="text-gray-400 text-sm text-center py-8">暂无数据</div>

  let cumAngle = -Math.PI / 2
  const cx = 80, cy = 80, r = 70
  const slices = data.map(d => {
    const angle = (d.count / total) * 2 * Math.PI
    const x1 = cx + r * Math.cos(cumAngle)
    const y1 = cy + r * Math.sin(cumAngle)
    cumAngle += angle
    const x2 = cx + r * Math.cos(cumAngle)
    const y2 = cy + r * Math.sin(cumAngle)
    const large = angle > Math.PI ? 1 : 0
    return { ...d, path: `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large},1 ${x2},${y2} Z` }
  })

  return (
    <div className="flex items-center gap-6">
      <svg width="160" height="160" viewBox="0 0 160 160">
        {slices.map((s, i) => (
          <path key={i} d={s.path} fill={STAGE_COLORS[s.stage] || '#e2e8f0'} stroke="#fff" strokeWidth="2" />
        ))}
      </svg>
      <div className="flex flex-col gap-1.5">
        {slices.map((s, i) => (
          <div key={i} className="flex items-center gap-2 text-sm">
            <span className="w-3 h-3 rounded-full inline-block" style={{ background: STAGE_COLORS[s.stage] || '#e2e8f0' }} />
            <span className="text-gray-700">{STAGE_LABELS[s.stage] || s.stage}</span>
            <span className="text-gray-400 ml-1">{s.count}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── 事件提交表单 ──────────────────────────────────────────────────────────
const UPDATE_KINDS = [
  'INTERACTION', 'DEEP_TALK', 'EMOTIONAL_SUPPORT',
  'SHARED_EXPERIENCE', 'CONFLICT', 'RECONCILIATION',
]

function EventForm({ onSubmit, loading }) {
  const [form, setForm] = useState({
    user_id: '',
    update_kind: 'INTERACTION',
    evidence_refs: '',
    emotion_tag: 'NEUTRAL',
    emotion_intensity: 0.5,
  })

  const handleSubmit = (e) => {
    e.preventDefault()
    const refs = form.evidence_refs.split(',').map(s => s.trim()).filter(Boolean)
    if (!form.user_id || refs.length === 0) return
    onSubmit({ ...form, evidence_refs: refs, emotion_intensity: parseFloat(form.emotion_intensity) })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">用户 ID</label>
          <input
            className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.user_id}
            onChange={e => setForm(f => ({ ...f, user_id: e.target.value }))}
            placeholder="user_id"
            required
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">事件类型</label>
          <select
            className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.update_kind}
            onChange={e => setForm(f => ({ ...f, update_kind: e.target.value }))}
          >
            {UPDATE_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">证据引用（逗号分隔）</label>
        <input
          className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.evidence_refs}
          onChange={e => setForm(f => ({ ...f, evidence_refs: e.target.value }))}
          placeholder="ref1, ref2, ..."
          required
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">情感标签</label>
          <select
            className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.emotion_tag}
            onChange={e => setForm(f => ({ ...f, emotion_tag: e.target.value }))}
          >
            {['NEUTRAL','JOY','SADNESS','ANGER','FEAR','SURPRISE','DISGUST'].map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">情感强度 ({form.emotion_intensity})</label>
          <input
            type="range" min="0" max="1" step="0.1"
            className="w-full mt-1"
            value={form.emotion_intensity}
            onChange={e => setForm(f => ({ ...f, emotion_intensity: e.target.value }))}
          />
        </div>
      </div>
      <button
        type="submit"
        disabled={loading}
        className="w-full bg-blue-600 text-white rounded px-4 py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
      >
        {loading ? '提交中...' : '提交 Bond 事件'}
      </button>
    </form>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────
export default function Bond() {
  const pollingEnabled  = useAppStore(s => s.pollingEnabled)
  const pollingInterval = useAppStore(s => s.pollingInterval)

  const [health, setHealth]       = useState(null)
  const [users, setUsers]         = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [userDetail, setUserDetail]     = useState(null)
  const [stageDist, setStageDist]       = useState([])
  const [submitResult, setSubmitResult] = useState(null)
  const [submitLoading, setSubmitLoading] = useState(false)
  const [error, setError]         = useState(null)
  const [loading, setLoading]     = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, u] = await Promise.all([fetchBondHealth(), fetchBondUsers()])
      setHealth(h)
      const userList = u.users || []
      setUsers(userList)

      // 计算阶段分布
      if (userList.length > 0) {
        const details = await Promise.all(userList.slice(0, 20).map(uid => fetchBondStage(uid).catch(() => null)))
        const dist = {}
        details.forEach(d => {
          if (d) dist[d.current_stage] = (dist[d.current_stage] || 0) + 1
        })
        setStageDist(Object.entries(dist).map(([stage, count]) => ({ stage, count })))
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadUserDetail = useCallback(async (uid) => {
    try {
      const d = await fetchBondStage(uid)
      setUserDetail(d)
    } catch (e) {
      setUserDetail(null)
    }
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!pollingEnabled) return
    const id = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(id)
  }, [pollingEnabled, pollingInterval, load])

  useEffect(() => {
    if (selectedUser) loadUserDetail(selectedUser)
  }, [selectedUser, loadUserDetail])

  const handleSubmitEvent = async (body) => {
    setSubmitLoading(true)
    setSubmitResult(null)
    try {
      const r = await submitBondEvent(body)
      setSubmitResult({ ok: true, data: r })
      load()
    } catch (e) {
      setSubmitResult({ ok: false, error: e.message })
    } finally {
      setSubmitLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <DigitalLifeNav />

      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">Bond 关系管理</h1>
          <p className="text-sm text-gray-500 mt-0.5">用户关系阶段追踪与事件管理</p>
        </div>
        <button onClick={load} disabled={loading}
          className="text-xs border border-gray-300 rounded px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50">
          {loading ? '刷新中...' : '刷新'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>
      )}

      {/* 健康状态 + 阶段分布 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">引擎状态</h2>
          {health ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${health.status === 'OK' ? 'bg-green-500' : 'bg-yellow-400'}`} />
                <span className="text-sm font-medium text-gray-700">{health.status}</span>
              </div>
              <div className="text-sm text-gray-500">用户总数：<span className="font-medium text-gray-700">{health.user_count}</span></div>
              <div className="text-xs text-gray-400">阶段顺序：{(health.stage_order || []).join(' → ')}</div>
            </div>
          ) : <div className="text-gray-400 text-sm">加载中...</div>}
        </div>

        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">阶段分布</h2>
          <PieChart data={stageDist} />
        </div>
      </div>

      {/* 用户列表 + 详情 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">用户列表 ({users.length})</h2>
          {users.length === 0 ? (
            <div className="text-gray-400 text-sm">暂无用户</div>
          ) : (
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {users.map(uid => (
                <button
                  key={uid}
                  onClick={() => setSelectedUser(uid)}
                  className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                    selectedUser === uid
                      ? 'bg-blue-50 text-blue-700 font-medium'
                      : 'text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {uid}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="md:col-span-2 bg-white rounded-lg border border-gray-200 p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            {selectedUser ? `用户详情：${selectedUser}` : '选择用户查看详情'}
          </h2>
          {userDetail ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <span
                  className="px-3 py-1 rounded-full text-sm font-medium text-white"
                  style={{ background: STAGE_COLORS[userDetail.current_stage] || '#94a3b8' }}
                >
                  {STAGE_LABELS[userDetail.current_stage] || userDetail.current_stage}
                </span>
                <span className={`text-sm font-medium ${TREND_COLORS[userDetail.trend] || 'text-gray-500'}`}>
                  {TREND_ICONS[userDetail.trend] || ''} {userDetail.trend}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: '阶段序号', value: userDetail.stage_index },
                  { label: '累计积分', value: userDetail.total_score },
                  { label: '事件总数', value: userDetail.event_count },
                ].map(({ label, value }) => (
                  <div key={label} className="bg-gray-50 rounded p-3 text-center">
                    <div className="text-lg font-bold text-gray-800">{value}</div>
                    <div className="text-xs text-gray-500 mt-0.5">{label}</div>
                  </div>
                ))}
              </div>
              <div className="text-xs text-gray-500 bg-gray-50 rounded p-2">
                {userDetail.capability_hint}
              </div>
              <div className="text-xs text-gray-400">
                最近事件：{userDetail.last_event_ts_ms
                  ? new Date(userDetail.last_event_ts_ms).toLocaleString('zh-CN')
                  : '—'}
              </div>
            </div>
          ) : (
            <div className="text-gray-400 text-sm">
              {selectedUser ? '加载中...' : '点击左侧用户查看详情'}
            </div>
          )}
        </div>
      </div>

      {/* 事件提交 */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">提交 Bond 事件</h2>
        <EventForm onSubmit={handleSubmitEvent} loading={submitLoading} />
        {submitResult && (
          <div className={`mt-3 p-3 rounded text-sm ${submitResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            {submitResult.ok ? (
              <span>
                ✓ 提交成功：{submitResult.data.from_stage} → {submitResult.data.to_stage}
                （{submitResult.data.transition_status}，积分变化 {submitResult.data.score_delta > 0 ? '+' : ''}{submitResult.data.score_delta}）
              </span>
            ) : (
              <span>✗ 提交失败：{submitResult.error}</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
