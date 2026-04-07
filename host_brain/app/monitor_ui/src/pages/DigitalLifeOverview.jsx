import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Brain,
  ChevronRight,
  Database,
  FileText,
  Heart,
  MoonStar,
  Orbit,
  PersonStanding,
  RefreshCw,
  Settings,
  ShieldAlert,
  Sparkles,
  Wand2,
  Waves,
} from 'lucide-react'
import useAppStore from '../store/useAppStore.js'
import {
  fetchDigitalLifeRuntimeSnapshot,
  fetchMemoryStats,
  fetchPromptBuilderStats,
  fetchServicesHealth,
  fetchSubsystemsStats,
  fetchVtsStats,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import {
  classNames,
  compactText,
  formatNumber,
  formatStage,
  MODE_META,
} from '../utils/digitalLife.js'

const WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

const DOMAIN_STYLE = {
  wired: 'border-green-200 bg-green-50 text-green-700',
  degraded: 'border-orange-200 bg-orange-50 text-orange-700',
  planned: 'border-gray-200 bg-gray-50 text-gray-500',
}

function StatCard({ icon: Icon, label, value, hint, tone = 'gray' }) {
  const toneClass =
    tone === 'green'
      ? 'text-green-600'
      : tone === 'orange'
      ? 'text-orange-500'
      : tone === 'red'
      ? 'text-red-500'
      : tone === 'blue'
      ? 'text-blue-600'
      : 'text-gray-800'

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <Icon size={14} />
        <span>{label}</span>
      </div>
      <div className={classNames('mt-2 text-2xl font-bold', toneClass)}>{value ?? '—'}</div>
      <div className="mt-1 text-xs text-gray-400">{hint ?? '—'}</div>
    </div>
  )
}

function DomainCard({ icon: Icon, title, desc, status, actionTo, actionLabel }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-gray-100 text-gray-600">
            <Icon size={18} />
          </span>
          <div>
            <div className="text-sm font-semibold text-gray-800">{title}</div>
            <div className="mt-1 text-xs text-gray-500">{desc}</div>
          </div>
        </div>
        <span className={classNames('rounded-full border px-2 py-0.5 text-xs font-medium', DOMAIN_STYLE[status] || DOMAIN_STYLE.planned)}>
          {status === 'wired' ? '已接入' : status === 'degraded' ? '待收口' : '待接入'}
        </span>
      </div>
      {actionTo ? (
        <Link to={actionTo} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-700">
          {actionLabel || '查看'}
          <ChevronRight size={12} />
        </Link>
      ) : (
        <div className="mt-3 text-xs text-gray-400">后续补聚合接口与专页</div>
      )}
    </div>
  )
}

function DetailRow({ label, value, tone = 'text-gray-800' }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={classNames('text-sm font-medium text-right break-all', tone)}>{value ?? '—'}</span>
    </div>
  )
}

export default function DigitalLifeOverview() {
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [windowMinutes, setWindowMinutes] = useState(360)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [runtimeSnapshot, setRuntimeSnapshot] = useState(null)
  const [vtsData, setVtsData] = useState(null)
  const [memoryData, setMemoryData] = useState(null)
  const [promptData, setPromptData] = useState(null)
  const [servicesData, setServicesData] = useState(null)
  const [subsystemsData, setSubsystemsData] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [
        runtimeRes,
        vtsRes,
        memoryRes,
        promptRes,
        servicesRes,
        subsystemsRes,
      ] = await Promise.all([
        fetchDigitalLifeRuntimeSnapshot().catch(() => null),
        fetchVtsStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchMemoryStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchPromptBuilderStats({ window_minutes: windowMinutes }).catch(() => null),
        fetchServicesHealth({ window_minutes: windowMinutes }).catch(() => null),
        fetchSubsystemsStats({ window_minutes: windowMinutes }).catch(() => null),
      ])

      setRuntimeSnapshot(runtimeRes)
      setVtsData(vtsRes)
      setMemoryData(memoryRes)
      setPromptData(promptRes)
      setServicesData(servicesRes)
      setSubsystemsData(subsystemsRes)
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [windowMinutes])

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

  const currentMode = String(runtimeSnapshot?.sys_mode || 'normal').toLowerCase()
  const modeMeta = MODE_META[currentMode] || MODE_META.normal
  const topEmotion = vtsData?.top_emotions?.[0] || null
  const memorySubsystems = memoryData?.subsystems || []
  const avgMemorySuccessRate = memorySubsystems.length
    ? Math.round(
        memorySubsystems.reduce((sum, item) => sum + Number(item?.success_rate || 0), 0) / memorySubsystems.length
      )
    : null
  const activeSubsystemCount = (subsystemsData?.subsystems || []).filter((item) => (item?.total || 0) > 0).length
  const serviceDegraded = servicesData?.degraded ?? null
  const serviceDown = servicesData?.down ?? null
  const runtimeSummary = runtimeSnapshot?.runtime_summary || {}
  const currentState = runtimeSummary?.current_state || {}
  const offlineState = runtimeSnapshot?.offline_state || {}
  const silenceState = runtimeSnapshot?.silence_state || {}
  const bodySense = runtimeSnapshot?.body_sense || {}
  const innerWorldState = runtimeSnapshot?.inner_world_state || {}
  const selfReflectionPulse = runtimeSnapshot?.self_reflection_pulse || {}
  const selfModelSnapshot = runtimeSnapshot?.self_model_snapshot || {}
  const motiveState = runtimeSnapshot?.motive_state || {}
  const responsePolicy = runtimeSnapshot?.response_policy || {}
  const bondState = runtimeSnapshot?.bond_state || {}
  const slotStatus = runtimeSnapshot?.slot_status || {}
  const degradedReasons = runtimeSnapshot?.degraded_reasons || []

  const wiredDomains = useMemo(
    () => [
      {
        icon: Orbit,
        title: '运行模式',
        desc: `${modeMeta.emoji} ${modeMeta.label} · ${modeMeta.desc}`,
        status: 'wired',
        actionTo: '/runtime-mode',
        actionLabel: '管理模式',
      },
      {
        icon: Heart,
        title: '关系与 Bond',
        desc: bondState.current_stage
          ? `${formatStage(bondState.current_stage)} · event=${bondState.event_count ?? 0}`
          : 'Bond 已有后端能力，但首页聚合仍在补齐',
        status: bondState.current_stage ? 'wired' : 'degraded',
        actionTo: '/bond',
        actionLabel: '查看 Bond',
      },
      {
        icon: Waves,
        title: '情绪 / VTS',
        desc: topEmotion
          ? `主情绪 ${topEmotion.emotion_tag} · degrade=${vtsData?.degrade_rate ?? 0}%`
          : '已有 VTS 统计，但缺少统一情绪 SSOT 首页聚合',
        status: topEmotion ? 'wired' : 'degraded',
        actionTo: '/emotion',
        actionLabel: '查看情绪',
      },
      {
        icon: Database,
        title: '记忆系统',
        desc:
          avgMemorySuccessRate != null
            ? `近窗口平均成功率 ${avgMemorySuccessRate}% · ${memorySubsystems.length} 个子系统`
            : '已有 Memory Browser，但生命视角首页仅接入第一批摘要',
        status: avgMemorySuccessRate != null ? 'wired' : 'degraded',
        actionTo: '/memory',
        actionLabel: '查看记忆',
      },
      {
        icon: MoonStar,
        title: '沉默 / 回复策略',
        desc:
          silenceState.willingness_level
            ? `willingness=${silenceState.willingness_level} · posture=${runtimeSummary?.reply_posture?.style || '—'}`
            : '沉默机制已存在，但还没拿到运行时快照',
        status: silenceState.willingness_level ? 'wired' : 'degraded',
        actionTo: '/response-policy',
        actionLabel: '查看策略',
      },
    ],
    [avgMemorySuccessRate, bondState, memorySubsystems.length, modeMeta, runtimeSummary, silenceState, topEmotion, vtsData]
  )

  const deepDiveDomains = [
    {
      icon: Sparkles,
      title: 'Inner World',
      desc: innerWorldState.user_id
        ? `offline=${formatNumber(offlineState.offline_hours, 2)}h · anticipation=${(offlineState.anticipation_topics || []).length}`
        : '内心世界、独处、投射与夜间演化',
      status: innerWorldState.user_id ? 'wired' : 'degraded',
      actionTo: '/inner-world',
      actionLabel: '进入专题页',
    },
    {
      icon: PersonStanding,
      title: 'Body Sense',
      desc: bodySense.fatigue_level
        ? `fatigue=${bodySense.fatigue_level} · drowsiness=${bodySense.drowsiness_level || '—'}`
        : '疲惫、困倦、身体感与时段状态',
      status: bodySense.fatigue_level ? 'wired' : 'degraded',
      actionTo: '/response-policy',
      actionLabel: '进入专题页',
    },
    {
      icon: Brain,
      title: 'Self Model / Drift',
      desc: selfModelSnapshot.recent_mood_trend
        ? `mood=${selfModelSnapshot.recent_mood_trend} · shared_memory=${selfModelSnapshot.shared_memory_count ?? 0}`
        : '自我模型、人格漂移、特质守护',
      status: selfModelSnapshot.recent_mood_trend ? 'wired' : 'degraded',
      actionTo: '/self-model',
      actionLabel: '进入专题页',
    },
    {
      icon: Activity,
      title: 'Growth / Evolution',
      desc: compactText(selfReflectionPulse.growth_note || '成长、欲望、反思与长期演化', 96),
      status: selfReflectionPulse.growth_note ? 'wired' : 'degraded',
      actionTo: '/self-model',
      actionLabel: '进入专题页',
    },
  ]

  const operationDomains = [
    {
      icon: FileText,
      title: 'Prompt / Fallback',
      desc: promptData
        ? `build=${promptData.total_builds ?? 0} · high_risk=${promptData.high_risk_count ?? 0} · injection=${promptData.injection_detected_count ?? 0}`
        : '调主链提示词、守卫词和体验文案',
      status: 'wired',
      actionTo: '/prompt-config',
      actionLabel: '运营 Prompt',
    },
    {
      icon: Wand2,
      title: 'Persona 文案',
      desc: '调人格语气、自我叙事、主动表达与关系温度',
      status: 'wired',
      actionTo: '/persona-config',
      actionLabel: '运营 Persona',
    },
    {
      icon: Settings,
      title: '系统底座',
      desc: serviceDegraded == null && serviceDown == null
        ? '检查模型路由、检索对齐与底层服务状态'
        : `services degraded/down = ${serviceDegraded ?? 0}/${serviceDown ?? 0}`,
      status: 'wired',
      actionTo: '/system',
      actionLabel: '查看底座',
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">数字生命总览</h1>
          <p className="text-sm text-gray-500">
            先把夏目蓝放回首页。这里展示第一批已接入的数字生命状态，运维视角保留在 `Ops`。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="text-xs border rounded px-2 py-1.5"
            value={windowMinutes}
            onChange={(event) => setWindowMinutes(Number(event.target.value))}
          >
            {WINDOW_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button className="btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            刷新
          </button>
        </div>
      </div>

      <DigitalLifeNav />

      <div className="rounded-2xl border border-primary-100 bg-gradient-to-r from-primary-50 via-white to-pink-50 p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-white/80 px-3 py-1 text-xs font-medium text-primary">
              <Sparkles size={12} />
              WebUI v2 · 数字生命入口
            </div>
            <div className="mt-3 text-2xl font-bold text-gray-900">
              {modeMeta.emoji} {modeMeta.label}
            </div>
            <div className="mt-1 text-sm text-gray-600">{modeMeta.desc}</div>
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-gray-500">
              <span className="rounded-full bg-white px-3 py-1 border border-gray-200">
                Bond：{bondState.current_stage ? formatStage(bondState.current_stage) : '未接入首屏 SSOT'}
              </span>
              <span className="rounded-full bg-white px-3 py-1 border border-gray-200">
                主情绪：{topEmotion ? topEmotion.emotion_tag : '暂无'}
              </span>
              <span className="rounded-full bg-white px-3 py-1 border border-gray-200">
                活跃子系统：{activeSubsystemCount}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/persona-config" className="btn-secondary text-sm">
              Persona 运营
            </Link>
            <Link to="/prompt-config" className="btn-secondary text-sm">
              Prompt 配置
            </Link>
            <Link to="/overview" className="btn-secondary text-sm">
              打开 Ops
            </Link>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          icon={Orbit}
          label="运行模式"
          value={modeMeta.label}
          hint={`idle ${runtimeSnapshot?.sys_mode_status?.idle_minutes ?? '—'} min`}
          tone="blue"
        />
        <StatCard
          icon={Heart}
          label="Bond 阶段"
          value={bondState.current_stage ? formatStage(bondState.current_stage) : '—'}
          hint={bondState.current_stage ? `event=${bondState.event_count ?? 0} · trend=${bondState.trend || '—'}` : '暂无阶段快照'}
          tone="green"
        />
        <StatCard
          icon={Waves}
          label="主情绪 / VTS"
          value={topEmotion ? topEmotion.emotion_tag : '—'}
          hint={vtsData ? `degrade=${vtsData.degrade_rate ?? 0}% · allow=${vtsData.talk_gate_allowed ?? 0}` : '暂无统计'}
          tone={vtsData?.degrade_rate > 10 ? 'orange' : 'blue'}
        />
        <StatCard
          icon={ShieldAlert}
          label="系统风险"
          value={serviceDegraded == null && serviceDown == null ? '—' : `${serviceDegraded ?? 0}/${serviceDown ?? 0}`}
          hint="degraded / down"
          tone={(serviceDown ?? 0) > 0 ? 'red' : (serviceDegraded ?? 0) > 0 ? 'orange' : 'green'}
        />
      </div>

      {error && <div className="card p-4 text-sm text-red-500">加载失败：{error}</div>}

      <div>
        <div className="mb-3 flex items-center gap-2">
          <Sparkles size={15} className="text-primary" />
          <h2 className="text-sm font-semibold text-gray-800">第一批已接入</h2>
          <span className="text-xs text-gray-400">把已有能力重新组织成数字生命视角</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {wiredDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </div>

      <div>
        <div className="mb-3 flex items-center gap-2">
          <Brain size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">专题页 / 深入观察</h2>
          <span className="text-xs text-gray-400">这批页面已经建立，可从总览直接跳到具体生命域</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {deepDiveDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </div>

      <div>
        <div className="mb-3 flex items-center gap-2">
          <Wand2 size={15} className="text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-800">运营控制</h2>
          <span className="text-xs text-gray-400">把会影响人格和体验的可调项集中到 WebUI</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {operationDomains.map((item) => (
            <DomainCard key={item.title} {...item} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Database size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">记忆与人格摘要</h2>
          </div>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-gray-600">Memory 子系统数</span>
              <span className="font-semibold text-gray-800">{memorySubsystems.length || 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-600">平均成功率</span>
              <span className="font-semibold text-gray-800">
                {avgMemorySuccessRate != null ? `${avgMemorySuccessRate}%` : '—'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-600">PromptBuilder 构建</span>
              <span className="font-semibold text-gray-800">{promptData?.total_builds ?? '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-600">注入检测</span>
              <span className={classNames('font-semibold', (promptData?.injection_detected_count ?? 0) > 0 ? 'text-orange-500' : 'text-green-600')}>
                {promptData?.injection_detected_count ?? '—'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-gray-600">高风险构建</span>
              <span className={classNames('font-semibold', (promptData?.high_risk_count ?? 0) > 0 ? 'text-orange-500' : 'text-gray-800')}>
                {promptData?.high_risk_count ?? '—'}
              </span>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link to="/memory" className="btn-secondary text-sm">Memory</Link>
            <Link to="/prompt-config" className="btn-secondary text-sm">Prompts</Link>
            <Link to="/persona-config" className="btn-secondary text-sm">Persona</Link>
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <MoonStar size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">沉默 / 身体感 / 回复策略</h2>
          </div>
          <div className="mt-4 space-y-3">
            <DetailRow label="回复意愿" value={`${silenceState.response_willingness ?? '—'} / ${silenceState.willingness_level || '—'}`} />
            <DetailRow label="话题抗拒" value={silenceState.topic_resistance_active ? `是 · ${silenceState.resistant_topic || 'unknown'}` : '否'} />
            <DetailRow label="疲惫 / 困倦" value={`${bodySense.fatigue_level || '—'} / ${bodySense.drowsiness_level || '—'}`} />
            <DetailRow label="主导动机" value={motiveState.dominant_motive || '—'} tone="text-blue-600" />
            <DetailRow label="回复姿态" value={runtimeSummary?.reply_posture?.style || '—'} tone="text-purple-600" />
            <DetailRow
              label="策略限制"
              value={`chars=${responsePolicy.max_chars ?? '—'} · followup=${responsePolicy.allow_followup === false ? 'off' : 'on'} · shift=${responsePolicy.allow_topic_shift === false ? 'off' : 'on'}`}
            />
          </div>
          <div className="mt-4 rounded-xl border border-dashed border-gray-200 bg-gray-50 p-3 text-xs text-gray-500">
            这部分现在已经来自真实 runtime snapshot，不再是前端假状态。
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Inner World</h2>
          </div>
          <div className="mt-4 space-y-3">
            <DetailRow label="离线时长" value={offlineState.offline_hours != null ? `${formatNumber(offlineState.offline_hours, 2)}h` : '—'} />
            <DetailRow label="离线分层" value={offlineState.absence_band || currentState.absence_band || '—'} />
            <DetailRow label="anticipation topics" value={(offlineState.anticipation_topics || []).length} />
            <DetailRow label="drifted valence" value={formatNumber(innerWorldState.drifted_valence, 2)} />
            <DetailRow label="drifted arousal" value={formatNumber(innerWorldState.drifted_arousal, 2)} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Self Model</h2>
          </div>
          <div className="mt-4 space-y-3">
            <DetailRow label="最近情绪趋势" value={selfModelSnapshot.recent_mood_trend || '—'} />
            <DetailRow label="情绪稳定性" value={selfModelSnapshot.emotional_stability ?? '—'} />
            <DetailRow label="主导近期情绪" value={selfModelSnapshot.dominant_recent_emotion || '—'} />
            <DetailRow label="成长里程碑" value={(selfModelSnapshot.growth_milestones || []).length} />
            <DetailRow label="共享记忆" value={selfModelSnapshot.shared_memory_count ?? '—'} />
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2">
            <Activity size={15} className="text-gray-500" />
            <h2 className="text-sm font-semibold text-gray-800">Self Reflection</h2>
          </div>
          <div className="mt-4 space-y-3">
            <DetailRow label="自我感知" value={compactText(selfReflectionPulse.self_awareness, 96)} />
            <DetailRow label="成长笔记" value={compactText(selfReflectionPulse.growth_note, 96)} />
            <DetailRow label="触发原因" value={selfReflectionPulse.trigger_reason || '—'} />
            <DetailRow
              label="风险等级"
              value={selfReflectionPulse?.narrative_guardrails?.risk_level || '—'}
              tone={(selfReflectionPulse?.narrative_guardrails?.risk_level || '') === 'medium' ? 'text-orange-500' : 'text-gray-800'}
            />
          </div>
        </div>
      </div>

      {(degradedReasons.length > 0 || Object.values(slotStatus).some((value) => value !== 'live')) && (
        <div className="card p-5">
          <div className="flex items-center gap-2">
            <ShieldAlert size={15} className="text-orange-500" />
            <h2 className="text-sm font-semibold text-gray-800">快照降级 / 缺口</h2>
          </div>
          <div className="mt-4 space-y-2 text-sm text-gray-600">
            {degradedReasons.map((reason) => (
              <div key={reason}>- {reason}</div>
            ))}
            {degradedReasons.length === 0 && (
              <div>
                - {Object.entries(slotStatus)
                  .filter(([, value]) => value !== 'live')
                  .map(([key, value]) => `${key}:${value}`)
                  .join('，')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
