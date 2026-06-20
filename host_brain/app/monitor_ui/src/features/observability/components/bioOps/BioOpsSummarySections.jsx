import React from 'react'
import { CheckCircle2, FileText, XCircle } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import { BIO_OPS_STATUS_ORDER } from '../../../../shared/statusMeta.js'
import { EvidenceChips, IconTile, StatusPill } from './BioOpsSurfaces.jsx'
import { EFFECT_LABELS, TRACE_LABELS } from './BioOpsLabels.js'

export function BenefitCard({ benefit }) {
  return (
    <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function ContractFieldGrid({ inspect }) {
  const present = Array.isArray(inspect?.contract_fields_present) ? inspect.contract_fields_present : []
  const missing = Array.isArray(inspect?.contract_fields_missing) ? inspect.contract_fields_missing : []
  return (
    <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function CodexResultCard({ title, result, icon, tone }) {
  const riskFlags = Array.isArray(result?.risk_flags) ? result.risk_flags : []
  return (
    <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function StatusDistribution({ counts }) {
  const entries = BIO_OPS_STATUS_ORDER
    .map((status) => [status, Number(counts?.[status] || 0)])
    .filter(([, value]) => value > 0)
  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  if (!total) {
    return <EmptyStateCard title="暂无 BioOps 分布" hint="等待日循环或观测脚本生成 artifacts" />
  }
  return (
    <div className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function FocusEffectList({ effects }) {
  const items = Array.isArray(effects) ? effects : []
  if (!items.length) {
    return <EmptyStateCard title="暂无关键效果样本" hint="等待 focused effects artifact" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      {items.map((item) => (
        <div key={item.id} className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function GroupTable({ groups }) {
  const rows = Array.isArray(groups) ? groups : []
  if (!rows.length) return null
  return (
    <div className="overflow-hidden rounded-lg border border-white/80 bg-white/82 shadow-sm">
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

export function RuntimeTraceList({ traces }) {
  const rows = Array.isArray(traces) ? traces : []
  if (!rows.length) {
    return <EmptyStateCard title="暂无运行时 trace" hint="等待 Chat/Planner 观测样本" />
  }
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
      {rows.map((trace) => (
        <div key={trace.id} className="rounded-lg border border-white/80 bg-white/82 p-4 shadow-sm">
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

export function ArtifactList({ artifacts }) {
  const rows = Array.isArray(artifacts) ? artifacts : []
  if (!rows.length) return null
  return (
    <div className="rounded-lg border border-white/80 bg-white/82 shadow-sm">
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
