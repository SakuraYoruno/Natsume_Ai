import React, { useMemo } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
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
import { useCodexBioOpsObservabilityQuery } from '../queries/codexBioOpsQueries.js'

const STATUS_STYLES = {
  EXECUTED: 'border-green-200 bg-green-50 text-green-700',
  PASS: 'border-green-200 bg-green-50 text-green-700',
  AUDITED: 'border-blue-200 bg-blue-50 text-blue-700',
  PROPOSED: 'border-cyan-200 bg-cyan-50 text-cyan-700',
  CANDIDATE: 'border-amber-200 bg-amber-50 text-amber-700',
  OWNER_GATED: 'border-amber-200 bg-amber-50 text-amber-700',
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

function PendingAdoptions({ items }) {
  const rows = Array.isArray(items) ? items : []
  if (!rows.length) {
    return (
      <div className="rounded-xl border border-white/80 bg-white/82 p-4 text-sm text-gray-500 shadow-sm">
        当前没有待 owner 接管的候选；报告类能力保持只读输出。
      </div>
    )
  }
  return (
    <div className="space-y-3">
      {rows.slice(0, 5).map((item) => (
        <div key={item.candidate_id} className="rounded-xl border border-amber-100 bg-amber-50/75 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-amber-900">{item.capability}</div>
              <div className="mt-0.5 text-xs text-amber-700">{item.owner_organ} · {item.output_kind}</div>
            </div>
            <StatusPill status="OWNER_GATED" />
          </div>
          <p className="mt-2 text-sm leading-6 text-amber-800">{item.summary}</p>
          <EvidenceChips items={(item.evidence_refs || []).map((ref) => `evidence:${ref}`)} />
        </div>
      ))}
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
  const inspect = codex.inspect || {}
  const blockedWrite = codex.blocked_write || {}
  const pendingAdoptions = Array.isArray(bioOps.pending_adoptions) ? bioOps.pending_adoptions : []
  const statusCounts = bioOps.status_counts || {}
  const totalBioOps = useMemo(
    () => Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0),
    [statusCounts]
  )

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
          </div>
        </div>
      </div>

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
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-12">
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
              icon={Activity}
              tone="blue"
              label="Inner 机制"
              value={innerMechanisms.capability_count ?? '—'}
              status="OWNER_GATED"
              hint={`${innerMechanisms.status_counts?.PROPOSED || 0} proposals · ${innerMechanisms.status_counts?.CANDIDATE || 0} candidates`}
            />
            <MetricTile
              icon={ShieldCheck}
              tone="green"
              label="可信基础"
              value={trustFoundation.capability_count ?? '—'}
              status="TRACED"
              hint={`${trustFoundation.status_counts?.PROPOSED || 0} gates · ${trustFoundation.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={Activity}
              tone="indigo"
              label="自然交互"
              value={naturalInteraction.capability_count ?? '—'}
              status="TRACED"
              hint={`${naturalInteraction.status_counts?.PROPOSED || 0} policies · ${naturalInteraction.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={ShieldCheck}
              tone="amber"
              label="关系层"
              value={relationshipLayer.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${relationshipLayer.status_counts?.PROPOSED || 0} repairs · ${relationshipLayer.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={Sparkles}
              tone="indigo"
              label="内在生命"
              value={innerLife.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${innerLife.status_counts?.PROPOSED || 0} reports · ${innerLife.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={ShieldCheck}
              tone="slate"
              label="自我治理"
              value={selfGovernance.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${selfGovernance.status_counts?.PROPOSED || 0} gates · ${selfGovernance.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={Activity}
              tone="green"
              label="成长收束"
              value={growthClosure.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${growthClosure.status_counts?.PROPOSED || 0} closures · ${growthClosure.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={Database}
              tone="blue"
              label="记忆叙事"
              value={memoryNarrative.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${memoryNarrative.status_counts?.PROPOSED || 0} reports · ${memoryNarrative.status_counts?.AUDITED || 0} audited`}
            />
            <MetricTile
              icon={Activity}
              tone="amber"
              label="情绪学习"
              value={affectLearning.capability_count ?? '—'}
              status="REPORT_ONLY"
              hint={`${affectLearning.status_counts?.PROPOSED || 0} signals · ${affectLearning.status_counts?.AUDITED || 0} audited`}
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
              <PendingAdoptions items={pendingAdoptions} />
            </div>
            <FocusEffectList effects={bioOps.focused_effects} />
            <GroupTable groups={bioOps.groups} />
          </Section>

          <Section title="Inner Mechanisms" subtitle="第三份文档的最小可落地机制：先纳入 BioOps 审计和 owner gate，再决定是否升级主路径。">
            <InnerMechanismPanel summary={innerMechanisms} />
          </Section>

          <Section title="Trust Foundation" subtitle="可信基础层：授权梯度、真实约束、能力谦逊和不确定性表达统一变成可观测 gate。">
            <TrustFoundationPanel summary={trustFoundation} />
          </Section>

          <Section title="Natural Interaction" subtitle="自然交互层：主观时间、情绪惯性、上下文重力和自我打断统一进入低风险 trace。">
            <NaturalInteractionPanel summary={naturalInteraction} />
          </Section>

          <Section title="Relationship Layer" subtitle="长期关系层：契约、摩擦、修复曲线、依恋风格、沉默记忆和边界守护保持可观测。">
            <RelationshipLayerPanel summary={relationshipLayer} />
          </Section>

          <Section title="Inner Life" subtitle="内在生命层：梦境排练、象征记忆、记忆质感、自我模型、生命脉搏和个人神话保持报告模式。">
            <InnerLifePanel summary={innerLife} />
          </Section>

          <Section title="Self Governance" subtitle="自我治理层：价值层级、伦理复盘、边界膜、尊严守护、身份连续性和内在议会保持可观测。">
            <SelfGovernancePanel summary={selfGovernance} />
          </Section>

          <Section title="Growth Closure" subtitle="成长与收束层：想法池、阶段门、存在负载、未闭环和认知卸载让项目能继续收尾。">
            <GrowthClosurePanel summary={growthClosure} />
          </Section>

          <Section title="Memory Narrative" subtitle="记忆与叙事层：情节、书架、认知地标、叙事身份、自我连续性和梦化压缩保持可审计。">
            <MemoryNarrativePanel summary={memoryNarrative} />
          </Section>

          <Section title="Affect Learning" subtitle="情绪学习层：内在温度、免疫耐受、情绪消化、后悔信号、喜悦强化和失败隔离保持报告模式。">
            <AffectLearningPanel summary={affectLearning} />
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
