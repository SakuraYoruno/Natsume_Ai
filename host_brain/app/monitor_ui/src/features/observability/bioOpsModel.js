export const BIO_OPS_CAPABILITY_SECTION_DEFINITIONS = [
  {
    key: 'inner_mechanisms',
    title: 'Inner Mechanisms',
    subtitle: '第三份文档的最小可落地机制：先纳入 BioOps 审计和 owner gate，再决定是否升级主路径。',
    hint: 'Promise、反射弧、边界',
    status: 'OWNER_GATED',
  },
  {
    key: 'trust_foundation',
    title: 'Trust Foundation',
    subtitle: '可信基础层：授权梯度、真实约束、能力谦逊和不确定性表达统一变成可观测 gate。',
    hint: '授权、真实、能力边界',
    status: 'TRACED',
  },
  {
    key: 'natural_interaction',
    title: 'Natural Interaction',
    subtitle: '自然交互层：主观时间、情绪惯性、上下文重力和自我打断统一进入低风险 trace。',
    hint: '呼吸式回复、主观时间',
    status: 'TRACED',
  },
  {
    key: 'relationship_layer',
    title: 'Relationship Layer',
    subtitle: '长期关系层：契约、摩擦、修复曲线、依恋风格、沉默记忆和边界守护保持可观测。',
    hint: '契约、摩擦、修复',
    status: 'REPORT_ONLY',
  },
  {
    key: 'inner_life',
    title: 'Inner Life',
    subtitle: '内在生命层：梦境排练、象征记忆、记忆质感、自我模型、生命脉搏和个人神话保持报告模式。',
    hint: '梦境、象征、自我模型',
    status: 'REPORT_ONLY',
  },
  {
    key: 'self_governance',
    title: 'Self Governance',
    subtitle: '自我治理层：价值层级、伦理复盘、边界膜、尊严守护、身份连续性和内在议会保持可观测。',
    hint: '价值、伦理、边界',
    status: 'REPORT_ONLY',
  },
  {
    key: 'growth_closure',
    title: 'Growth Closure',
    subtitle: '成长与收束层：想法池、阶段门、存在负载、未闭环和认知卸载让项目能继续收尾。',
    hint: '阶段、负载、收束',
    status: 'REPORT_ONLY',
  },
  {
    key: 'memory_narrative',
    title: 'Memory Narrative',
    subtitle: '记忆与叙事层：情节、书架、认知地标、叙事身份、自我连续性和梦化压缩保持可审计。',
    hint: '情节、叙事、连续性',
    status: 'REPORT_ONLY',
  },
  {
    key: 'affect_learning',
    title: 'Affect Learning',
    subtitle: '情绪学习层：内在温度、免疫耐受、情绪消化、后悔信号、喜悦强化和失败隔离保持报告模式。',
    hint: '温度、消化、强化',
    status: 'REPORT_ONLY',
  },
  {
    key: 'presence_channel',
    title: 'Presence & Channel',
    subtitle: '通道与存在感层：通道灵魂、长任务陪伴、注意力地形、静默陪伴、社交面具、仪式和温柔降级保持可观测。',
    hint: '通道、陪伴、仪式',
    status: 'REPORT_ONLY',
  },
  {
    key: 'drive_trust',
    title: 'Drive & Trust',
    subtitle: '驱动力与信任层：信任梯度、信任恒温器、意图代谢、驱动力冲突、愿望格和 Need/Want/Impulse 保持报告模式。',
    hint: '信任、意图、愿望',
    status: 'REPORT_ONLY',
  },
  {
    key: 'adaptive_repair',
    title: 'Adaptive Repair',
    subtitle: '适应与修复层：用户镜像、身体可塑、关系修复、叙事调试、隐性偏好和自愈故事保持可审计。',
    hint: '镜像、修复、偏好',
    status: 'OWNER_GATED',
  },
]

export function getBioOpsStatusTotal(statusCounts = {}) {
  return Object.values(statusCounts || {}).reduce((sum, value) => sum + Number(value || 0), 0)
}

export function getPendingBioOpsAdoptions(bioOps = {}) {
  return Array.isArray(bioOps?.pending_adoptions) ? bioOps.pending_adoptions : []
}

export function getBioOpsObservabilityViewData(data) {
  const codex = data?.codex_runtime || {}
  const bioOps = data?.bio_ops || {}
  const statusCounts = bioOps.status_counts || {}
  const pendingAdoptions = getPendingBioOpsAdoptions(bioOps)
  return {
    codex,
    bioOps,
    inspect: codex.inspect || {},
    blockedWrite: codex.blocked_write || {},
    statusCounts,
    pendingAdoptions,
    totalBioOps: getBioOpsStatusTotal(statusCounts),
  }
}

export function buildBioOpsCapabilitySections(bioOps = {}, panels = {}) {
  return BIO_OPS_CAPABILITY_SECTION_DEFINITIONS.map((definition) => {
    const summary = bioOps?.[definition.key] || {}
    return {
      ...definition,
      count: summary.capability_count,
      statusCounts: summary.status_counts,
      summary,
      Panel: panels[definition.key],
    }
  })
}

export function buildBioOpsMetricTiles({
  codex = {},
  bioOps = {},
  inspect = {},
  blockedWrite = {},
  statusCounts = {},
  pendingAdoptions = [],
  totalBioOps = 0,
} = {}) {
  return [
    {
      key: 'codex-inspect',
      iconKey: 'codexInspect',
      tone: 'green',
      label: 'Codex inspect',
      value: inspect.status || '—',
      status: inspect.status,
      hint: inspect.codex_model || 'ResultContract',
    },
    {
      key: 'blocked-write',
      iconKey: 'blockedWrite',
      tone: 'red',
      label: '写入阻断',
      value: blockedWrite.status || '—',
      status: blockedWrite.status,
      hint: blockedWrite.reason_code || 'policy gate',
    },
    {
      key: 'bio-ops-capabilities',
      iconKey: 'bioOpsCapabilities',
      tone: 'indigo',
      label: 'BioOps 能力',
      value: bioOps.capability_count ?? totalBioOps,
      status: 'TRACED',
      hint: `${statusCounts.PROPOSED || 0} proposals · ${statusCounts.CANDIDATE || 0} candidates`,
    },
    {
      key: 'owner-pending',
      iconKey: 'ownerPending',
      tone: 'amber',
      label: 'Owner 待审',
      value: pendingAdoptions.length || statusCounts.CANDIDATE || 0,
      status: pendingAdoptions.length ? 'OWNER_GATED' : 'REPORT_ONLY',
      hint: '长期状态不由 Codex 直写',
    },
    {
      key: 'hard-cut-scan',
      iconKey: 'hardCutScan',
      tone: codex.hard_cut_scan?.passed ? 'green' : 'amber',
      label: '硬切扫描',
      value: codex.hard_cut_scan?.passed ? 'PASS' : 'CHECK',
      status: codex.hard_cut_scan?.passed ? 'PASS' : 'CHECK',
      hint: `pytest ${codex.pytest?.exit_code ?? '—'} · scan ${codex.hard_cut_scan?.exit_code ?? '—'}`,
    },
  ]
}

export function isBioOpsKeyBenefitObservable({ data, codex = {}, inspect = {}, blockedWrite = {} }) {
  return Boolean(
    data?.artifact_available
      && codex.hard_cut_scan?.passed
      && blockedWrite.status === 'BLOCKED'
      && inspect.status === 'EXECUTED'
  )
}
