import React, { useMemo, useState } from 'react'
import {
  Activity,
  CheckCircle2,
  Database,
  GitBranch,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import EmptyStateCard from '../shared/ui/EmptyStateCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import {
  BioOpsSkeletonGrid,
  MetricTile,
  Section,
} from '../features/observability/components/bioOps/BioOpsSurfaces.jsx'
import {
  ArtifactList,
  BenefitCard,
  CodexResultCard,
  ContractFieldGrid,
  FocusEffectList,
  GroupTable,
  RuntimeTraceList,
  StatusDistribution,
} from '../features/observability/components/bioOps/BioOpsSummarySections.jsx'
import { OwnerInboxPanel, PendingAdoptions } from '../features/observability/components/bioOps/BioOpsOwnerPanels.jsx'
import { CapabilityWorkbench } from '../features/observability/components/bioOps/BioOpsWorkbench.jsx'
import {
  AdaptiveRepairPanel,
  AffectLearningPanel,
  DriveTrustPanel,
  GrowthClosurePanel,
  InnerLifePanel,
  InnerMechanismPanel,
  MemoryNarrativePanel,
  NaturalInteractionPanel,
  PresenceChannelPanel,
  RelationshipLayerPanel,
  SelfGovernancePanel,
  TrustFoundationPanel,
} from '../features/observability/components/bioOps/BioOpsCapabilityPanels.jsx'
import useAppStore from '../store/useAppStore.js'
import { runCodexBioOpsDailyCycle } from '../features/observability/bioOpsApi.js'
import {
  buildBioOpsCapabilitySections,
  buildBioOpsMetricTiles,
  getBioOpsObservabilityViewData,
  isBioOpsKeyBenefitObservable,
} from '../features/observability/bioOpsModel.js'
import { useCodexBioOpsObservabilityQuery } from '../features/observability/codexBioOpsQueries.js'
import { formatDateTime } from '../shared/formatters.js'

const BIO_OPS_CAPABILITY_PANELS = {
  inner_mechanisms: InnerMechanismPanel,
  trust_foundation: TrustFoundationPanel,
  natural_interaction: NaturalInteractionPanel,
  relationship_layer: RelationshipLayerPanel,
  inner_life: InnerLifePanel,
  self_governance: SelfGovernancePanel,
  growth_closure: GrowthClosurePanel,
  memory_narrative: MemoryNarrativePanel,
  affect_learning: AffectLearningPanel,
  presence_channel: PresenceChannelPanel,
  drive_trust: DriveTrustPanel,
  adaptive_repair: AdaptiveRepairPanel,
}

const BIO_OPS_METRIC_ICONS = {
  codexInspect: GitBranch,
  blockedWrite: Lock,
  bioOpsCapabilities: Sparkles,
  ownerPending: ShieldCheck,
  hardCutScan: CheckCircle2,
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
  const bioOpsView = useMemo(() => getBioOpsObservabilityViewData(data), [data])
  const {
    codex,
    bioOps,
    inspect,
    blockedWrite,
    pendingAdoptions,
    statusCounts,
  } = bioOpsView
  const capabilitySections = useMemo(
    () => buildBioOpsCapabilitySections(bioOps, BIO_OPS_CAPABILITY_PANELS),
    [bioOps]
  )
  const metricTiles = useMemo(() => buildBioOpsMetricTiles(bioOpsView), [bioOpsView])
  const keyBenefitObservable = isBioOpsKeyBenefitObservable({ data, codex, inspect, blockedWrite })
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
      <div className="rounded-lg border border-white/80 bg-white/86 p-5 shadow-card backdrop-blur-sm">
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
        <BioOpsSkeletonGrid />
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
            {metricTiles.map((tile) => (
              <MetricTile
                key={tile.key}
                icon={BIO_OPS_METRIC_ICONS[tile.iconKey]}
                tone={tile.tone}
                label={tile.label}
                value={tile.value}
                status={tile.status}
                hint={tile.hint}
              />
            ))}
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
              className="overflow-hidden rounded-lg border border-white/80 bg-white/86 shadow-sm"
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
            aside={query.lastUpdatedAt ? <span className="text-xs text-gray-400">updated {formatDateTime(query.lastUpdatedAt)}</span> : null}
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

      {keyBenefitObservable ? (
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
