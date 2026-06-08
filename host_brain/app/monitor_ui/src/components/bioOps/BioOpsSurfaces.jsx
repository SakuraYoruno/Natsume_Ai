import React from 'react'

export const STATUS_STYLES = {
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

export const STATUS_LABELS = {
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

export function classNames(...items) {
  return items.filter(Boolean).join(' ')
}

export function StatusPill({ status }) {
  const normalized = String(status || 'UNKNOWN').toUpperCase()
  return (
    <span className={classNames(
      'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold',
      STATUS_STYLES[normalized] || STATUS_STYLES.UNKNOWN,
    )}>
      {STATUS_LABELS[normalized] || normalized}
    </span>
  )
}

export function IconTile({ icon: Icon, tone = 'blue' }) {
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

export function MetricTile({ icon, tone, label, value, hint, status }) {
  return (
    <div className="rounded-xl border border-white/80 bg-white/82 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <IconTile icon={icon} tone={tone} />
        {status ? <StatusPill status={status} /> : null}
      </div>
      <div className="mt-3 text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-bold text-gray-900">{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div> : null}
    </div>
  )
}

export function Section({ title, subtitle, children, aside = null }) {
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

export function EvidenceChips({ items }) {
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

export function BioOpsSkeletonGrid({ count = 4 }) {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="h-36 animate-pulse rounded-xl border border-white/80 bg-white/70 shadow-sm" />
      ))}
    </div>
  )
}
