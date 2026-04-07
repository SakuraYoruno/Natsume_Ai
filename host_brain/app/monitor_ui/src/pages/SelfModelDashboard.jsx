import React, { useCallback, useEffect, useState } from 'react'
import { Brain, Heart, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react'
import { fetchDigitalLifeRuntimeSnapshot } from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import useAppStore from '../store/useAppStore.js'
import {
  asArray,
  compactText,
  formatNumber,
  formatStage,
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

function ListBlock({ title, items, emptyText }) {
  return (
    <div className="card p-5">
      <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
      <div className="mt-4 space-y-2">
        {items.length > 0 ? (
          items.map((item, index) => (
            <div key={`${title}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
              {compactText(item, 220)}
            </div>
          ))
        ) : (
          <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-sm text-gray-400">
            {emptyText}
          </div>
        )}
      </div>
    </div>
  )
}

export default function SelfModelDashboard() {
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

  const selfModel = runtimeSnapshot?.self_model_snapshot || {}
  const selfReflection = runtimeSnapshot?.self_reflection_pulse || {}
  const guardrails = selfReflection?.narrative_guardrails || {}
  const bondSummary = runtimeSnapshot?.bond_summary_human || {}
  const milestones = asArray(selfModel.growth_milestones)
  const personalityChanges = asArray(selfModel.personality_changes)
  const riskTags = asArray(guardrails.risk_tags)
  const promptDirectives = asArray(guardrails.prompt_directives)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Self Model</h1>
          <p className="text-sm text-gray-500">自我模型、反思脉冲、成长里程碑与叙事护栏</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <DigitalLifeNav />

      <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-violet-50 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              <Brain size={12} />
              人格与成长专题页
            </div>
            <div className="mt-3 text-2xl font-bold text-gray-900">
              {selfModel.recent_mood_trend || '平稳'}
            </div>
            <div className="mt-1 text-sm text-gray-600">
              {compactText(selfReflection.growth_note || selfReflection.self_awareness || '当前尚无明显成长摘要', 220)}
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-gray-500">
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              stage：{formatStage(runtimeSnapshot?.bond_state?.current_stage)}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              shared memory：{selfModel.shared_memory_count ?? 0}
            </span>
            <span className="rounded-full border border-gray-200 bg-white px-3 py-1">
              risk：{guardrails.risk_level || '—'}
            </span>
          </div>
        </div>
      </div>

      {error && <div className="card p-4 text-sm text-red-500">加载失败：{error}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="情绪稳定性"
          value={formatNumber(selfModel.emotional_stability, 2)}
          hint={selfModel.recent_mood_trend || '—'}
          tone="text-blue-600"
        />
        <StatCard
          label="共享记忆"
          value={selfModel.shared_memory_count ?? 0}
          hint={`最近深聊 ${selfModel.last_deep_talk_days_ago ?? 0} 天前`}
          tone="text-pink-600"
        />
        <StatCard
          label="成长里程碑"
          value={milestones.length}
          hint="milestones"
          tone="text-purple-600"
        />
        <StatCard
          label="护栏风险"
          value={guardrails.risk_level || '—'}
          hint={`${riskTags.length} 个风险标签`}
          tone={(guardrails.risk_level || '') === 'medium' ? 'text-orange-500' : 'text-green-600'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">自我模型</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="最近情绪趋势" value={selfModel.recent_mood_trend || '—'} />
            <InfoRow label="主导近期情绪" value={selfModel.dominant_recent_emotion || '—'} />
            <InfoRow label="关系成长感知" value={selfModel.bond_growth_perception || '—'} />
            <InfoRow label="共享记忆数" value={selfModel.shared_memory_count ?? 0} />
            <InfoRow label="最近深聊" value={selfModel.last_deep_talk_days_ago != null ? `${selfModel.last_deep_talk_days_ago} 天前` : '—'} />
            <InfoRow label="人格变化数" value={personalityChanges.length} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">自我反思脉冲</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="自我感知" value={selfReflection.self_awareness || '—'} />
            <InfoRow label="情绪感知" value={selfReflection.mood_awareness || '—'} />
            <InfoRow label="成长笔记" value={selfReflection.growth_note || '—'} />
            <InfoRow label="触发原因" value={selfReflection.trigger_reason || '—'} />
            <InfoRow label="允许风格" value={guardrails.allowed_style || '—'} />
            <InfoRow label="可扩展自叙事" value={guardrails.allow_self_narrative_expansion ? '是' : '否'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">叙事护栏</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="风险等级" value={guardrails.risk_level || '—'} />
            <InfoRow label="最大自叙事句数" value={guardrails.max_self_narrative_sentences ?? '—'} />
            <InfoRow label="护栏指令" value={guardrails.instruction || '—'} />
          </div>
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-gray-500">风险标签</div>
            <div className="flex flex-wrap gap-2">
              {riskTags.length > 0 ? (
                riskTags.map((item) => (
                  <span key={item} className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-xs font-medium text-orange-700">
                    {item}
                  </span>
                ))
              ) : (
                <span className="text-sm text-gray-400">暂无风险标签</span>
              )}
            </div>
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Heart size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Bond 叙事摘要</h2>
          </div>
          <div className="mt-4 space-y-3">
            <InfoRow label="阶段" value={bondSummary.stage || formatStage(runtimeSnapshot?.bond_state?.current_stage)} />
            <InfoRow label="趋势" value={bondSummary.trend || runtimeSnapshot?.bond_state?.trend || '—'} />
            <InfoRow label="能力提示" value={bondSummary.capability_hint || '—'} />
            <InfoRow label="共享记忆" value={bondSummary.shared_memory_count || '—'} />
            <InfoRow label="里程碑提示" value={bondSummary.milestone_hint || '—'} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ListBlock
          title="成长里程碑"
          items={milestones}
          emptyText="当前还没有明确的成长里程碑。"
        />
        <ListBlock
          title="人格变化"
          items={personalityChanges}
          emptyText="目前没有检测到可记录的人格变化。"
        />
        <ListBlock
          title="Prompt Directives"
          items={promptDirectives}
          emptyText="当前没有额外的 prompt directives。"
        />
      </div>
    </div>
  )
}
