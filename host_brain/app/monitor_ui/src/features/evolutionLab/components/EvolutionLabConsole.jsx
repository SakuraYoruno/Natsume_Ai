import {
  ArrowRight,
  BadgeCheck,
  Boxes,
  Braces,
  Container,
  FlaskConical,
  GitCompareArrows,
  Hourglass,
  LockKeyhole,
  ShieldCheck,
} from 'lucide-react'
import { EVOLUTION_LAB_KIND_LABELS } from '../evolutionLabModel.js'

function short(value, size = 12) {
  const text = String(value || '')
  return text.length > size ? `${text.slice(0, size)}…` : (text || '—')
}

function formatMap(value) {
  const entries = Object.entries(value || {})
  if (!entries.length) return '—'
  return entries.map(([key, item]) => `${key} ${Number(item).toLocaleString()}`).join(' · ')
}

function DeltaRow({ label, values }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-1 text-xs font-medium text-gray-700">{formatMap(values)}</div>
    </div>
  )
}

function EvidenceDigest({ label, value }) {
  return (
    <div className="min-w-0 rounded-lg border border-gray-100 bg-white px-3 py-2">
      <div className="text-[11px] text-gray-400">{label}</div>
      <div className="mt-1 truncate font-mono text-xs text-gray-700" title={value}>{value || '—'}</div>
    </div>
  )
}

function ArmCard({ arm }) {
  return (
    <article className={`rounded-xl border p-4 ${arm.isBaseline ? 'border-slate-200 bg-slate-50/80' : 'border-emerald-200 bg-emerald-50/60'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${arm.isBaseline ? 'bg-slate-200 text-slate-700' : 'bg-emerald-100 text-emerald-700'}`}>
              {arm.isBaseline ? 'BASELINE' : 'CANDIDATE'}
            </span>
            <span className="text-sm font-semibold text-gray-900">{arm.variantId}</span>
          </div>
          <div className="mt-1 text-xs text-gray-500">
            {arm.policyVersion} · {arm.codeVersion}
          </div>
        </div>
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-1 text-xs font-semibold text-emerald-700">
          <BadgeCheck size={12} />
          {arm.verificationStatus}
        </span>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-white/80 px-3 py-2 text-xs">
          <div className="text-gray-400">Outcome</div>
          <div className="mt-1 font-semibold text-gray-800">{formatMap(arm.outcome)}</div>
        </div>
        <div className="rounded-lg bg-white/80 px-3 py-2 text-xs">
          <div className="text-gray-400">Cost / Usage</div>
          <div className="mt-1 font-semibold text-gray-800">{formatMap(arm.cost)} · {formatMap(arm.usage)}</div>
        </div>
        <div className="rounded-lg bg-white/80 px-3 py-2 text-xs">
          <div className="text-gray-400">Latency / Risk</div>
          <div className="mt-1 font-semibold text-gray-800">{arm.latencyMs} ms · {formatMap(arm.risk)}</div>
        </div>
      </div>
      <div className="mt-3 grid gap-2 xl:grid-cols-2">
        <EvidenceDigest label="Run receipt" value={arm.receiptDigest} />
        <EvidenceDigest label="Sandbox attestation" value={arm.sandboxDigest} />
        <EvidenceDigest label="Verifier receipt" value={arm.verificationDigest} />
        <EvidenceDigest label="Verifier evidence" value={arm.evidenceDigest} />
      </div>
    </article>
  )
}

function ProposalRail({ report }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <Hourglass size={15} />
            Proposal-only handoff
          </div>
          <div className="mt-1 text-xs leading-5 text-amber-700">
            Lab 只生成演化提案；未经过真实 Review 与后续版本激活 authority，不影响生产。
          </div>
        </div>
        <span className="rounded-full border border-amber-200 bg-white px-2 py-1 text-xs font-semibold text-amber-800">
          {report.reviewStatus}
        </span>
      </div>
      <div className="mt-3 space-y-2">
        {report.proposals.map((proposal) => (
          <div key={proposal.proposalId} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-100 bg-white/85 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-gray-800">{proposal.targetKind} → {proposal.targetVersion}</div>
              <div className="mt-0.5 truncate font-mono text-[11px] text-gray-400" title={proposal.proposalDigest}>
                {proposal.proposalId} · {short(proposal.proposalDigest, 18)}
              </div>
            </div>
            {proposal.reviewHref ? (
              <a
                className="inline-flex items-center gap-1 rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white"
                href={proposal.reviewHref}
              >
                打开 Review
                <ArrowRight size={12} />
              </a>
            ) : (
              <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700">
                <LockKeyhole size={12} />
                等待 durable ReviewItem
              </span>
            )}
          </div>
        ))}
        {!report.proposals.length ? (
          <div className="text-xs font-medium text-rose-700">可信 Report 尚未绑定 Proposal。</div>
        ) : null}
      </div>
    </div>
  )
}

function ReportCard({ report }) {
  const comparison = report.comparisons[0]
  return (
    <article className="card overflow-hidden">
      <div className="border-b border-gray-100 bg-gradient-to-r from-indigo-50/80 via-white to-emerald-50/60 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-bold text-gray-900">
              <FlaskConical size={16} className="text-indigo-600" />
              {EVOLUTION_LAB_KIND_LABELS[report.kind] || report.kind}
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {report.experimentId} · workspace {report.workspaceId}
            </div>
          </div>
          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-1 text-xs font-semibold text-indigo-700">
            <ShieldCheck size={12} />
            evidence eligible
          </span>
        </div>
        <div className="mt-3 grid gap-2 md:grid-cols-3">
          <EvidenceDigest label="Report digest" value={report.reportDigest} />
          <EvidenceDigest label="Spec digest" value={report.specDigest} />
          <EvidenceDigest label="Dataset manifest" value={report.datasetManifestDigest} />
        </div>
        <div className="mt-2 text-xs text-gray-500">
          Dataset {report.datasetSourceKind} · {report.datasetRecordCount} records · seed {report.seed}
          {' · '}Verifier {report.verifier.identity}@{report.verifier.version}
        </div>
      </div>
      <div className="space-y-4 p-4">
        <div className="grid gap-3 xl:grid-cols-2">
          {report.arms.map((arm) => <ArmCard key={arm.runId} arm={arm} />)}
        </div>
        {comparison ? (
          <section className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-indigo-900">
              <GitCompareArrows size={15} />
              Counterfactual delta
            </div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <DeltaRow label="Outcome" values={comparison.outcomeDelta} />
              <DeltaRow label="Evidence" values={comparison.evidenceDelta} />
              <DeltaRow label="Cost" values={comparison.costDelta} />
              <DeltaRow label="Latency" values={comparison.latencyDelta} />
              <DeltaRow label="Risk" values={comparison.riskDelta} />
              <DeltaRow label="Verification" values={comparison.verificationDelta} />
            </div>
          </section>
        ) : null}
        <ProposalRail report={report} />
      </div>
    </article>
  )
}

export default function EvolutionLabConsole({
  suites,
  selectedSuiteId,
  onSelectSuite,
}) {
  const selected = suites.find((suite) => suite.suiteId === selectedSuiteId) || suites[0]
  if (!selected) {
    return (
      <div className="card p-8 text-center">
        <FlaskConical className="mx-auto text-gray-300" size={32} />
        <div className="mt-3 text-sm font-semibold text-gray-700">暂无可信 Golden Suite</div>
        <div className="mt-1 text-xs text-gray-400">运行并持久化三条 Docker 双臂实验后，此处才会出现证据。</div>
      </div>
    )
  }
  return (
    <div className="grid gap-4 2xl:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="card h-fit p-3">
        <div className="mb-3 flex items-center gap-2 px-1 text-sm font-semibold text-gray-900">
          <Boxes size={15} className="text-indigo-600" />
          Golden Suites
        </div>
        <div className="space-y-2">
          {suites.map((suite) => (
            <button
              key={suite.suiteId}
              type="button"
              onClick={() => onSelectSuite(suite.suiteId)}
              className={`w-full rounded-xl border p-3 text-left transition ${suite.suiteId === selected.suiteId ? 'border-indigo-300 bg-indigo-50 shadow-sm' : 'border-gray-100 bg-white hover:border-indigo-200'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-gray-800">{suite.suiteId}</span>
                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">{suite.status}</span>
              </div>
              <div className="mt-2 font-mono text-[11px] text-gray-400">{short(suite.commitSha, 12)}</div>
              <div className="mt-1 text-[11px] text-gray-500">{suite.reports.length} reports · {suite.reports.reduce((sum, report) => sum + report.arms.length, 0)} arms</div>
            </button>
          ))}
        </div>
      </aside>
      <main className="min-w-0 space-y-4">
        <section className="card p-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-base font-bold text-gray-900">
                <Container size={18} className="text-sky-600" />
                {selected.suiteId}
              </div>
              <div className="mt-1 text-xs text-gray-500">
                {selected.engineIdentity} · {selected.imageReference}
              </div>
            </div>
            <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
              <Braces size={12} />
              {selected.migrationHead}
            </span>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            <EvidenceDigest label="Suite receipt" value={selected.receiptDigest} />
            <EvidenceDigest label="Commit SHA" value={selected.commitSha} />
          </div>
        </section>
        {selected.reports.map((report) => (
          <ReportCard key={report.reportId} report={report} />
        ))}
      </main>
    </div>
  )
}
