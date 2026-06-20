import React from 'react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { StatusPill } from './BioOpsSurfaces.jsx'
import {
  ADAPTIVE_REPAIR_LABELS,
  AFFECT_LEARNING_LABELS,
  DRIVE_TRUST_LABELS,
  GROWTH_CLOSURE_LABELS,
  INNER_LIFE_LABELS,
  INNER_MECHANISM_LABELS,
  MEMORY_NARRATIVE_LABELS,
  NATURAL_INTERACTION_LABELS,
  PRESENCE_CHANNEL_LABELS,
  RELATIONSHIP_LAYER_LABELS,
  SELF_GOVERNANCE_LABELS,
  TRUST_FOUNDATION_LABELS,
} from './BioOpsLabels.js'

function CapabilitySetPanel({ summary, labels, note, emptyTitle }) {
  const rows = Array.isArray(summary?.capabilities) ? summary.capabilities : []
  const counts = summary?.status_counts || {}
  if (!rows.length) {
    return <EmptyStateCard title={emptyTitle} hint="等待 BioOps catalog 注册或日循环产物" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[0.72fr_1.28fr]">
      <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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
      <div className="overflow-hidden rounded-lg border border-white/80 bg-white/82 shadow-sm">
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

export function InnerMechanismPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={INNER_MECHANISM_LABELS}
      emptyTitle="暂无 Inner Mechanisms 数据"
      note="Promise 类进入 owner candidate；其余机制只产审计、trace 或推荐，不直接写长期状态。"
    />
  )
}

export function TrustFoundationPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={TRUST_FOUNDATION_LABELS}
      emptyTitle="暂无 Trust Foundation 数据"
      note="授权、真实、能力边界和不确定性只作为执行前 gate 与回复审计，不替代 owner 决策。"
    />
  )
}

export function NaturalInteractionPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={NATURAL_INTERACTION_LABELS}
      emptyTitle="暂无 Natural Interaction 数据"
      note="自然交互调节只提供 trace 和低风险策略提示；复杂任务仍走规划链和 WorkOrder。"
    />
  )
}

export function RelationshipLayerPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={RELATIONSHIP_LAYER_LABELS}
      emptyTitle="暂无 Relationship Layer 数据"
      note="关系层只产审计和修复建议；不直接写 Bond、Canon、人格核心或长期记忆。"
    />
  )
}

export function InnerLifePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={INNER_LIFE_LABELS}
      emptyTitle="暂无 Inner Life 数据"
      note="内在生命层只生成梦境、象征、自我模型和生命脉搏报告；长期写入必须由对应 owner organ 采纳。"
    />
  )
}

export function SelfGovernancePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={SELF_GOVERNANCE_LABELS}
      emptyTitle="暂无 Self Governance 数据"
      note="自我治理层只产价值、伦理、边界和身份连续性报告；复杂决策仍需 owner review 或 WorkOrder。"
    />
  )
}

export function GrowthClosurePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={GROWTH_CLOSURE_LABELS}
      emptyTitle="暂无 Growth Closure 数据"
      note="成长与收束层只做阶段、负载、未闭环和降载建议；不直接删除记忆或开启新执行链。"
    />
  )
}

export function MemoryNarrativePanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={MEMORY_NARRATIVE_LABELS}
      emptyTitle="暂无 Memory Narrative 数据"
      note="记忆与叙事层只产 episode、书架、地标、连续性和梦化压缩报告；长期记忆写入必须由 owner 采纳。"
    />
  )
}

export function AffectLearningPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={AFFECT_LEARNING_LABELS}
      emptyTitle="暂无 Affect Learning 数据"
      note="情绪学习层只做温度、耐受、消化、后悔、喜悦和失败隔离报告；不直接写人格或长期情绪记忆。"
    />
  )
}

export function PresenceChannelPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={PRESENCE_CHANNEL_LABELS}
      emptyTitle="暂无 Presence & Channel 数据"
      note="通道与存在感层只做通道表达、长任务陪伴、静默陪伴、仪式和降级报告；不主动打扰用户，也不直接写长期状态。"
    />
  )
}

export function DriveTrustPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={DRIVE_TRUST_LABELS}
      emptyTitle="暂无 Drive & Trust 数据"
      note="驱动力与信任层只做信任温度、意图代谢、驱动力仲裁和愿望格报告；不提升权限、不主动执行。"
    />
  )
}

export function AdaptiveRepairPanel({ summary }) {
  return (
    <CapabilitySetPanel
      summary={summary}
      labels={ADAPTIVE_REPAIR_LABELS}
      emptyTitle="暂无 Adaptive Repair 数据"
      note="适应与修复层只做用户镜像、身体校准、关系修复、叙事调试报告；偏好和自愈故事进入 owner-gated candidate。"
    />
  )
}
