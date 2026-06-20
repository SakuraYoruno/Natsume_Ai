import React, { useEffect, useState } from 'react'
import { Heart, RefreshCw, ShieldCheck, Sparkles, Users } from 'lucide-react'
import { submitBondEvent } from '../features/life/api.js'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import KeyValueRow from '../shared/ui/KeyValueRow.jsx'
import OrganSystemOverview from '../features/life/components/lifeBody/OrganSystemOverview.jsx'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import {
  buildBondDashboardViewModel,
  buildBondStageDistribution,
  buildBondSubmitSuccessMessage,
} from '../features/life/bondModel.js'
import { useBondOverviewQuery, useBondUserDetailQuery } from '../features/life/bondQueries.js'
import useAppStore from '../store/useAppStore.js'

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

function StageDistribution({ items }) {
  const distribution = buildBondStageDistribution(items)

  if (!distribution.items.length || distribution.total <= 0) {
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
      {distribution.items.map((item) => {
        return (
          <div key={item.stage} className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-700">{item.label}</span>
              <span className="text-gray-400">{item.count} · {item.percentLabel}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-gray-100">
              <div
                className="h-full rounded-full"
                style={{
                  width: item.width,
                  backgroundColor: item.color,
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
  const pollingTick = useAppStore((state) => state.pollingTick)

  const [selectedUser, setSelectedUser] = useState(null)
  const [submitResult, setSubmitResult] = useState(null)
  const [submitLoading, setSubmitLoading] = useState(false)
  const {
    health,
    users,
    stageDist,
    stageSampleCount,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useBondOverviewQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const {
    userDetail,
    detailError,
    loading: detailLoading,
    refreshing: detailRefreshing,
    lastUpdatedAt: detailUpdatedAt,
    refetch: refetchUserDetail,
  } = useBondUserDetailQuery({
    userId: selectedUser,
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  useEffect(() => {
    if (!selectedUser && users.length > 0) {
      setSelectedUser(users[0])
      return
    }
    if (selectedUser && !users.includes(selectedUser)) {
      setSelectedUser(users[0] || null)
    }
  }, [selectedUser, users])

  const {
    heroView,
    sampleMeta,
    noticeView,
    organModel,
    metricCards,
    detailView,
  } = buildBondDashboardViewModel({
    health,
    users,
    stageDist,
    stageSampleCount,
    overviewError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    selectedUser,
    userDetail,
    detailError,
    detailUpdatedAt,
  })

  const handleSubmitEvent = async (body) => {
    setSubmitLoading(true)
    setSubmitResult(null)
    const shouldRefreshDetail = body.user_id === selectedUser
    try {
      const result = await submitBondEvent(body)
      setSubmitResult({ ok: true, data: result })
      await refetch()
      if (shouldRefreshDetail) {
        await refetchUserDetail()
      }
      setSelectedUser(body.user_id)
    } catch (submitError) {
      setSubmitResult({ ok: false, error: submitError.message || '提交失败' })
    } finally {
      setSubmitLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">社交脑</h1>
          <p className="text-sm text-gray-500">查看关系阶段、趋势、用户详情与事件补录入口。</p>
        </div>
        <button
          className="btn-secondary"
          onClick={() => Promise.allSettled([refetch(), selectedUser ? refetchUserDetail() : Promise.resolve()])}
          disabled={loading || refreshing || detailLoading || detailRefreshing}
        >
          <RefreshCw size={14} className={loading || refreshing || detailLoading || detailRefreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Heart}
        eyebrow="社交脑快照"
        title={heroView.title}
        description={heroView.description}
        gradientClass="from-primary-50 via-white to-rose-50"
        metaItems={heroView.metaItems}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={sampleMeta.loadState}
            sampleSize={sampleMeta.sampleSize}
            sampleLabel={sampleMeta.sampleLabel}
            updatedAt={sampleMeta.updatedAt}
            source={sampleMeta.source}
            definition={sampleMeta.definition}
          />
        )}
      />

      {noticeView.overviewError ? (
        <NoticeBanner variant="error" title="关系数据加载失败">{noticeView.overviewError}</NoticeBanner>
      ) : null}

      {noticeView.noSample ? (
        <NoticeBanner variant="info" title="关系链路待采样">
          当前还没有可用的 Bond 用户或阶段样本，页面会保留中性空态。
        </NoticeBanner>
      ) : null}

      {noticeView.unavailable ? (
        <NoticeBanner variant="warning" title="关系观测暂不可用">
          Bond 相关接口当前未返回可用快照，建议稍后重试。
        </NoticeBanner>
      ) : null}

      <OrganSystemOverview model={organModel} />

      {submitResult ? (
        <NoticeBanner
          variant={submitResult.ok ? 'success' : 'error'}
          title={submitResult.ok ? '关系事件已提交' : '关系事件提交失败'}
        >
          {submitResult.ok
            ? buildBondSubmitSuccessMessage(submitResult.data)
            : submitResult.error}
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {metricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} hint={card.hint} tone={card.tone} />
        ))}
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
            {detailLoading || detailRefreshing ? (
              <NoticeBanner variant="info">正在加载用户关系详情…</NoticeBanner>
            ) : detailView.errorMessage ? (
              <NoticeBanner variant="warning" title="用户详情暂不可用">
                {detailView.errorMessage}
              </NoticeBanner>
            ) : userDetail ? (
              <div className="space-y-3">
                {detailView.rows.map((row) => (
                  <KeyValueRow key={row.label} label={row.label} value={row.value} />
                ))}
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
