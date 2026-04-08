import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Heart, RefreshCw, ShieldCheck, Sparkles, Users } from 'lucide-react'
import {
  fetchBondHealth,
  fetchBondStage,
  fetchBondUsers,
  submitBondEvent,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import KeyValueRow from '../components/KeyValueRow.jsx'
import MetricCard from '../components/MetricCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useAppStore from '../store/useAppStore.js'
import {
  formatDateTime,
  formatEmotionTag,
  formatStage,
  formatTransitionStatus,
  formatTrend,
} from '../utils/digitalLife.js'

const STAGE_COLORS = {
  STRANGER: '#94a3b8',
  ACQUAINTANCE: '#60a5fa',
  FRIEND: '#34d399',
  CLOSE: '#22c55e',
  CLOSE_FRIEND: '#a78bfa',
  COMPANION: '#f472b6',
  CONFIDANT: '#ec4899',
  INTIMATE: '#ef4444',
  SOULMATE: '#f59e0b',
}

const UPDATE_KIND_OPTIONS = [
  { value: 'INTERACTION', label: '日常互动' },
  { value: 'DEEP_TALK', label: '深度交流' },
  { value: 'EMOTIONAL_SUPPORT', label: '情绪支持' },
  { value: 'SHARED_EXPERIENCE', label: '共同经历' },
  { value: 'CONFLICT', label: '冲突摩擦' },
  { value: 'RECONCILIATION', label: '和解修复' },
]

const EMOTION_TAG_OPTIONS = [
  { value: 'NEUTRAL', label: '平静' },
  { value: 'JOY', label: '喜悦' },
  { value: 'SADNESS', label: '难过' },
  { value: 'ANGER', label: '愤怒' },
  { value: 'FEAR', label: '害怕' },
  { value: 'SURPRISE', label: '惊讶' },
  { value: 'DISGUST', label: '排斥' },
]

function formatHealthStatus(status) {
  if (!status) return '—'
  if (status === 'OK') return '正常'
  if (status === 'DEGRADED') return '降级'
  if (status === 'DOWN') return '离线'
  return status
}

function StageDistribution({ items }) {
  const total = items.reduce((sum, item) => sum + item.count, 0)

  if (!items.length || total <= 0) {
    return (
      <EmptyStateCard
        title="暂无阶段分布"
        hint="等待关系样本累积后再展示。"
        className="p-6"
      />
    )
  }

  return (
    <div className="space-y-2">
      {items.map((item) => {
        const percent = (item.count / total) * 100
        return (
          <div key={item.stage} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-700">{formatStage(item.stage)}</span>
              <span className="text-gray-400">{item.count} · {percent.toFixed(0)}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-gray-100">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${percent}%`,
                  backgroundColor: STAGE_COLORS[item.stage] || '#cbd5e1',
                }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function EventForm({ onSubmit, loading, selectedUser }) {
  const [form, setForm] = useState({
    user_id: selectedUser || '',
    update_kind: 'INTERACTION',
    evidence_refs: '',
    emotion_tag: 'NEUTRAL',
    emotion_intensity: '0.5',
  })

  useEffect(() => {
    if (!selectedUser) return
    setForm((current) => ({ ...current, user_id: selectedUser }))
  }, [selectedUser])

  const handleSubmit = (event) => {
    event.preventDefault()
    const refs = form.evidence_refs
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    if (!form.user_id || refs.length === 0) return

    onSubmit({
      ...form,
      evidence_refs: refs,
      emotion_intensity: Number(form.emotion_intensity),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-gray-500">用户 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.user_id}
            onChange={(event) => setForm((current) => ({ ...current, user_id: event.target.value }))}
            placeholder="例如 wxid_user_001"
            required
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">事件类型</label>
          <select
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.update_kind}
            onChange={(event) => setForm((current) => ({ ...current, update_kind: event.target.value }))}
          >
            {UPDATE_KIND_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-gray-500">证据引用（逗号分隔）</label>
        <input
          className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.evidence_refs}
          onChange={(event) => setForm((current) => ({ ...current, evidence_refs: event.target.value }))}
          placeholder="例如 trace_001, message_002"
          required
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-gray-500">情绪标签</label>
          <select
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.emotion_tag}
            onChange={(event) => setForm((current) => ({ ...current, emotion_tag: event.target.value }))}
          >
            {EMOTION_TAG_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">
            情绪强度（{form.emotion_intensity}）
          </label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            className="mt-1 w-full"
            value={form.emotion_intensity}
            onChange={(event) => setForm((current) => ({ ...current, emotion_intensity: event.target.value }))}
          />
        </div>
      </div>

      <button
        type="submit"
        disabled={loading}
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? '提交中…' : '提交关系事件'}
      </button>
    </form>
  )
}

export default function Bond() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)

  const [health, setHealth] = useState(null)
  const [users, setUsers] = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [userDetail, setUserDetail] = useState(null)
  const [stageDist, setStageDist] = useState([])
  const [submitResult, setSubmitResult] = useState(null)
  const [submitLoading, setSubmitLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [healthResponse, usersResponse] = await Promise.all([
        fetchBondHealth(),
        fetchBondUsers(),
      ])

      const userList = Array.isArray(usersResponse?.users)
        ? usersResponse.users
        : Array.isArray(usersResponse)
          ? usersResponse
          : []

      setHealth(healthResponse)
      setUsers(userList)

      const samples = await Promise.all(
        userList.slice(0, 24).map((userId) => fetchBondStage(userId).catch(() => null))
      )

      const distribution = samples.reduce((result, item) => {
        if (!item?.current_stage) return result
        result[item.current_stage] = (result[item.current_stage] || 0) + 1
        return result
      }, {})

      setStageDist(
        Object.entries(distribution).map(([stage, count]) => ({
          stage,
          count,
        }))
      )

      if (!selectedUser && userList.length > 0) {
        setSelectedUser(userList[0])
      }
    } catch (loadError) {
      setError(loadError.message || '关系数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [selectedUser])

  const loadUserDetail = useCallback(async (userId) => {
    if (!userId) {
      setUserDetail(null)
      return
    }

    setDetailLoading(true)
    try {
      const detail = await fetchBondStage(userId)
      setUserDetail(detail)
    } catch {
      setUserDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pollingEnabled) return undefined
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  useEffect(() => {
    if (selectedUser) {
      loadUserDetail(selectedUser)
    }
  }, [loadUserDetail, selectedUser])

  useEffect(() => {
    if (selectedUser && !users.includes(selectedUser)) {
      setSelectedUser(users[0] || null)
      setUserDetail(null)
    }
  }, [selectedUser, users])

  const handleSubmitEvent = async (body) => {
    setSubmitLoading(true)
    setSubmitResult(null)
    try {
      const result = await submitBondEvent(body)
      setSubmitResult({ ok: true, data: result })
      setSelectedUser(body.user_id)
      await load()
      await loadUserDetail(body.user_id)
    } catch (submitError) {
      setSubmitResult({ ok: false, error: submitError.message || '提交失败' })
    } finally {
      setSubmitLoading(false)
    }
  }

  const stageSampleCount = useMemo(
    () => stageDist.reduce((sum, item) => sum + item.count, 0),
    [stageDist]
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">关系状态</h1>
          <p className="text-sm text-gray-500">查看关系阶段、趋势、用户详情与事件补录入口。</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <DigitalLifeNav />

      <PageHero
        icon={Heart}
        eyebrow="关系引擎快照"
        title={selectedUser || formatHealthStatus(health?.status)}
        description={
          selectedUser
            ? '当前正在查看该用户的关系阶段、趋势与能力提示。'
            : '这里集中展示关系引擎健康度、阶段分布与关系事件录入。'
        }
        gradientClass="from-primary-50 via-white to-rose-50"
        metaItems={[
          `引擎状态：${formatHealthStatus(health?.status)}`,
          `用户数：${health?.user_count ?? users.length}`,
          `分布样本：${stageSampleCount}`,
          `已选用户：${selectedUser || '未选择'}`,
        ]}
      />

      {error ? <NoticeBanner variant="error" title="关系数据加载失败">{error}</NoticeBanner> : null}

      {submitResult ? (
        <NoticeBanner
          variant={submitResult.ok ? 'success' : 'error'}
          title={submitResult.ok ? '关系事件已提交' : '关系事件提交失败'}
        >
          {submitResult.ok
            ? `${formatStage(submitResult.data.from_stage)} → ${formatStage(submitResult.data.to_stage)}，${formatTransitionStatus(submitResult.data.transition_status)}，积分变化 ${submitResult.data.score_delta > 0 ? '+' : ''}${submitResult.data.score_delta ?? 0}`
            : submitResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard label="引擎状态" value={formatHealthStatus(health?.status)} hint={`用户 ${health?.user_count ?? users.length}`} tone="text-green-600" />
        <MetricCard label="当前阶段" value={formatStage(userDetail?.current_stage)} hint={`趋势 ${formatTrend(userDetail?.trend)}`} tone="text-pink-600" />
        <MetricCard label="累计积分" value={userDetail?.total_score ?? '—'} hint={`事件 ${userDetail?.event_count ?? 0} 次`} tone="text-purple-600" />
        <MetricCard label="最近事件" value={formatDateTime(userDetail?.last_event_ts_ms)} hint={userDetail?.capability_hint || '等待选择用户'} tone="text-blue-600" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)_360px]">
        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Users size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">用户列表</h2>
          </div>
          {users.length > 0 ? (
            <div className="max-h-[420px] space-y-2 overflow-y-auto">
              {users.map((userId) => (
                <button
                  key={userId}
                  className={`w-full rounded-xl border px-3 py-2 text-left text-sm transition-colors ${
                    selectedUser === userId
                      ? 'border-primary-200 bg-primary-50 text-primary'
                      : 'border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100'
                  }`}
                  onClick={() => setSelectedUser(userId)}
                >
                  {userId}
                </button>
              ))}
            </div>
          ) : (
            <EmptyStateCard title="暂无关系用户" hint="等第一批 Bond 数据写入后再显示。" className="p-4" />
          )}
        </div>

        <div className="space-y-4">
          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">阶段分布</h2>
            </div>
            <StageDistribution items={stageDist} />
          </div>

          <div className="card p-5">
            <div className="mb-3 flex items-center gap-2">
              <Heart size={15} className="text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-800">用户详情</h2>
            </div>
            {detailLoading ? (
              <NoticeBanner variant="info">正在加载用户关系详情…</NoticeBanner>
            ) : userDetail ? (
              <div className="space-y-3">
                <KeyValueRow label="用户 ID" value={userDetail.user_id} />
                <KeyValueRow label="当前阶段" value={formatStage(userDetail.current_stage)} />
                <KeyValueRow label="阶段序号" value={userDetail.stage_index} />
                <KeyValueRow label="累计积分" value={userDetail.total_score} />
                <KeyValueRow label="事件次数" value={userDetail.event_count} />
                <KeyValueRow label="趋势" value={formatTrend(userDetail.trend)} />
                <KeyValueRow label="能力提示" value={userDetail.capability_hint} />
                <KeyValueRow label="最近事件" value={formatDateTime(userDetail.last_event_ts_ms)} />
              </div>
            ) : (
              <EmptyStateCard title="请选择一个用户" hint="选择后这里会显示该用户的关系快照。" className="p-4" />
            )}
          </div>
        </div>

        <div className="card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">补录关系事件</h2>
          </div>
          <NoticeBanner variant="info" className="mb-3">
            这里用于人工补录关系事件；`evidence_refs` 不能为空。
          </NoticeBanner>
          <EventForm onSubmit={handleSubmitEvent} loading={submitLoading} selectedUser={selectedUser} />
        </div>
      </div>
    </div>
  )
}
