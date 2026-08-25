import {
  AlertTriangle,
  BrainCircuit,
  Clock3,
  Eye,
  GitBranch,
  Globe2,
  HeartHandshake,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { formatDateTime } from '../../../utils/digitalLife.js'
import { LifeSection } from './digitalLifeSectionShared.jsx'

const FACT_TONES = {
  fact: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  inference: 'border-violet-200 bg-violet-50 text-violet-700',
}

function Pill({ children, className = 'border-gray-200 bg-gray-50 text-gray-600' }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${className}`}>{children}</span>
}

function Refs({ refs = [] }) {
  if (!refs.length) return <span className="text-xs text-gray-400">无证据引用</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {refs.slice(0, 5).map((ref) => (
        <span key={ref} className="max-w-full break-all rounded-lg border border-gray-200 bg-white px-2 py-1 font-mono text-[10px] text-gray-500">
          {ref}
        </span>
      ))}
    </div>
  )
}

function WorldFactCard({ item }) {
  const stateTone = item.expired || item.conflicted
    ? 'border-orange-200 bg-orange-50 text-orange-700'
    : 'border-blue-100 bg-blue-50 text-blue-700'
  return (
    <div className={`rounded-xl border p-3 ${item.expired ? 'border-dashed border-orange-300 bg-orange-50/40' : 'border-white/80 bg-white/75'}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="break-all text-sm font-semibold text-gray-800">{item.key}</div>
          <div className="mt-1 break-words text-sm text-gray-600">{item.value}</div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Pill className={FACT_TONES[item.factClassTone]}>{item.factClassLabel}</Pill>
          <Pill className={stateTone}>{item.displayStateLabel}</Pill>
        </div>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-gray-500 sm:grid-cols-3">
        <div><span className="text-gray-400">来源</span><div className="mt-0.5 break-all font-medium text-gray-700">{item.source}</div></div>
        <div><span className="text-gray-400">置信度</span><div className="mt-0.5 font-medium text-gray-700">{item.confidence}</div></div>
        <div><span className="text-gray-400">TTL</span><div className="mt-0.5 font-medium text-gray-700">{item.ttl}</div></div>
      </div>
      <div className="mt-2 break-all font-mono text-[10px] text-gray-400">{item.sourceRef}</div>
      {item.conflicted ? <div className="mt-2 text-xs text-orange-700">冲突：{item.conflictState} {item.conflictDetail}</div> : null}
    </div>
  )
}

function WorldPanel({ world }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Globe2 size={16} className="text-primary" />
        <h3 className="text-sm font-semibold text-gray-800">World Projection</h3>
        <Pill>{world.status}</Pill>
        <span className="ml-auto text-xs text-gray-400">事实 {world.factCount} · 推断 {world.inferenceCount} · 冲突 {world.conflictCount} · 过期 {world.expiredCount}</span>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        {world.facts.length ? world.facts.map((item) => <WorldFactCard key={`${item.key}-${item.sourceRef}`} item={item} />) : (
          <div className="rounded-xl border border-dashed border-gray-200 p-4 text-sm text-gray-400">当前没有可公开的 World fact；不会用推断补成事实。</div>
        )}
      </div>
    </div>
  )
}

function AppraisalPanel({ items, privateBodyIncluded }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <BrainCircuit size={16} className="text-violet-600" />
        <h3 className="text-sm font-semibold text-gray-800">Appraisal Evidence</h3>
        <Pill className="border-violet-200 bg-violet-50 text-violet-700">派生只读</Pill>
      </div>
      {privateBodyIncluded ? <NoticeBanner variant="error" title="隐私正文门禁异常" className="mt-3">该读模型不应包含私密正文。</NoticeBanner> : null}
      <div className="mt-3 space-y-3">
        {items.length ? items.slice(0, 8).map((item) => (
          <div key={item.id} className="rounded-xl border border-white/80 bg-white/80 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-gray-800">{item.kind}</span>
              <Pill className="border-violet-200 bg-violet-50 text-violet-700">{item.epistemicType}</Pill>
              <span className="ml-auto text-xs text-gray-400">{formatDateTime(item.timestamp)}</span>
            </div>
            <div className="mt-2 grid gap-2 text-xs text-gray-600 sm:grid-cols-4">
              <span>结果 {item.outcome}</span><span>置信 {item.confidence}</span><span>效价 {item.valence}</span><span>唤醒 {item.arousal}</span>
            </div>
            <div className="mt-2 text-xs text-gray-500">Bond signal：{item.bondDirection} · {item.bondStrength} · 权限效果 {item.permissionEffect}</div>
            <div className="mt-2"><Refs refs={[item.sourceRef, ...item.evidenceRefs]} /></div>
          </div>
        )) : <div className="rounded-xl border border-dashed border-gray-200 p-4 text-sm text-gray-400">暂无可回放 Appraisal evidence；不会展示对话或记忆正文。</div>}
      </div>
    </div>
  )
}

function BondPanel({ bond }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <HeartHandshake size={16} className="text-rose-500" />
        <h3 className="text-sm font-semibold text-gray-800">Bond Projection</h3>
        <Pill className="border-rose-200 bg-rose-50 text-rose-700">只读兼容投影</Pill>
        <Pill>权限效果 {bond.permissionEffect}</Pill>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-white/80 bg-white/80 p-3"><div className="text-xs text-gray-400">阶段</div><div className="mt-1 font-semibold text-gray-800">{bond.stage}</div></div>
        <div className="rounded-xl border border-white/80 bg-white/80 p-3"><div className="text-xs text-gray-400">兼容分数</div><div className="mt-1 font-semibold text-gray-800">{bond.score}</div></div>
        <div className="rounded-xl border border-white/80 bg-white/80 p-3"><div className="text-xs text-gray-400">趋势</div><div className="mt-1 font-semibold text-gray-800">{bond.trend}</div></div>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2">
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-2">vector · {bond.vector}</div>
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-2">rift · {bond.rift}</div>
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-2">repair · {bond.repair}</div>
        <div className="rounded-xl border border-gray-100 bg-gray-50/70 p-2">boundary · {bond.boundary}</div>
      </div>
      <div className="mt-3"><Refs refs={bond.evidenceRefs} /></div>
      <div className="mt-2 text-[11px] text-gray-400">{bond.authorityLabel} · authoritative={String(bond.authoritative)} · {bond.epistemicType}</div>
    </div>
  )
}

function ScenePanel({ scene }) {
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <GitBranch size={16} className="text-blue-600" />
        <h3 className="text-sm font-semibold text-gray-800">Scene Decision</h3>
        <Pill>{scene.status}</Pill>
        {scene.expired ? <Pill className="border-orange-200 bg-orange-50 text-orange-700">已过期</Pill> : null}
        {scene.conflicted ? <Pill className="border-red-200 bg-red-50 text-red-700">来源冲突</Pill> : null}
      </div>
      <div className="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2">
        <span>场景：{scene.type}</span><span>置信度：{scene.confidence}</span>
        <span>TTL：{scene.ttl}</span><span>认识类型：{scene.epistemicType}</span>
        <span>记录策略：{scene.recordedPolicy}</span><span>当前有效策略：{scene.effectivePolicy}</span>
        <span>表达模式：{scene.expressionMode}</span><span>权限效果：{scene.permissionEffect}</span>
      </div>
      <div className="mt-3"><Refs refs={[scene.observationSourceRef, ...scene.sourceRefs].filter((item) => item && item !== '--')} /></div>
      <div className="mt-2 text-[11px] text-gray-400">actual producer={String(scene.observedFromActualProducer)}</div>
    </div>
  )
}

function PreviewPanel({ preview, busy, error, onPreview }) {
  const blocked = preview.decision === 'BLOCK' || !preview.wouldExecute
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck size={16} className={blocked ? 'text-orange-600' : 'text-emerald-600'} />
        <h3 className="text-sm font-semibold text-gray-800">ActionArbiter Dry-run</h3>
        <Pill className={blocked ? 'border-orange-200 bg-orange-50 text-orange-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}>{preview.decision}</Pill>
        <Pill>{preview.executionState}</Pill>
        {onPreview ? <button type="button" className="btn-secondary ml-auto text-xs" disabled={busy} onClick={onPreview}><RefreshCw size={13} className={busy ? 'animate-spin' : ''} />只读重算</button> : null}
      </div>
      {error ? <NoticeBanner variant="warning" title="dry-run 暂不可用" className="mt-3">{error.message || String(error)}</NoticeBanner> : null}
      <div className="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2">
        <span>状态：{preview.status}</span><span>最终性：{preview.finality}</span>
        <span>会执行：{String(preview.wouldExecute)}</span><span>Arbiter eligible：{String(preview.arbiterEligible)}</span>
        <span>已执行：{String(preview.executionPerformed)}</span><span>Handler precheck：{preview.executionPrecheckStatus}</span>
        <span>Governor：{preview.governorDecision}</span><span>用户目标保持：{String(preview.userGoalPreserved)}</span>
        <span>Authority context：{preview.authorityContextStatus}</span><span className="break-all">来源：{preview.authoritySourceRef}</span>
        <span className="sm:col-span-2">Authority validation：{preview.authorityValidationReason}</span>
      </div>
      <div className="mt-3 rounded-xl border border-gray-100 bg-gray-50/70 p-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-gray-700"><Clock3 size={13} />为什么现在 / 最终裁决依据</div>
        <div className="mt-2 flex flex-wrap gap-1.5">{preview.whyNow.length ? preview.whyNow.map((item) => <Pill key={item}>{item}</Pill>) : <span className="text-xs text-gray-400">没有可用 authority context，默认拒绝执行。</span>}</div>
      </div>
      <div className="mt-3"><Refs refs={preview.evidenceRefs} /></div>
      <div className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-400"><Eye size={12} />dry-run 永不调用 executor，也不记录 Runtime Event。</div>
    </div>
  )
}

function ArbiterDecisionPanel({ arbiter }) {
  const unavailable = arbiter.status === 'UNAVAILABLE'
  return (
    <div className="rounded-2xl border border-white/80 bg-white/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck size={16} className={unavailable ? 'text-gray-400' : 'text-cyan-600'} />
        <h3 className="text-sm font-semibold text-gray-800">最近实际 ActionArbiter</h3>
        <Pill>{arbiter.status}</Pill>
        <Pill className={arbiter.decision === 'BLOCK' ? 'border-orange-200 bg-orange-50 text-orange-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}>{arbiter.decision}</Pill>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2">
        <span>执行态：{arbiter.executionState}</span><span>Governor：{arbiter.governorDecision}</span>
        <span>binding valid：{String(arbiter.authorityBindingValid)}</span><span>用户目标保持：{String(arbiter.userGoalPreserved)}</span>
        <span className="sm:col-span-2">最终性：{arbiter.finality}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">{[...arbiter.reasonCodes, ...arbiter.restrictions].map((item) => <Pill key={item}>{item}</Pill>)}</div>
      <div className="mt-3"><Refs refs={arbiter.evidenceRefs} /></div>
      <div className="mt-2 text-[11px] text-gray-400">actual producer={String(arbiter.observedFromActualProducer)}；这是裁决观测，不代表 UI 执行过动作。</div>
    </div>
  )
}

export default function LifeEvolutionSection({ query, model, previewBusy = false, previewError = null, onPreview }) {
  const error = query?.error
  return (
    <LifeSection title="长期演化只读视图" hint="World、Appraisal、Bond、Scene 与最终裁决统一在版本化 read model 中；事实、推断、冲突和过期状态不会互相伪装。">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-gray-500">
        <Pill className="border-emerald-200 bg-emerald-50 text-emerald-700">事实</Pill>
        <Pill className="border-violet-200 bg-violet-50 text-violet-700">推断</Pill>
        <Pill className="border-gray-200 bg-gray-50 text-gray-600">只读 · 无写 API</Pill>
        {model.generatedAt ? <span className="ml-auto">生成于 {formatDateTime(model.generatedAt)}</span> : null}
      </div>
      {error ? <NoticeBanner variant="warning" title="长期演化视图未启用或暂不可用" className="mb-3">{error.message || String(error)}</NoticeBanner> : null}
      {!model.available && query?.loading ? <div className="rounded-xl border border-dashed border-gray-200 p-5 text-sm text-gray-400">正在读取版本化 Life evolution snapshot…</div> : null}
      {!model.available && !query?.loading ? <div className="rounded-xl border border-dashed border-gray-200 p-5 text-sm text-gray-400"><AlertTriangle size={15} className="mr-1 inline" />没有可用 read model；核心 Life 后端不受影响，也不会为 UI 自动初始化状态。</div> : null}
      {model.available ? (
        <div className="space-y-4">
          <WorldPanel world={model.world} />
          <div className="grid gap-4 xl:grid-cols-2"><AppraisalPanel items={model.appraisals} privateBodyIncluded={model.appraisalPrivateBodyIncluded} /><BondPanel bond={model.bond} /></div>
          <div className="grid gap-4 xl:grid-cols-3"><ScenePanel scene={model.scene} /><ArbiterDecisionPanel arbiter={model.arbiter} /><PreviewPanel preview={model.preview} busy={previewBusy} error={previewError} onPreview={onPreview} /></div>
        </div>
      ) : null}
    </LifeSection>
  )
}
