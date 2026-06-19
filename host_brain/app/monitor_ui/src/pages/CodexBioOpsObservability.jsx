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
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import {
  BioOpsSkeletonGrid,
  MetricTile,
  Section,
} from '../components/bioOps/BioOpsSurfaces.jsx'
import {
  ArtifactList,
  BenefitCard,
  CodexResultCard,
  ContractFieldGrid,
  FocusEffectList,
  GroupTable,
  RuntimeTraceList,
  StatusDistribution,
} from '../components/bioOps/BioOpsSummarySections.jsx'
import { OwnerInboxPanel, PendingAdoptions } from '../components/bioOps/BioOpsOwnerPanels.jsx'
import { CapabilityWorkbench } from '../components/bioOps/BioOpsWorkbench.jsx'
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
} from '../components/bioOps/BioOpsCapabilityPanels.jsx'
import useAppStore from '../store/useAppStore.js'
import { runCodexBioOpsDailyCycle } from '../features/observability/api.js'
import { useCodexBioOpsObservabilityQuery } from '../features/observability/queries.js'

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
