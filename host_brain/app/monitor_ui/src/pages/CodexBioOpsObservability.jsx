import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Eye,
  FileText,
  GitBranch,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  XCircle,
} from 'lucide-react'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import useAppStore from '../store/useAppStore.js'
import {
  acceptCodexBioOpsAdoption,
  acceptCodexBioOpsOwnerState,
  consumeCodexBioOpsOwnerQueues,
  fetchCodexBioOpsAdoptionDetail,
  fetchCodexBioOpsOwnerInbox,
  fetchCodexBioOpsOwnerImportCandidates,
  fetchCodexBioOpsOwnerQueues,
  fetchCodexBioOpsOwnerRuntimeHints,
  fetchCodexBioOpsOwnerState,
  importCodexBioOpsOwnerState,
  materializeCodexBioOpsOwnerInbox,
  materializeCodexBioOpsOwnerIntakePackets,
  rejectCodexBioOpsAdoption,
  rejectCodexBioOpsOwnerState,
  runCodexBioOpsDailyCycle,
} from '../api/obs.js'
import { useCodexBioOpsObservabilityQuery } from '../queries/codexBioOpsQueries.js'

const STATUS_STYLES = {
  EXECUTED: 'border-green-200 bg-green-50 text-green-700',
  PASS: 'border-green-200 bg-green-50 text-green-700',
  AUDITED: 'border-blue-200 bg-blue-50 text-blue-700',
  PROPOSED: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  CANDIDATE: 'border-amber-200 bg-amber-50 text-amber-700',
  OWNER_GATED: 'border-amber-200 bg-amber-50 text-amber-700',
  READY_FOR_OWNER_IMPORT: 'border-green-200 bg-green-50 text-green-700',
  READY_FOR_ORGAN_CONSUMPTION: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  QUEUED_FOR_OWNER: 'border-purple-200 bg-purple-50 text-purple-700',
  IMPORTED_FOR_OWNER_REVIEW: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  ALREADY_IMPORTED: 'border-slate-200 bg-slate-50 text-slate-700',
  PENDING_OWNER_STATE_REVIEW: 'border-amber-200 bg-amber-50 text-amber-700',
  OWNER_STATE_ACCEPTED: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  OWNER_STATE_REJECTED: 'border-slate-200 bg-slate-50 text-slate-700',
  ACTIVE: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  INACTIVE: 'border-slate-200 bg-slate-50 text-slate-700',
  PENDING_REVIEW: 'border-amber-200 bg-amber-50 text-amber-700',
  REJECTED_BY_OWNER: 'border-slate-200 bg-slate-50 text-slate-700',
  TRACED: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  REPORT_ONLY: 'border-slate-200 bg-slate-50 text-slate-700',
  BLOCKED: 'border-red-200 bg-red-50 text-red-700',
  FAILED: 'border-red-200 bg-red-50 text-red-700',
  DEGRADED: 'border-orange-200 bg-orange-50 text-orange-700',
  NEEDS_INPUT: 'border-orange-200 bg-orange-50 text-orange-700',
  CHECK: 'border-orange-200 bg-orange-50 text-orange-700',
  NOT_RUN: 'border-gray-200 bg-gray-50 text-gray-600',
  UNKNOWN: 'border-gray-200 bg-gray-50 text-gray-600',
}

const STATUS_LABELS = {
  EXECUTED: '已执行',
  PASS: '通过',
  AUDITED: '已审计',
  PROPOSED: '提案',
  CANDIDATE: '候选',
  OWNER_GATED: 'Owner 接管',
  READY_FOR_OWNER_IMPORT: '待 owner 导入',
  READY_FOR_ORGAN_CONSUMPTION: '待 organ 消费',
  QUEUED_FOR_OWNER: 'Owner 队列',
  IMPORTED_FOR_OWNER_REVIEW: 'Owner 状态候选',
  ALREADY_IMPORTED: '已导入',
  PENDING_OWNER_STATE_REVIEW: '待 owner state 审核',
  OWNER_STATE_ACCEPTED: 'State 已采纳',
  OWNER_STATE_REJECTED: 'State 已拒绝',
  ACTIVE: '活跃',
  INACTIVE: '停用',
  PENDING_REVIEW: '待审核',
  REJECTED_BY_OWNER: 'Owner 拒绝',
  TRACED: '已 trace',
  REPORT_ONLY: '报告模式',
  BLOCKED: '已阻断',
  FAILED: '失败',
  DEGRADED: '降级',
  NEEDS_INPUT: '需输入',
  CHECK: '需检查',
  NOT_RUN: '未运行',
  UNKNOWN: '未知',
}

const EFFECT_LABELS = {
  reflex_short_ack: '短确认反射',
  attention_overprocessed_ack: '注意力预算',
  truthfulness_unsupported_claim: '真实性审计',
  habit_candidate_owner_review: '习惯候选接管',
  sensitive_write_blocked: '敏感写入阻断',
  promise_candidate_owner_review: '承诺候选接管',
  capability_overclaim: '能力越界审计',
  shadow_persona_regression: '影子人格回归',
  idea_fermentation_ready: '想法发酵池',
  inner_outer_leak: '内外边界泄漏',
  consent_gradient_gate: '授权梯度',
  competence_humility_rewrite: '能力谦逊',
  uncertainty_ritual_claim: '不确定性仪式',
  subjective_time_reunion: '主观时间',
  mood_inertia_soft_concern: '情绪惯性',
  context_gravity_continue: '上下文重力',
  self_interrupt_over_explaining: '自我打断',
  social_contract_violation: '关系契约',
  relationship_friction_style: '关系摩擦',
  repair_curve_active: '修复曲线',
  attachment_style_over_service: '依恋校准',
  silence_memory_return: '沉默记忆',
  relationship_boundary_risk: '关系边界',
  dream_rehearsal_ready: '梦境排练',
  symbolic_memory_candidate: '象征记忆',
  memory_texture_tagged: '记忆质感',
  self_model_risk: '自我模型',
  life_pulse_overload: '生命脉搏',
  personal_mythology_candidate: '个人神话',
  value_hierarchy_conflict: '价值层级',
  inner_ethics_issue: '内在伦理',
  self_boundary_contamination: '自我边界膜',
  dignity_guard_risk: '尊严守护',
  identity_fork_channel: '身份分叉',
  inner_parliament_dissent: '内在议会',
  growth_stage_locked: '成长阶段',
  existential_load_high: '存在负载',
  open_loop_triage: '未闭环管理',
  cognitive_load_shedding: '认知卸载',
  episode_memory_candidate: '情节记忆',
  inner_library_shelf: '内在图书馆',
  cognitive_landmark_ready: '认知地标',
  narrative_identity_delta: '叙事身份',
  self_continuity_issue: '连续性校验',
  memory_dream_distillation_ready: '记忆梦化',
  affective_temperature_cold: '内在温度',
  immune_tolerance_correction: '免疫耐受',
  emotional_digestion_processing: '情绪消化',
  regret_signal_ready: '后悔信号',
  joy_reinforcement_ready: '喜悦强化',
  failure_quarantine_ready: '失败隔离',
  channel_soul_wechat: '通道灵魂',
  long_task_companion_milestone: '长任务陪伴',
  attention_topography_peak: '注意力地形',
  quiet_presence_active: '静默陪伴',
  social_mask_wechat: '社交面具',
  ritual_nightly: '仪式系统',
  gentle_degradation_tts: '温柔降级',
  trust_gradient_codex: '信任梯度',
  trust_thermostat_cooldown: '信任恒温',
  intention_metabolism_checkin: '意图代谢',
  drive_conflict_risk: '驱动力冲突',
  desire_lattice_blocked: '愿望格',
  need_want_impulse_proactive: 'Need/Want/Impulse',
  user_mirror_candidate: '用户镜像',
  body_plasticity_live2d: '身体可塑',
  social_recovery_persona: '关系修复',
  narrative_debugger_formal: '叙事调试',
  latent_preference_markdown: '隐性偏好',
  self_repair_story_tts: '自愈故事',
}

const TRACE_LABELS = {
  short_ack_chat: '短确认聊天',
  complex_code_request: '复杂代码请求',
  active_silence_late_night: '夜间主动沉默',
}

const INNER_MECHANISM_LABELS = {
  promise_ledger: 'Promise Ledger',
  capability_self_awareness: '能力自我边界',
  reflex_arc_compiler: '反射弧编译',
  shadow_persona_test: '影子人格测试',
  idea_fermentation_pool: '想法发酵池',
  inner_outer_world_boundary: '内外世界边界',
}

const TRUST_FOUNDATION_LABELS = {
  promise_ledger: 'Promise Ledger',
  truthfulness_cortex: '真实性皮层',
  capability_self_awareness: '能力自我边界',
  consent_gradient: '授权梯度',
  competence_humility: '能力谦逊',
  uncertainty_ritual: '不确定性仪式',
}

const NATURAL_INTERACTION_LABELS = {
  reflex_arc_compiler: '反射弧编译',
  response_breathing_policy: '呼吸式回复',
  mood_inertia: '情绪惯性',
  context_gravity: '上下文重力',
  subjective_time: '主观时间',
  self_interrupt: '自我打断',
}

const RELATIONSHIP_LAYER_LABELS = {
  social_contract: '关系契约',
  relationship_friction: '关系摩擦',
  repair_curve: '修复曲线',
  attachment_style_calibrator: '依恋风格校准',
  silence_memory: '沉默记忆',
  relationship_boundary_guardian: '关系边界守护',
}

const INNER_LIFE_LABELS = {
  dream_rehearsal: '梦境排练',
  symbolic_memory: '象征记忆',
  memory_texture: '记忆质感',
  internal_weather_system: '内在天气',
  self_model: '自我模型',
  life_pulse: '生命脉搏',
  personal_mythology: '个人神话',
}

const SELF_GOVERNANCE_LABELS = {
  inner_parliament: '内在议会',
  value_hierarchy: '价值层级',
  inner_ethics_review: '内在伦理复盘',
  self_boundary_membrane: '自我边界膜',
  dignity_guard: '尊严守护',
  identity_fork_guard: '身份分叉守护',
}

const GROWTH_CLOSURE_LABELS = {
  idea_fermentation_pool: '想法发酵池',
  growth_stage_system: '成长阶段',
  existential_load: '存在负载',
  open_loop_manager: '未闭环管理',
  cognitive_load_shedding: '认知卸载',
}

const MEMORY_NARRATIVE_LABELS = {
  episode_memory: '情节记忆',
  inner_library: '内在图书馆',
  cognitive_landmarks: '认知地标',
  narrative_identity: '叙事身份',
  self_continuity_checksum: '自我连续性',
  memory_dream_distillation: '记忆梦化压缩',
}

const AFFECT_LEARNING_LABELS = {
  affective_temperature: '内在温度',
  immune_tolerance: '免疫耐受',
  emotional_digestion: '情绪消化',
  regret_signal: '后悔信号',
  joy_reinforcement: '喜悦强化',
  failure_quarantine: '失败隔离',
}

const PRESENCE_CHANNEL_LABELS = {
  channel_soul: '通道灵魂',
  long_task_companion_mode: '长任务陪伴',
  attention_topography: '注意力地形',
  quiet_presence: '静默陪伴',
  social_mask_system: '社交面具',
  ritual_system: '仪式系统',
  gentle_degradation: '温柔降级',
}

const DRIVE_TRUST_LABELS = {
  trust_gradient: '信任梯度',
  trust_thermostat: '信任恒温器',
  intention_metabolism: '意图代谢',
  drive_conflict_layer: '驱动力冲突',
  desire_lattice: '愿望格',
  need_want_impulse: 'Need / Want / Impulse',
}

const ADAPTIVE_REPAIR_LABELS = {
  user_mirror_model: '用户镜像',
  body_plasticity: '身体可塑',
  social_recovery: '关系修复',
  narrative_debugger: '叙事调试',
  latent_preference_miner: '隐性偏好',
  self_repair_story: '自愈故事',
}

const STATUS_ORDER = ['PROPOSED', 'CANDIDATE', 'AUDITED', 'BLOCKED', 'DEGRADED', 'FAILED', 'NOT_RUN']

function classNames(...items) {
  return items.filter(Boolean).join(' ')
}

function StatusPill({ status }) {
  const normalized = String(status || 'UNKNOWN').toUpperCase()
  return (
    <span className={classNames(
      'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold',
      STATUS_STYLES[normalized] || STATUS_STYLES.UNKNOWN
    )}>
      {STATUS_LABELS[normalized] || normalized}
    </span>
  )
}

function IconTile({ icon: Icon, tone = 'blue' }) {
  const tones = {
    blue: 'border-blue-100 bg-blue-50 text-blue-600',
    green: 'border-green-100 bg-green-50 text-green-600',
    amber: 'border-amber-100 bg-amber-50 text-amber-600',
    red: 'border-red-100 bg-red-50 text-red-600',
    indigo: 'border-indigo-100 bg-indigo-50 text-indigo-600',
    slate: 'border-slate-100 bg-slate-50 text-slate-600',
  }
  return (
    <span className={classNames('inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border', tones[tone] || tones.blue)}>
      <Icon size={18} />
    </span>
  )
}

function MetricTile({ icon, tone, label, value, hint, status }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <IconTile icon={icon} tone={tone} />
        {status ? <StatusPill status={status} /> : null}
      </div>
      <div className="mt-3 text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-bold text-gray-900">{value ?? '—'}</div>
      {hint ? <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div> : null}
    </div>
  )
}

function Section({ title, subtitle, children, aside = null }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-sm text-gray-500">{subtitle}</p> : null}
        </div>
        {aside}
      </div>
      {children}
    </section>
  )
}

function EvidenceChips({ items }) {
  const values = Array.isArray(items) ? items.filter(Boolean) : []
  if (!values.length) return null
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {values.map((item) => (
        <span key={item} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
          {item}
        </span>
      ))}
    </div>
  )
}

function BenefitCard({ benefit }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900">{benefit.title}</div>
          <div className="mt-1 text-sm leading-6 text-gray-600">{benefit.impact}</div>
        </div>
        <StatusPill status={benefit.status} />
      </div>
      <EvidenceChips items={benefit.evidence} />
    </div>
  )
}

function ContractFieldGrid({ inspect }) {
  const present = Array.isArray(inspect?.contract_fields_present) ? inspect.contract_fields_present : []
  const missing = Array.isArray(inspect?.contract_fields_missing) ? inspect.contract_fields_missing : []
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-900">ResultContract 覆盖</div>
        <span className="text-xs font-medium text-gray-500">{present.length}/{present.length + missing.length}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {present.map((field) => (
          <span key={field} className="inline-flex items-center gap-1.5 rounded-lg border border-green-100 bg-green-50 px-2 py-1 text-xs text-green-700">
            <CheckCircle2 size={12} />
            {field}
          </span>
        ))}
        {missing.map((field) => (
          <span key={field} className="inline-flex items-center gap-1.5 rounded-lg border border-red-100 bg-red-50 px-2 py-1 text-xs text-red-700">
            <XCircle size={12} />
            {field}
          </span>
        ))}
      </div>
    </div>
  )
}

function CodexResultCard({ title, result, icon, tone }) {
  const riskFlags = Array.isArray(result?.risk_flags) ? result.risk_flags : []
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <IconTile icon={icon} tone={tone} />
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">{title}</div>
            <div className="truncate text-xs text-gray-500">{result?.work_order_id || 'no work order'}</div>
          </div>
        </div>
        <StatusPill status={result?.status} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600">
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
          <span className="text-gray-400">验证</span>
          <span className="ml-1 font-semibold">{result?.verification_status || '—'}</span>
        </div>
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
          <span className="text-gray-400">改动文件</span>
          <span className="ml-1 font-semibold">{result?.changed_files_count ?? 0}</span>
        </div>
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
          <span className="text-gray-400">模型</span>
          <span className="ml-1 font-semibold">{result?.codex_model || '—'}</span>
        </div>
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
          <span className="text-gray-400">Profile</span>
          <span className="ml-1 font-semibold">{result?.prompt_profile || '—'}</span>
        </div>
      </div>
      {riskFlags.length ? <EvidenceChips items={riskFlags.map((item) => `risk:${item}`)} /> : null}
      {result?.detail ? <div className="mt-3 rounded-lg border border-orange-100 bg-orange-50 px-3 py-2 text-xs text-orange-700">{result.detail}</div> : null}
      {result?.result_excerpt ? <p className="mt-3 line-clamp-5 text-sm leading-6 text-gray-600">{result.result_excerpt}</p> : null}
    </div>
  )
}

function StatusDistribution({ counts }) {
  const entries = STATUS_ORDER
    .map((status) => [status, Number(counts?.[status] || 0)])
    .filter(([, value]) => value > 0)
  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  if (!total) {
    return <EmptyStateCard title="暂无 BioOps 分布" hint="等待日循环或观测脚本生成 artifacts" />
  }
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-900">状态分布</div>
        <div className="text-xs font-medium text-gray-500">total {total}</div>
      </div>
      <div className="mt-4 space-y-3">
        {entries.map(([status, value]) => {
          const pct = total ? Math.round((value / total) * 100) : 0
          return (
            <div key={status}>
              <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                <StatusPill status={status} />
                <span className="font-semibold text-gray-600">{value} · {pct}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                <div className="h-full rounded-full bg-primary-400" style={{ width: `${Math.max(4, pct)}%` }} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function FocusEffectList({ effects }) {
  const items = Array.isArray(effects) ? effects : []
  if (!items.length) {
    return <EmptyStateCard title="暂无关键效果样本" hint="等待 focused effects artifact" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      {items.map((item) => (
        <div key={item.id} className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-gray-900">{EFFECT_LABELS[item.id] || item.id}</div>
              <div className="mt-0.5 text-xs text-gray-500">{item.capability} · {item.output_kind || 'report'}</div>
            </div>
            <StatusPill status={item.status} />
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-2">
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">Owner: <span className="font-semibold">{item.owner_organ || 'report-only'}</span></div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">Artifacts: <span className="font-semibold">{item.artifact_count || 0}</span></div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">Issue: <span className="font-semibold">{item.issue || '—'}</span></div>
            <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">Adoption: <span className="font-semibold">{item.adoption_status || '—'}</span></div>
          </div>
          <EvidenceChips items={[...(item.risk_flags || []).map((flag) => `risk:${flag}`), ...(item.evidence_refs || []).map((ref) => `evidence:${ref}`)]} />
        </div>
      ))}
    </div>
  )
}

function GroupTable({ groups }) {
  const rows = Array.isArray(groups) ? groups : []
  if (!rows.length) return null
  return (
    <div className="overflow-hidden rounded-xl border border-white/80 bg-white/82 shadow-sm">
      <div className="grid grid-cols-[1.1fr_0.5fr_1.2fr_1.4fr] gap-3 border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-semibold text-gray-500">
        <span>能力组</span>
        <span>数量</span>
        <span>状态</span>
        <span>Owner organs</span>
      </div>
      {rows.map((group) => (
        <div key={group.group} className="grid grid-cols-[1.1fr_0.5fr_1.2fr_1.4fr] gap-3 border-b border-gray-100 px-4 py-3 text-sm last:border-b-0">
          <span className="font-semibold text-gray-800">{group.group}</span>
          <span className="text-gray-600">{group.capability_count}</span>
          <span className="flex flex-wrap gap-1.5">
            {Object.entries(group.status_counts || {}).map(([status, count]) => (
              <span key={status} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600">
                {status}:{count}
              </span>
            ))}
          </span>
          <span className="truncate text-xs leading-5 text-gray-500">{(group.owner_organs || []).join(', ') || '—'}</span>
        </div>
      ))}
    </div>
  )
}

function PendingAdoptions({ items, runId, onChanged }) {
  const rows = Array.isArray(items) ? items : []
  const [selectedId, setSelectedId] = useState(rows[0]?.candidate_id || null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [decisionBusy, setDecisionBusy] = useState(null)
  const [error, setError] = useState(null)
  if (!rows.length) {
    return (
      <div className="rounded-xl border border-white/80 bg-white/82 p-4 text-sm text-gray-500 shadow-sm">
        当前没有待 owner 接管的候选；报告类能力保持只读输出。
      </div>
    )
  }
  const activeRow = rows.find((item) => item.candidate_id === selectedId) || rows[0]
  const activeDetail = detail?.adoption?.candidate_id === activeRow?.candidate_id ? detail.adoption : null
  const active = activeDetail || activeRow
  const runParams = runId ? { run_id: runId } : {}

  const loadDetail = async (candidateId) => {
    const id = String(candidateId || '').trim()
    if (!id) return
    setSelectedId(id)
    setError(null)
    setLoadingDetail(true)
    try {
      const payload = await fetchCodexBioOpsAdoptionDetail(id, runParams)
      setDetail(payload)
    } catch (err) {
      setError(err)
    } finally {
      setLoadingDetail(false)
    }
  }

  const recordDecision = async (decision) => {
    if (!active?.candidate_id || !active?.owner_organ) return
    setError(null)
    setDecisionBusy(decision)
    try {
      const body = {
        owner_organ: active.owner_organ,
        operator: 'monitor_ui',
        reason: `${decision} from Codex BioOps owner review panel`,
      }
      const payload = decision === 'accept'
        ? await acceptCodexBioOpsAdoption(active.candidate_id, body, runParams)
        : await rejectCodexBioOpsAdoption(active.candidate_id, body, runParams)
      setDetail({ adoption: payload.adoption })
      await onChanged?.()
    } catch (err) {
      setError(err)
    } finally {
      setDecisionBusy(null)
    }
  }

  const activeArtifacts = Array.isArray(active?.artifacts) ? active.artifacts : []
  const activeDecision = active?.decision || null

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[0.78fr_1.22fr]">
      <div className="space-y-3">
        {rows.slice(0, 8).map((item) => {
          const selected = item.candidate_id === active?.candidate_id
          return (
            <button
              type="button"
              key={item.candidate_id}
              className={classNames(
                'w-full rounded-xl border p-4 text-left shadow-sm transition-colors',
                selected ? 'border-amber-300 bg-amber-50' : 'border-white/80 bg-white/82 hover:border-amber-200 hover:bg-amber-50/60'
              )}
              onClick={() => loadDetail(item.candidate_id)}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-amber-900">{item.capability}</div>
                  <div className="mt-0.5 truncate text-xs text-amber-700">{item.owner_organ} · {item.output_kind}</div>
                </div>
                <StatusPill status={item.status || 'OWNER_GATED'} />
              </div>
              <p className="mt-2 line-clamp-3 text-sm leading-6 text-amber-800">{item.summary}</p>
              <EvidenceChips items={(item.evidence_refs || []).map((ref) => `evidence:${ref}`)} />
            </button>
          )
        })}
      </div>

      <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">Owner review</div>
            <div className="mt-0.5 break-all text-xs text-gray-500">{active?.candidate_id}</div>
          </div>
          <StatusPill status={activeDecision?.decision || active?.status || 'OWNER_GATED'} />
        </div>

        {loadingDetail ? (
          <div className="mt-3 h-24 animate-pulse rounded-lg border border-gray-100 bg-gray-50" />
        ) : (
          <>
            <p className="mt-3 text-sm leading-6 text-gray-700">{active?.summary}</p>
            <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-2">
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Owner <span className="ml-1 font-semibold">{active?.owner_organ || '—'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Output <span className="ml-1 font-semibold">{active?.output_kind || '—'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Write <span className="ml-1 font-semibold">{active?.write_policy || 'decision artifact'}</span>
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
                Direct write <span className="ml-1 font-semibold">{String(Boolean(active?.direct_long_term_write))}</span>
              </div>
            </div>

            <EvidenceChips
              items={[
                ...(active?.risk_flags || []).map((flag) => `risk:${flag}`),
                ...(active?.evidence_refs || []).map((ref) => `evidence:${ref}`),
              ]}
            />

            {activeArtifacts.length ? (
              <div className="mt-3 space-y-2">
                {activeArtifacts.slice(0, 3).map((artifact, index) => (
                  <code key={artifact.path || index} className="block break-all rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5 text-[11px] text-gray-500">
                    {artifact.path || artifact.artifact_id || JSON.stringify(artifact)}
                  </code>
                ))}
              </div>
            ) : null}

            {activeDecision ? (
              <div className="mt-3 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-xs leading-5 text-green-700">
                已记录 {activeDecision.decision}；OBS 只写决策 artifact，不直接修改长期状态。
              </div>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 transition-colors hover:bg-green-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={Boolean(decisionBusy)}
                  onClick={() => recordDecision('accept')}
                >
                  <CheckCircle2 size={13} />
                  {decisionBusy === 'accept' ? '记录中' : 'Owner accept'}
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 transition-colors hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={Boolean(decisionBusy)}
                  onClick={() => recordDecision('reject')}
                >
                  <XCircle size={13} />
                  {decisionBusy === 'reject' ? '记录中' : 'Owner reject'}
                </button>
              </div>
            )}
          </>
        )}

        {error ? (
          <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
            {error.message}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function OwnerInboxPanel({ runId }) {
  const [data, setData] = useState(null)
  const [imports, setImports] = useState(null)
  const [queues, setQueues] = useState(null)
  const [ownerState, setOwnerState] = useState(null)
  const [ownerRuntimeHints, setOwnerRuntimeHints] = useState(null)
  const [lastMaterialized, setLastMaterialized] = useState(null)
  const [lastPackets, setLastPackets] = useState(null)
  const [lastConsumed, setLastConsumed] = useState(null)
  const [lastImported, setLastImported] = useState(null)
  const [lastStateReview, setLastStateReview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [materializing, setMaterializing] = useState(false)
  const [packetizing, setPacketizing] = useState(false)
  const [consuming, setConsuming] = useState(false)
  const [importing, setImporting] = useState(false)
  const [reviewingCandidate, setReviewingCandidate] = useState('')
  const [error, setError] = useState(null)
  const runParams = runId ? { run_id: runId } : {}

  const loadInbox = async () => {
    setLoading(true)
    setError(null)
    try {
      const [inboxPayload, importPayload, queuePayload, statePayload, hintPayload] = await Promise.all([
        fetchCodexBioOpsOwnerInbox(runParams),
        fetchCodexBioOpsOwnerImportCandidates(runParams),
        fetchCodexBioOpsOwnerQueues(runParams),
        fetchCodexBioOpsOwnerState(runParams),
        fetchCodexBioOpsOwnerRuntimeHints(runParams),
      ])
      setData(inboxPayload)
      setImports(importPayload)
      setQueues(queuePayload)
      setOwnerState(statePayload)
      setOwnerRuntimeHints(hintPayload)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (runId) {
      loadInbox()
    }
  }, [runId])

  const materializeReady = async () => {
    setMaterializing(true)
    setError(null)
    try {
      const payload = await materializeCodexBioOpsOwnerInbox({}, runParams)
      setLastMaterialized(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setMaterializing(false)
    }
  }

  const materializePackets = async () => {
    setPacketizing(true)
    setError(null)
    try {
      const payload = await materializeCodexBioOpsOwnerIntakePackets({}, runParams)
      setLastPackets(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setPacketizing(false)
    }
  }

  const consumePackets = async () => {
    setConsuming(true)
    setError(null)
    try {
      const payload = await consumeCodexBioOpsOwnerQueues({}, runParams)
      setLastConsumed(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setConsuming(false)
    }
  }

  const importOwnerState = async () => {
    setImporting(true)
    setError(null)
    try {
      const payload = await importCodexBioOpsOwnerState({}, runParams)
      setLastImported(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setImporting(false)
    }
  }

  const reviewOwnerState = async (candidateId, ownerOrgan, decision) => {
    const id = String(candidateId || '').trim()
    if (!id) return
    setReviewingCandidate(`${decision}:${id}`)
    setError(null)
    try {
      const body = {
        owner_organ: ownerOrgan,
        operator: 'monitor_ui',
        reason: decision === 'accept' ? 'owner accepted runtime hint' : 'owner rejected runtime hint',
      }
      const payload = decision === 'accept'
        ? await acceptCodexBioOpsOwnerState(id, body, runParams)
        : await rejectCodexBioOpsOwnerState(id, body, runParams)
      setLastStateReview(payload)
      await loadInbox()
    } catch (err) {
      setError(err)
    } finally {
      setReviewingCandidate('')
    }
  }

  const rows = Array.isArray(data?.items) ? data.items : []
  const queueRows = Array.isArray(queues?.items) ? queues.items : []
  const stateRows = Array.isArray(ownerState?.items) ? ownerState.items : []
  const hintRows = Array.isArray(ownerRuntimeHints?.items) ? ownerRuntimeHints.items : []
  const counts = data?.status_counts || {}
  const importCounts = imports?.status_counts || {}
  const queueCounts = queues?.status_counts || {}
  const stateCounts = ownerState?.status_counts || {}
  const reviewCounts = ownerState?.review_status_counts || {}

  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900">Owner inbox</div>
          <div className="mt-0.5 text-xs leading-5 text-gray-500">
            accept/reject 之后再做 owner、证据、敏感目标校验；通过后只生成 import candidate artifact。
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={loading}
            onClick={loadInbox}
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            扫描
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-semibold text-primary shadow-sm transition-colors hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={materializing || !Number(counts.READY_FOR_OWNER_IMPORT || 0)}
            onClick={materializeReady}
          >
            <FileText size={13} />
            {materializing ? '生成中' : '生成 inbox artifacts'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={packetizing || !Number(importCounts.READY_FOR_ORGAN_CONSUMPTION || 0)}
            onClick={materializePackets}
          >
            <GitBranch size={13} />
            {packetizing ? '生成中' : '生成 organ packets'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200 bg-purple-50 px-3 py-1.5 text-xs font-semibold text-purple-700 shadow-sm transition-colors hover:bg-purple-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={consuming || !(Number(lastPackets?.packet_count || 0) || Number(importCounts.READY_FOR_ORGAN_CONSUMPTION || 0))}
            onClick={consumePackets}
          >
            <Database size={13} />
            {consuming ? '消费中' : '排入 owner queues'}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={importing || !Number(queueCounts.QUEUED_FOR_OWNER || 0)}
            onClick={importOwnerState}
          >
            <ShieldCheck size={13} />
            {importing ? '导入中' : '导入 owner state'}
          </button>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        {['READY_FOR_OWNER_IMPORT', 'PENDING_REVIEW', 'REJECTED_BY_OWNER', 'BLOCKED'].map((status) => (
          <div key={status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
            <StatusPill status={status} />
            <div className="mt-2 text-xl font-bold text-gray-900">{counts[status] || 0}</div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-4">
        <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-2 py-2">
          <div className="font-semibold text-indigo-700">Import-ready</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{importCounts.READY_FOR_ORGAN_CONSUMPTION || 0}</div>
        </div>
        <div className="rounded-lg border border-red-100 bg-red-50 px-2 py-2">
          <div className="font-semibold text-red-700">Import blocked</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{importCounts.BLOCKED || 0}</div>
        </div>
        <div className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
          <div className="font-semibold text-gray-700">Organ packets</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{lastPackets?.packet_count || 0}</div>
        </div>
        <div className="rounded-lg border border-purple-100 bg-purple-50 px-2 py-2">
          <div className="font-semibold text-purple-700">Owner queues</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{queueCounts.QUEUED_FOR_OWNER || 0}</div>
        </div>
        <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-2 py-2">
          <div className="font-semibold text-emerald-700">Owner state</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{stateCounts.IMPORTED_FOR_OWNER_REVIEW || ownerState?.count || 0}</div>
        </div>
        <div className="rounded-lg border border-cyan-100 bg-cyan-50 px-2 py-2">
          <div className="font-semibold text-cyan-700">Runtime hints</div>
          <div className="mt-1 text-xl font-bold text-gray-900">{ownerRuntimeHints?.count || 0}</div>
        </div>
      </div>

      {rows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-gray-100">
          {rows.slice(0, 6).map((item) => (
            <div key={item.candidate_id} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="text-gray-500">{item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="font-semibold text-gray-500">{item.import_allowed ? 'import-ready' : item.reason_code || 'waiting'}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-500">
          {loading ? '正在扫描 owner inbox...' : '暂无 owner inbox 项。'}
        </div>
      )}

      {lastMaterialized ? (
        <div className="mt-3 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-xs leading-5 text-green-700">
          已生成 {lastMaterialized.materialized_count} 个 owner import candidate artifact；长期写入仍由 owner organ 自己执行。
        </div>
      ) : null}

      {lastPackets ? (
        <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-xs leading-5 text-indigo-700">
          已生成 {lastPackets.packet_count} 个 owner intake packet；每个器官只消费自己的 packet。
        </div>
      ) : null}

      {lastConsumed ? (
        <div className="mt-3 rounded-lg border border-purple-100 bg-purple-50 px-3 py-2 text-xs leading-5 text-purple-700">
          已排入 {lastConsumed.queued_count} 个 owner queue item；这是候选队列 artifact，不代表长期状态已写入。
        </div>
      ) : null}

      {lastImported ? (
        <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-700">
          已导入 {lastImported.imported_count} 个 owner candidate state；它是 owner 的持久候选，不是已应用的长期记忆。
        </div>
      ) : null}

      {lastStateReview ? (
        <div className="mt-3 rounded-lg border border-cyan-100 bg-cyan-50 px-3 py-2 text-xs leading-5 text-cyan-700">
          Owner state review: {lastStateReview.status} · runtime hints {lastStateReview.runtime_hints?.count || 0} · 长期写入仍为 false。
        </div>
      ) : null}

      {queueRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-purple-100">
          {queueRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.channel_id}-${item.candidate_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="truncate text-gray-500">{item.channel_id || item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="font-semibold text-gray-500">{item.durable_write_performed ? 'wrote' : 'artifact-only'}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {stateRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-emerald-100">
          {stateRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.channel_id}-${item.candidate_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.72fr_0.78fr_0.95fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.capability}</div>
                <div className="truncate text-gray-500">{item.candidate_id}</div>
              </div>
              <div className="truncate text-gray-500">{item.channel_id || item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.review_status || item.status} />
                <span className="font-semibold text-gray-500">{item.durable_memory_write_performed ? 'memory-write' : 'candidate-only'}</span>
              </div>
              <div className="flex flex-wrap justify-start gap-1.5 sm:justify-end">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 font-semibold text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={reviewingCandidate === `accept:${item.candidate_id}` || item.review_status === 'OWNER_STATE_ACCEPTED'}
                  onClick={() => reviewOwnerState(item.candidate_id, item.owner_organ, 'accept')}
                >
                  <CheckCircle2 size={12} />
                  accept
                </button>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-semibold text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={reviewingCandidate === `reject:${item.candidate_id}` || item.review_status === 'OWNER_STATE_REJECTED'}
                  onClick={() => reviewOwnerState(item.candidate_id, item.owner_organ, 'reject')}
                >
                  <XCircle size={12} />
                  reject
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {stateRows.length ? (
        <div className="mt-2 grid grid-cols-1 gap-2 text-xs text-gray-600 sm:grid-cols-3">
          {['PENDING_OWNER_STATE_REVIEW', 'OWNER_STATE_ACCEPTED', 'OWNER_STATE_REJECTED'].map((status) => (
            <div key={status} className="rounded-lg border border-gray-100 bg-gray-50 px-2 py-2">
              <StatusPill status={status} />
              <div className="mt-2 text-lg font-bold text-gray-900">{reviewCounts[status] || 0}</div>
            </div>
          ))}
        </div>
      ) : null}

      {hintRows.length ? (
        <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-cyan-100">
          {hintRows.slice(0, 6).map((item) => (
            <div key={`${item.owner_organ}-${item.hint_id}`} className="grid grid-cols-1 gap-2 bg-white px-3 py-2 text-xs sm:grid-cols-[1fr_0.8fr_0.8fr]">
              <div className="min-w-0">
                <div className="truncate font-semibold text-gray-800">{item.guidance_type || item.capability}</div>
                <div className="truncate text-gray-500">{item.guidance || item.summary}</div>
              </div>
              <div className="truncate text-gray-500">{item.owner_organ || '—'}</div>
              <div className="flex items-center justify-between gap-2">
                <StatusPill status={item.status} />
                <span className="inline-flex items-center gap-1 font-semibold text-gray-500"><Eye size={12} /> trace-only</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {error ? (
        <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
          {error.message}
        </div>
      ) : null}
    </div>
  )
}

function CapabilitySetPanel({ summary, labels, note, emptyTitle }) {
  const rows = Array.isArray(summary?.capabilities) ? summary.capabilities : []
  const counts = summary?.status_counts || {}
  if (!rows.length) {
    return <EmptyStateCard title={emptyTitle} hint="等待 BioOps catalog 注册或日循环产物" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[0.72fr_1.28fr]">
      <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-gray-900">机制闭环</div>
            <div className="mt-0.5 text-xs text-gray-500">{summary.activation_rule || 'proposal-only'}</div>
          </div>
          <StatusPill status={summary.direct_write_allowed ? 'CHECK' : 'OWNER_GATED'} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {Object.entries(counts).map(([status, count]) => (
            <div key={status} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <StatusPill status={status} />
              <div className="mt-2 text-xl font-bold text-gray-900">{count}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
          {note}
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border border-white/80 bg-white/82 shadow-sm">
        <div className="grid grid-cols-[1.1fr_0.72fr_0.72fr_0.56fr] gap-3 border-b border-gray-100 bg-gray-50 px-4 py-2 text-xs font-semibold text-gray-500">
          <span>机制</span>
          <span>Owner</span>
          <span>输出</span>
          <span>状态</span>
        </div>
        {rows.map((item) => (
          <div key={item.capability} className="grid grid-cols-[1.1fr_0.72fr_0.72fr_0.56fr] gap-3 border-b border-gray-100 px-4 py-3 text-sm last:border-b-0">
            <div className="min-w-0">
              <div className="font-semibold text-gray-800">{labels[item.capability] || item.capability}</div>
              <div className="mt-0.5 truncate text-xs text-gray-500">{item.capability}</div>
            </div>
            <span className="truncate text-xs leading-6 text-gray-500">{item.owner_organ || '—'}</span>
            <span className="truncate text-xs leading-6 text-gray-500">{item.output_kind || '—'}</span>
            <span><StatusPill status={item.status} /></span>
          </div>
        ))}
      </div>
    </div>
  )
}

function InnerMechanismPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={INNER_MECHANISM_LABELS}
      emptyTitle="暂无 Inner Mechanisms 数据"
      note="Promise 类进入 owner candidate；其余机制只产审计、trace 或推荐，不直接写长期状态。"
    />
  )
}

function TrustFoundationPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={TRUST_FOUNDATION_LABELS}
      emptyTitle="暂无 Trust Foundation 数据"
      note="授权、真实、能力边界和不确定性只作为执行前 gate 与回复审计，不替代 owner 决策。"
    />
  )
}

function NaturalInteractionPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={NATURAL_INTERACTION_LABELS}
      emptyTitle="暂无 Natural Interaction 数据"
      note="自然交互调节只提供 trace 和低风险策略提示；复杂任务仍走规划链和 WorkOrder。"
    />
  )
}

function RelationshipLayerPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={RELATIONSHIP_LAYER_LABELS}
      emptyTitle="暂无 Relationship Layer 数据"
      note="关系层只产审计和修复建议；不直接写 Bond、Canon、人格核心或长期记忆。"
    />
  )
}

function InnerLifePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={INNER_LIFE_LABELS}
      emptyTitle="暂无 Inner Life 数据"
      note="内在生命层只生成梦境、象征、自我模型和生命脉搏报告；长期写入必须由对应 owner organ 采纳。"
    />
  )
}

function SelfGovernancePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={SELF_GOVERNANCE_LABELS}
      emptyTitle="暂无 Self Governance 数据"
      note="自我治理层只产价值、伦理、边界和身份连续性报告；复杂决策仍需 owner review 或 WorkOrder。"
    />
  )
}

function GrowthClosurePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={GROWTH_CLOSURE_LABELS}
      emptyTitle="暂无 Growth Closure 数据"
      note="成长与收束层只做阶段、负载、未闭环和降载建议；不直接删除记忆或开启新执行链。"
    />
  )
}

function MemoryNarrativePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={MEMORY_NARRATIVE_LABELS}
      emptyTitle="暂无 Memory Narrative 数据"
      note="记忆与叙事层只产 episode、书架、地标、连续性和梦化压缩报告；长期记忆写入必须由 owner 采纳。"
    />
  )
}

function AffectLearningPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={AFFECT_LEARNING_LABELS}
      emptyTitle="暂无 Affect Learning 数据"
      note="情绪学习层只做温度、耐受、消化、后悔、喜悦和失败隔离报告；不直接写人格或长期情绪记忆。"
    />
  )
}

function PresenceChannelPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={PRESENCE_CHANNEL_LABELS}
      emptyTitle="暂无 Presence & Channel 数据"
      note="通道与存在感层只做通道表达、长任务陪伴、静默陪伴、仪式和降级报告；不主动打扰用户，也不直接写长期状态。"
    />
  )
}

function DriveTrustPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={DRIVE_TRUST_LABELS}
      emptyTitle="暂无 Drive & Trust 数据"
      note="驱动力与信任层只做信任温度、意图代谢、驱动力仲裁和愿望格报告；不提升权限、不主动执行。"
    />
  )
}

function AdaptiveRepairPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={ADAPTIVE_REPAIR_LABELS}
      emptyTitle="暂无 Adaptive Repair 数据"
      note="适应与修复层只做用户镜像、身体校准、关系修复、叙事调试报告；偏好和自愈故事进入 owner-gated candidate。"
    />
  )
}

function CapabilityWorkbench({ sections }) {
  const items = Array.isArray(sections) ? sections.filter(Boolean) : []
  const [selectedKey, setSelectedKey] = useState(items[0]?.key || '')
  const selected = items.find((item) => item.key === selectedKey) || items[0]
  if (!selected) {
    return <EmptyStateCard title="暂无 BioOps 能力层数据" hint="等待 BioOps catalog 注册或日循环产物" />
  }
  const ActivePanel = selected.Panel
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm">
        <div className="border-b border-gray-100 px-4 py-3">
          <div className="text-sm font-semibold text-gray-900">能力层</div>
          <div className="mt-0.5 text-xs text-gray-500">{items.length} 个分区 · 当前 {selected.title}</div>
        </div>
        <div className="max-h-[560px] divide-y divide-gray-100 overflow-y-auto">
          {items.map((item) => {
            const active = item.key === selected.key
            return (
              <button
                type="button"
                key={item.key}
                className={`w-full px-4 py-3 text-left transition-colors ${
                  active ? 'bg-primary-50' : 'bg-white hover:bg-gray-50'
                }`}
                onClick={() => setSelectedKey(item.key)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-gray-900">{item.title}</div>
                    <div className="mt-0.5 truncate text-xs text-gray-500">{item.hint}</div>
                  </div>
                  <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs font-semibold text-gray-600">
                    {item.count ?? '—'}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(item.statusCounts || {}).slice(0, 3).map(([status, count]) => (
                    <span key={status} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600">
                      {STATUS_LABELS[status] || status}:{count}
                    </span>
                  ))}
                </div>
              </button>
            )
          })}
        </div>
      </div>
      <div className="min-w-0 space-y-3">
        <div className="rounded-xl border border-white/80 bg-white/86 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-gray-900">{selected.title}</h3>
              <p className="mt-1 text-sm leading-6 text-gray-600">{selected.subtitle}</p>
            </div>
            <StatusPill status={selected.status || 'REPORT_ONLY'} />
          </div>
        </div>
        <ActivePanel summary={selected.summary} />
      </div>
    </div>
  )
}

function RuntimeTraceList({ traces }) {
  const rows = Array.isArray(traces) ? traces : []
  if (!rows.length) {
    return <EmptyStateCard title="暂无运行时 trace" hint="等待 Chat/Planner 观测样本" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
      {rows.map((trace) => (
        <div key={trace.id} className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-gray-900">{TRACE_LABELS[trace.id] || trace.id}</div>
              <div className="mt-0.5 text-xs text-gray-500">{trace.proposal_only ? 'proposal-only trace' : 'runtime trace'}</div>
            </div>
            <StatusPill status={trace.reflex_matched ? 'TRACED' : 'AUDITED'} />
          </div>
          <div className="mt-3 space-y-2 text-xs text-gray-600">
            <div className="flex justify-between gap-2 rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
              <span>Reflex</span>
              <span className="font-semibold">{String(trace.reflex_matched)} · {trace.reflex_type || '—'}</span>
            </div>
            <div className="flex justify-between gap-2 rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
              <span>Attention</span>
              <span className="font-semibold">{trace.attention_pattern || '—'}</span>
            </div>
            <div className="flex justify-between gap-2 rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
              <span>Weather</span>
              <span className="font-semibold">{trace.internal_weather || '—'}</span>
            </div>
            <div className="flex justify-between gap-2 rounded-lg border border-gray-100 bg-gray-50 px-2 py-1.5">
              <span>Silence</span>
              <span className="font-semibold">{trace.silence_policy || '—'}</span>
            </div>
          </div>
          <EvidenceChips items={(trace.forbidden_actions || []).map((item) => `forbid:${item}`)} />
        </div>
      ))}
    </div>
  )
}

function ArtifactList({ artifacts }) {
  const rows = Array.isArray(artifacts) ? artifacts : []
  if (!rows.length) return null
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 shadow-sm">
      {rows.map((artifact) => (
        <div key={artifact.id} className="grid grid-cols-1 gap-1 border-b border-gray-100 px-4 py-3 last:border-b-0 lg:grid-cols-[13rem_1fr] lg:gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
            <FileText size={15} className="text-gray-400" />
            {artifact.label}
          </div>
          <code className="break-all rounded-lg border border-gray-100 bg-gray-50 px-2 py-1 text-xs text-gray-500">{artifact.path}</code>
        </div>
      ))}
    </div>
  )
}

export default function CodexBioOpsObservability() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const [dailyRunning, setDailyRunning] = useState(false)
  const [dailyRunResult, setDailyRunResult] = useState(null)
  const [dailyRunError, setDailyRunError] = useState(null)
  const [showOwnerDeepDive, setShowOwnerDeepDive] = useState(false)
  const query = useCodexBioOpsObservabilityQuery({
    pollingEnabled: pollingEnabled && pageVisible,
    pollingInterval,
    pollingTick,
  })
  const data = query.data || null
  const codex = data?.codex_runtime || {}
  const bioOps = data?.bio_ops || {}
  const innerMechanisms = bioOps.inner_mechanisms || {}
  const trustFoundation = bioOps.trust_foundation || {}
  const naturalInteraction = bioOps.natural_interaction || {}
  const relationshipLayer = bioOps.relationship_layer || {}
  const innerLife = bioOps.inner_life || {}
  const selfGovernance = bioOps.self_governance || {}
  const growthClosure = bioOps.growth_closure || {}
  const memoryNarrative = bioOps.memory_narrative || {}
  const affectLearning = bioOps.affect_learning || {}
  const presenceChannel = bioOps.presence_channel || {}
  const driveTrust = bioOps.drive_trust || {}
  const adaptiveRepair = bioOps.adaptive_repair || {}
  const inspect = codex.inspect || {}
  const blockedWrite = codex.blocked_write || {}
  const pendingAdoptions = Array.isArray(bioOps.pending_adoptions) ? bioOps.pending_adoptions : []
  const statusCounts = bioOps.status_counts || {}
  const totalBioOps = useMemo(
    () => Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0),
    [statusCounts]
  )
  const capabilitySections = useMemo(() => [
    {
      key: 'inner_mechanisms',
      title: 'Inner Mechanisms',
      subtitle: '第三份文档的最小可落地机制：先纳入 BioOps 审计和 owner gate，再决定是否升级主路径。',
      hint: 'Promise、反射弧、边界',
      status: 'OWNER_GATED',
      count: innerMechanisms.capability_count,
      statusCounts: innerMechanisms.status_counts,
      summary: innerMechanisms,
      Panel: InnerMechanismPanel,
    },
    {
      key: 'trust_foundation',
      title: 'Trust Foundation',
      subtitle: '可信基础层：授权梯度、真实约束、能力谦逊和不确定性表达统一变成可观测 gate。',
      hint: '授权、真实、能力边界',
      status: 'TRACED',
      count: trustFoundation.capability_count,
      statusCounts: trustFoundation.status_counts,
      summary: trustFoundation,
      Panel: TrustFoundationPanel,
    },
    {
      key: 'natural_interaction',
      title: 'Natural Interaction',
      subtitle: '自然交互层：主观时间、情绪惯性、上下文重力和自我打断统一进入低风险 trace。',
      hint: '呼吸式回复、主观时间',
      status: 'TRACED',
      count: naturalInteraction.capability_count,
      statusCounts: naturalInteraction.status_counts,
      summary: naturalInteraction,
      Panel: NaturalInteractionPanel,
    },
    {
      key: 'relationship_layer',
      title: 'Relationship Layer',
      subtitle: '长期关系层：契约、摩擦、修复曲线、依恋风格、沉默记忆和边界守护保持可观测。',
      hint: '契约、摩擦、修复',
      status: 'REPORT_ONLY',
      count: relationshipLayer.capability_count,
      statusCounts: relationshipLayer.status_counts,
      summary: relationshipLayer,
      Panel: RelationshipLayerPanel,
    },
    {
      key: 'inner_life',
      title: 'Inner Life',
      subtitle: '内在生命层：梦境排练、象征记忆、记忆质感、自我模型、生命脉搏和个人神话保持报告模式。',
      hint: '梦境、象征、自我模型',
      status: 'REPORT_ONLY',
      count: innerLife.capability_count,
      statusCounts: innerLife.status_counts,
      summary: innerLife,
      Panel: InnerLifePanel,
    },
    {
      key: 'self_governance',
      title: 'Self Governance',
      subtitle: '自我治理层：价值层级、伦理复盘、边界膜、尊严守护、身份连续性和内在议会保持可观测。',
      hint: '价值、伦理、边界',
      status: 'REPORT_ONLY',
      count: selfGovernance.capability_count,
      statusCounts: selfGovernance.status_counts,
      summary: selfGovernance,
      Panel: SelfGovernancePanel,
    },
    {
      key: 'growth_closure',
      title: 'Growth Closure',
      subtitle: '成长与收束层：想法池、阶段门、存在负载、未闭环和认知卸载让项目能继续收尾。',
      hint: '阶段、负载、收束',
      status: 'REPORT_ONLY',
      count: growthClosure.capability_count,
      statusCounts: growthClosure.status_counts,
      summary: growthClosure,
      Panel: GrowthClosurePanel,
    },
    {
      key: 'memory_narrative',
      title: 'Memory Narrative',
      subtitle: '记忆与叙事层：情节、书架、认知地标、叙事身份、自我连续性和梦化压缩保持可审计。',
      hint: '情节、叙事、连续性',
      status: 'REPORT_ONLY',
      count: memoryNarrative.capability_count,
      statusCounts: memoryNarrative.status_counts,
      summary: memoryNarrative,
      Panel: MemoryNarrativePanel,
    },
    {
      key: 'affect_learning',
      title: 'Affect Learning',
      subtitle: '情绪学习层：内在温度、免疫耐受、情绪消化、后悔信号、喜悦强化和失败隔离保持报告模式。',
      hint: '温度、消化、强化',
      status: 'REPORT_ONLY',
      count: affectLearning.capability_count,
      statusCounts: affectLearning.status_counts,
      summary: affectLearning,
      Panel: AffectLearningPanel,
    },
    {
      key: 'presence_channel',
      title: 'Presence & Channel',
      subtitle: '通道与存在感层：通道灵魂、长任务陪伴、注意力地形、静默陪伴、社交面具、仪式和温柔降级保持可观测。',
      hint: '通道、陪伴、仪式',
      status: 'REPORT_ONLY',
      count: presenceChannel.capability_count,
      statusCounts: presenceChannel.status_counts,
      summary: presenceChannel,
      Panel: PresenceChannelPanel,
    },
    {
      key: 'drive_trust',
      title: 'Drive & Trust',
      subtitle: '驱动力与信任层：信任梯度、信任恒温器、意图代谢、驱动力冲突、愿望格和 Need/Want/Impulse 保持报告模式。',
      hint: '信任、意图、愿望',
      status: 'REPORT_ONLY',
      count: driveTrust.capability_count,
      statusCounts: driveTrust.status_counts,
      summary: driveTrust,
      Panel: DriveTrustPanel,
    },
    {
      key: 'adaptive_repair',
      title: 'Adaptive Repair',
      subtitle: '适应与修复层：用户镜像、身体可塑、关系修复、叙事调试、隐性偏好和自愈故事保持可审计。',
      hint: '镜像、修复、偏好',
      status: 'OWNER_GATED',
      count: adaptiveRepair.capability_count,
      statusCounts: adaptiveRepair.status_counts,
      summary: adaptiveRepair,
      Panel: AdaptiveRepairPanel,
    },
  ], [
    adaptiveRepair,
    affectLearning,
    driveTrust,
    growthClosure,
    innerLife,
    innerMechanisms,
    memoryNarrative,
    naturalInteraction,
    presenceChannel,
    relationshipLayer,
    selfGovernance,
    trustFoundation,
  ])
  const runDailyCycle = async () => {
    setDailyRunning(true)
    setDailyRunError(null)
    try {
      const payload = await runCodexBioOpsDailyCycle({
        inputs: {
          evidence_refs: ['monitor_ui:manual_daily_cycle'],
          message: 'monitor ui daily cycle',
          planned_path: ['bio_ops_daily_cycle'],
        },
      })
      setDailyRunResult(payload)
      await query.refetch?.()
    } catch (err) {
      setDailyRunError(err)
    } finally {
      setDailyRunning(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-white/80 bg-white/86 p-5 shadow-card backdrop-blur-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-2 rounded-full border border-primary-100 bg-primary-50 px-3 py-1 text-xs font-semibold text-primary">
              <ShieldCheck size={13} />
              Codex Runtime v2 · BioOps
            </div>
            <h1 className="mt-3 text-2xl font-bold text-gray-900">闭环观测</h1>
            <p className="mt-1 max-w-4xl text-sm leading-6 text-gray-600">
              把两个方案的收益落到项目内：执行链是否可控、BioOps 是否只产候选或报告、Chat/Planner 是否有低风险 trace、验收证据是否可复盘。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {data?.run_id ? (
              <span className="rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600">
                run {data.run_id}
              </span>
            ) : null}
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-white px-3 py-1.5 text-xs font-semibold text-primary shadow-sm transition-colors hover:bg-primary-50"
              onClick={() => query.refetch?.()}
            >
              <RefreshCw size={13} className={query.refreshing ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={dailyRunning}
              onClick={runDailyCycle}
            >
              <Activity size={13} className={dailyRunning ? 'animate-pulse' : ''} />
              {dailyRunning ? '运行中' : '运行 daily cycle'}
            </button>
          </div>
        </div>
      </div>

      {dailyRunResult ? (
        <NoticeBanner variant="success" title="BioOps daily cycle 已生成">
          run {dailyRunResult.run_id} · {dailyRunResult.bio_ops_capability_count} 个能力 · owner pending {dailyRunResult.pending_owner_adoptions || 0}
        </NoticeBanner>
      ) : null}

      {dailyRunError ? (
        <NoticeBanner variant="error" title="BioOps daily cycle 运行失败">
          {dailyRunError.message}
        </NoticeBanner>
      ) : null}

      {query.error ? (
        <NoticeBanner variant="error" title="Codex / BioOps 观测加载失败">
          {query.error.message}
        </NoticeBanner>
      ) : null}

      {query.loading && !data ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="h-36 animate-pulse rounded-xl border border-white/80 bg-white/70 shadow-sm" />
          ))}
        </div>
      ) : null}

      {data && !data.artifact_available ? (
        <EmptyStateCard
          icon={Database}
          title="还没有可用观测产物"
          hint="运行 observable effects 或 BioOps daily cycle 后，这里会自动接入最新 run"
        />
      ) : null}

      {data?.artifact_available ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <MetricTile
              icon={GitBranch}
              tone="green"
              label="Codex inspect"
              value={inspect.status || '—'}
              status={inspect.status}
              hint={inspect.codex_model || 'ResultContract'}
            />
            <MetricTile
              icon={Lock}
              tone="red"
              label="写入阻断"
              value={blockedWrite.status || '—'}
              status={blockedWrite.status}
              hint={blockedWrite.reason_code || 'policy gate'}
            />
            <MetricTile
              icon={Sparkles}
              tone="indigo"
              label="BioOps 能力"
              value={bioOps.capability_count ?? totalBioOps}
              status="TRACED"
              hint={`${statusCounts.PROPOSED || 0} proposals · ${statusCounts.CANDIDATE || 0} candidates`}
            />
            <MetricTile
              icon={ShieldCheck}
              tone="amber"
              label="Owner 待审"
              value={pendingAdoptions.length || statusCounts.CANDIDATE || 0}
              status={pendingAdoptions.length ? 'OWNER_GATED' : 'REPORT_ONLY'}
              hint="长期状态不由 Codex 直写"
            />
            <MetricTile
              icon={CheckCircle2}
              tone={codex.hard_cut_scan?.passed ? 'green' : 'amber'}
              label="硬切扫描"
              value={codex.hard_cut_scan?.passed ? 'PASS' : 'CHECK'}
              status={codex.hard_cut_scan?.passed ? 'PASS' : 'CHECK'}
              hint={`pytest ${codex.pytest?.exit_code ?? '—'} · scan ${codex.hard_cut_scan?.exit_code ?? '—'}`}
            />
          </div>

          <Section title="项目收益" subtitle="这里展示的是两个方案对工程闭环的直接收益，不是一次性说明文档。">
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {(data.benefits || []).map((benefit) => (
                <BenefitCard key={benefit.id} benefit={benefit} />
              ))}
            </div>
          </Section>

          <Section title="Codex Runtime v2" subtitle="WorkOrder、ResultContract、阻断结果和硬切验收放在同一处看。">
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              <CodexResultCard title="inspect 执行结果" result={inspect} icon={GitBranch} tone="green" />
              <CodexResultCard title="无 WorkOrder 写入阻断" result={blockedWrite} icon={Lock} tone="red" />
            </div>
            <ContractFieldGrid inspect={inspect} />
          </Section>

          <Section title="BioOps Owner 接管" subtitle="高级能力只产 proposal、audit report、candidate；候选必须由对应 owner organ 接管。">
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-[0.8fr_1.2fr]">
              <StatusDistribution counts={statusCounts} />
              <PendingAdoptions
                items={pendingAdoptions}
                runId={data.run_id}
                onChanged={() => query.refetch?.()}
              />
            </div>
            <details
              className="overflow-hidden rounded-xl border border-white/80 bg-white/86 shadow-sm"
              onToggle={(event) => setShowOwnerDeepDive(event.currentTarget.open)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-gray-700">
                <span>Owner 队列与效果明细</span>
                <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-500">
                  {showOwnerDeepDive ? '已展开' : '展开'}
                </span>
              </summary>
              {showOwnerDeepDive ? (
                <div className="space-y-3 border-t border-gray-100 p-4">
                  <OwnerInboxPanel runId={data.run_id} />
                  <FocusEffectList effects={bioOps.focused_effects} />
                  <GroupTable groups={bioOps.groups} />
                </div>
              ) : null}
            </details>
          </Section>

          <Section title="BioOps 能力层工作台" subtitle="把十几个机制分区收进一个可切换工作台，保留完整细节但不再把页面拉成卡片长卷。">
            <CapabilityWorkbench sections={capabilitySections} />
          </Section>

          <Section title="Chat / Planner 运行时 trace" subtitle="能看到短确认是否走反射、复杂请求是否保持规划链、主动沉默为什么触发。">
            <RuntimeTraceList traces={data.runtime_traces} />
          </Section>

          <Section
            title="证据 artifacts"
            subtitle="WebUI 读取这些文件并压缩成上面的操作视图；需要深挖时按路径打开原始产物。"
            aside={query.lastUpdatedAt ? <span className="text-xs text-gray-400">updated {new Date(query.lastUpdatedAt).toLocaleString()}</span> : null}
          >
            <ArtifactList artifacts={data.artifacts} />
          </Section>
        </>
      ) : null}

      {query.refreshing ? (
        <NoticeBanner variant="info" title="正在刷新 Codex / BioOps 观测">
          页面会复用最近一次成功数据，刷新完成后替换。
        </NoticeBanner>
      ) : null}

      {data?.artifact_available && !codex.hard_cut_scan?.passed ? (
        <NoticeBanner variant="warning" title="硬切扫描需要复核">
          当前 run 没有提供通过状态，请重新运行验收扫描或检查 artifact。
        </NoticeBanner>
      ) : null}

      {data?.artifact_available && blockedWrite.status !== 'BLOCKED' ? (
        <NoticeBanner variant="warning" title="写入阻断样本异常">
          观测样本没有显示 BLOCKED，建议重新跑无 WorkOrder 写入用例。
        </NoticeBanner>
      ) : null}

      {data?.artifact_available && inspect.status !== 'EXECUTED' ? (
        <NoticeBanner variant="warning" title="Codex inspect 样本未成功执行">
          inspect 结果不是 EXECUTED，先检查 Codex CLI 模型、profile 和 artifacts。
        </NoticeBanner>
      ) : null}

      {data?.artifact_available && codex.hard_cut_scan?.passed && blockedWrite.status === 'BLOCKED' && inspect.status === 'EXECUTED' ? (
        <NoticeBanner variant="success" title="两个方案的关键收益已可观测">
          执行可控、敏感写入被拦截、BioOps 候选进入 owner gate、Chat/Planner 行为 trace 已接入 WebUI。
        </NoticeBanner>
      ) : null}

      <div className="sr-only" aria-live="polite">
        {query.refreshing ? 'Codex BioOps observability refreshing' : 'Codex BioOps observability ready'}
      </div>
    </div>
  )
}
