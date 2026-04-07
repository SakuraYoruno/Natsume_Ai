import React, { useCallback, useEffect, useState } from 'react'
import {
  Brain,
  HeartPulse,
  MessagesSquare,
  MoonStar,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { fetchDigitalLifeRuntimeSnapshot } from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import useAppStore from '../store/useAppStore.js'
import {
  asArray,
  classNames,
  compactText,
  formatBoolean,
  formatNumber,
} from '../utils/digitalLife.js'

function StatCard({ label, value, hint, tone = 'text-gray-900' }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-gray-500">{label}</div>
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

function TagList({ items, tone = 'gray' }) {
  if (!items.length) {
    return <div className="text-sm text-gray-400">暂无标签</div>
  }

  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span
          key={item}
          className={classNames(
            'rounded-full border px-2.5 py-1 text-xs font-medium',
            tone === 'orange'
              ? 'border-orange-200 bg-orange-50 text-orange-700'
              : tone === 'purple'
              ? 'border-purple-200 bg-purple-50 text-purple-700'
              : 'border-gray-200 bg-gray-50 text-gray-700'
          )}
        >
          {item}
        </span>
      ))}
    </div>
  )
}

export default function ResponsePolicyDashboard() {
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

  const silenceState = runtimeSnapshot?.silence_state || {}
  const bodySense = runtimeSnapshot?.body_sense || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const responsePolicy = runtimeSnapshot?.response_policy || {}
  const runtimeSummary = runtimeSnapshot?.runtime_summary || {}
  const whyReply = asArray(runtimeSummary.why_reply)
  const decisionChain = asArray(runtimeSummary.decision_chain)
  const policyReasonTags = asArray(responsePolicy.policy_reason_tags)
  const motiveReasonTags = asArray(motiveState.motive_reason_tags)
  const activeDrives = motiveState.active_drives || {}

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">回应策略</h1>
          <p className="text-sm text-gray-500">沉默机制、身体感、动机驱动与回复预算</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <DigitalLifeNav />

      <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-pink-50 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              <Sparkles size={12} />
              实时回复决策
            </div>
            <div className="mt-3 text-2xl font-bold text-gray-900">
              {runtimeSummary?.reply_posture?.style || 'focused'}
            </div>
            <div className="mt-1 text-sm text-gray-600">
              {compactText(runtimeSummary.headline || '当前未生成完整 headline', 200)}
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-gray-500">
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              willingness：{silenceState.willingness_level || '—'}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              followup：{responsePolicy.allow_followup === false ? 'off' : 'on'}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              dominant motive：{motiveState.dominant_motive || '—'}
            </span>
          </div>
        </div>
      </div>

      {error && <div className="card p-4 text-sm text-red-500">加载失败：{error}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="回复意愿"
          value={`${formatNumber(silenceState.response_willingness, 2)} / ${silenceState.willingness_level || '—'}`}
          hint="数值 / 档位"
          tone="text-blue-600"
        />
        <StatCard
          label="回复姿态"
          value={runtimeSummary?.reply_posture?.style || '—'}
          hint={`chars=${responsePolicy.max_chars ?? '—'}`}
          tone="text-purple-600"
        />
        <StatCard
          label="主导动机"
          value={motiveState.dominant_motive || '—'}
          hint={`${motiveReasonTags.length} 个 reason tags`}
          tone="text-pink-600"
        />
        <StatCard
          label="身体负荷"
          value={`${bodySense.fatigue_level || '—'} / ${bodySense.drowsiness_level || '—'}`}
          hint={`active=${formatBoolean(bodySense.is_active)}`}
          tone="text-orange-500"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MoonStar size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">沉默机制</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="回复意愿" value={formatNumber(silenceState.response_willingness, 3)} />
            <InfoRow label="意愿等级" value={silenceState.willingness_level || '—'} />
            <InfoRow label="话题抗拒" value={silenceState.topic_resistance_active ? `是 · ${silenceState.resistant_topic || 'unknown'}` : '否'} />
            <InfoRow label="沉默提示" value={silenceState.silence_hint || '—'} />
            <InfoRow label="触发原因" value={silenceState.trigger_reason || '—'} />
            <InfoRow label="抗拒提示" value={silenceState.topic_resistance_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <HeartPulse size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">身体感</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="疲惫" value={`${formatNumber(bodySense.fatigue, 2)} / ${bodySense.fatigue_level || '—'}`} />
            <InfoRow label="困倦" value={`${formatNumber(bodySense.drowsiness, 2)} / ${bodySense.drowsiness_level || '—'}`} />
            <InfoRow label="孤独感" value={`${formatNumber(bodySense.loneliness, 2)} / ${bodySense.loneliness_level || '—'}`} />
            <InfoRow label="意愿惩罚" value={formatNumber(bodySense.willingness_penalty, 2)} />
            <InfoRow label="活跃中" value={formatBoolean(bodySense.is_active)} />
            <InfoRow label="身体提示" value={bodySense.body_sense_hint || '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MessagesSquare size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">回复策略</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="字数预算" value={`${responsePolicy.max_chars ?? '—'} chars`} />
            <InfoRow label="句数预算" value={`${responsePolicy.min_sentences ?? '—'} ~ ${responsePolicy.max_sentences ?? '—'}`} />
            <InfoRow label="允许追问" value={formatBoolean(responsePolicy.allow_followup)} />
            <InfoRow label="允许换题" value={formatBoolean(responsePolicy.allow_topic_shift)} />
            <InfoRow label="允许答后主动" value={formatBoolean(responsePolicy.allow_proactive_after_reply)} />
            <InfoRow label="偏收敛回复" value={formatBoolean(responsePolicy.should_be_withdrawn)} />
            <InfoRow label="warmth floor" value={formatNumber(responsePolicy.warmth_floor, 2)} />
            <InfoRow label="intimacy ceiling" value={formatNumber(responsePolicy.intimacy_ceiling, 2)} />
            <InfoRow label="humor ceiling" value={formatNumber(responsePolicy.humor_ceiling, 2)} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">动机与决策链</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="dominant motive" value={motiveState.dominant_motive || '—'} />
            <InfoRow label="reach_out" value={formatNumber(activeDrives.reach_out_drive, 2)} />
            <InfoRow label="continuity" value={formatNumber(activeDrives.continuity_drive, 2)} />
            <InfoRow label="withdraw" value={formatNumber(activeDrives.withdraw_drive, 2)} />
            <InfoRow label="self_protect" value={formatNumber(activeDrives.self_protect_drive, 2)} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-5 lg:col-span-1">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Policy Tags</h2>
          </div>
          <TagList items={policyReasonTags} tone="orange" />
        </div>

        <div className="card p-5 lg:col-span-1">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Motive Tags</h2>
          </div>
          <TagList items={motiveReasonTags} tone="purple" />
        </div>

        <div className="card p-5 lg:col-span-1">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Why Reply</h2>
          </div>
          <TagList items={whyReply} />
        </div>
      </div>

      <div className="card p-5">
        <div className="mb-3 flex items-center gap-2">
          <Sparkles size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">Decision Chain</h2>
        </div>
        <div className="space-y-2">
          {decisionChain.length > 0 ? (
            decisionChain.map((item, index) => (
              <div key={`${item}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
                {item}
              </div>
            ))
          ) : (
            <div className="text-sm text-gray-400">暂无决策链信息</div>
          )}
        </div>
      </div>
    </div>
  )
}
