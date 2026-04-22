import {
  compactText,
  DASH,
  formatAbsenceBand,
  formatAlertTitle,
  formatDegradedReason,
  formatDigitalLifeTag,
  formatDominantMotive,
  formatMode,
  formatNonLiveSlotList,
  formatReplyPosture,
  formatRiskLevel,
  formatRuntimeSignal,
  formatStage,
  normalizeText,
} from './digitalLife.js'

const SEVERITY_ORDER = {
  critical: 0,
  warning: 1,
  info: 2,
}

function buildHeadline({
  mode,
  bondStage,
  replyPosture,
  dominantMotive,
  absenceBand,
  dominantMotiveRaw,
  absenceBandRaw,
}) {
  const parts = [`当前处于${mode}`]
  const motiveKey = normalizeText(dominantMotiveRaw).toLowerCase()
  const absenceKey = normalizeText(absenceBandRaw).toLowerCase()

  if (bondStage && bondStage !== DASH) {
    parts.push(`关系阶段为${bondStage}`)
  }
  if (replyPosture && replyPosture !== DASH) {
    parts.push(`回复姿态是${replyPosture}`)
  }
  if (dominantMotive && dominantMotive !== DASH && motiveKey && motiveKey !== 'none') {
    parts.push(`当前主导动机为${dominantMotive}`)
  } else if (absenceBand && absenceBand !== DASH && absenceKey && absenceKey !== 'recent') {
    parts.push(`当前处于${absenceBand}`)
  }

  return `${parts.join('，')}。`
}

export function getNonLiveSlots(snapshot) {
  return Object.entries(snapshot?.slot_status || {}).filter(([, value]) => value !== 'live')
}

export function buildDigitalLifeAlerts(snapshot, options = {}) {
  const promptStats = options.promptStats || null
  const capabilityGapData = options.capabilityGapData || null
  const alerts = []
  const tsMs = snapshot?.timestamp || Date.now()

  if (!snapshot) {
    return alerts
  }

  const sysModeStatus = snapshot.sys_mode_status || {}
  const reflectionGuard = snapshot.self_reflection_pulse?.narrative_guardrails || {}
  const silenceState = snapshot.silence_state || {}
  const renderPolicy = snapshot.render_policy || {}
  const runtimeSummary = snapshot.runtime_summary || {}
  const degradedReasons = snapshot.degraded_reasons || []
  const nonLiveSlots = getNonLiveSlots(snapshot)

  if (sysModeStatus.degraded || sysModeStatus.degraded_reason) {
    const degradedReason = normalizeText(sysModeStatus.degraded_reason)
    alerts.push({
      reason_code: 'RUNTIME_MODE_DEGRADED',
      title: formatAlertTitle('RUNTIME_MODE_DEGRADED'),
      detail: degradedReason
        ? `生命体存在态轴当前处于降级状态：${degradedReason}`
        : '生命体存在态轴当前处于降级状态。',
      ts_ms: tsMs,
      severity: 'warning',
      source: 'runtime',
      to: '/runtime-mode',
    })
  }

  if (degradedReasons.length > 0) {
    const detail = degradedReasons.map(formatDegradedReason).filter(Boolean).join('；')
    alerts.push({
      reason_code: 'RUNTIME_SNAPSHOT_DEGRADED',
      title: formatAlertTitle('RUNTIME_SNAPSHOT_DEGRADED'),
      detail: compactText(detail || '运行快照中仍有关键状态未补齐。', 180),
      ts_ms: tsMs,
      severity: 'warning',
      source: 'runtime',
      to: '/digital-life',
    })
  }

  if (nonLiveSlots.length > 0) {
    alerts.push({
      reason_code: 'RUNTIME_SLOT_NOT_LIVE',
      title: formatAlertTitle('RUNTIME_SLOT_NOT_LIVE'),
      detail: compactText(`共有 ${nonLiveSlots.length} 个关键槽位未在线：${formatNonLiveSlotList(nonLiveSlots)}`, 180),
      ts_ms: tsMs,
      severity: 'warning',
      source: 'runtime',
      to: '/digital-life',
    })
  }

  const riskLevel = String(reflectionGuard.risk_level || '').toLowerCase()
  if (riskLevel && riskLevel !== 'low' && riskLevel !== 'none') {
    const riskTags = Array.isArray(reflectionGuard.risk_tags)
      ? reflectionGuard.risk_tags.map(formatDigitalLifeTag).filter(Boolean)
      : []

    alerts.push({
      reason_code: 'REFLECTION_GUARD_ACTIVE',
      title: formatAlertTitle('REFLECTION_GUARD_ACTIVE'),
      detail: compactText(
        `当前自我反思护栏风险为${formatRiskLevel(riskLevel)}${riskTags.length ? `，涉及 ${riskTags.join('、')}` : ''}。`,
        180
      ),
      ts_ms: tsMs,
      severity: riskLevel === 'high' || riskLevel === 'critical' ? 'critical' : 'warning',
      source: 'runtime',
      to: '/self-model',
    })
  }

  if (silenceState.topic_resistance_active) {
    alerts.push({
      reason_code: 'TOPIC_RESISTANCE_ACTIVE',
      title: formatAlertTitle('TOPIC_RESISTANCE_ACTIVE'),
      detail: `当前对“${silenceState.resistant_topic || '该话题'}”存在明显抗拒，回复会更偏谨慎。`,
      ts_ms: tsMs,
      severity: 'warning',
      source: 'runtime',
      to: '/render-policy',
    })
  }

  if (renderPolicy.should_be_withdrawn) {
    alerts.push({
      reason_code: 'WITHDRAWN_REPLY_POLICY',
      title: formatAlertTitle('WITHDRAWN_REPLY_POLICY'),
      detail: `当前策略更偏短答与收敛，回复姿态为${formatReplyPosture(runtimeSummary.reply_posture?.style)}。`,
      ts_ms: tsMs,
      severity: 'info',
      source: 'runtime',
      to: '/render-policy',
    })
  }

  if ((promptStats?.injection_detected_count ?? 0) > 0) {
    alerts.push({
      reason_code: 'PROMPT_INJECTION_DETECTED',
      title: formatAlertTitle('PROMPT_INJECTION_DETECTED'),
      detail: `当前时间窗内检测到 ${promptStats.injection_detected_count} 次提示注入风险。`,
      ts_ms: tsMs,
      severity: 'critical',
      source: 'prompt',
      to: '/prompt-config',
    })
  }

  if ((promptStats?.high_risk_count ?? 0) > 0) {
    alerts.push({
      reason_code: 'PROMPT_HIGH_RISK_BUILD',
      title: formatAlertTitle('PROMPT_HIGH_RISK_BUILD'),
      detail: `当前时间窗内存在 ${promptStats.high_risk_count} 次高风险 Prompt 构建。`,
      ts_ms: tsMs,
      severity: 'warning',
      source: 'prompt',
      to: '/prompt-config',
    })
  }

  const backgroundRuntime = snapshot.background_runtime || {}
  const hasBackgroundRuntime = backgroundRuntime && typeof backgroundRuntime === 'object' && Object.keys(backgroundRuntime).length > 0
  if (hasBackgroundRuntime) {
    const schedulerStatus = String(backgroundRuntime.scheduler_status || 'UNKNOWN').toUpperCase()
    const tickHealth = String(backgroundRuntime.tick_health || 'unknown').toLowerCase()
    const isAdvancing = Boolean(backgroundRuntime.is_advancing)
    const innerTickAgo = backgroundRuntime.seconds_since_inner_world_tick
    const missingTicks = Array.isArray(backgroundRuntime.core_ticks_missing)
      ? backgroundRuntime.core_ticks_missing.filter(Boolean)
      : []

    if (!isAdvancing) {
      alerts.push({
        reason_code: 'BACKGROUND_RUNTIME_NOT_ADVANCING',
        title: formatAlertTitle('BACKGROUND_RUNTIME_NOT_ADVANCING'),
        detail: compactText(
          `后台推进未达预期：tick=${tickHealth || 'unknown'}，scheduler=${schedulerStatus}，inner_world_tick 距今 ${innerTickAgo ?? 'unknown'}s。`,
          180
        ),
        ts_ms: tsMs,
        severity: tickHealth === 'stalled' ? 'critical' : 'warning',
        source: 'runtime',
        to: '/inner-world',
      })
    }

    if (missingTicks.length > 0) {
      alerts.push({
        reason_code: 'BACKGROUND_CORE_TICK_MISSING',
        title: formatAlertTitle('BACKGROUND_CORE_TICK_MISSING'),
        detail: compactText(`后台核心任务缺失：${missingTicks.join('、')}。`, 180),
        ts_ms: tsMs,
        severity: 'warning',
        source: 'runtime',
        to: '/automation',
      })
    }

    if (backgroundRuntime.persona_user_alignment_ok === false) {
      alerts.push({
        reason_code: 'BACKGROUND_PERSONA_MISMATCH',
        title: formatAlertTitle('BACKGROUND_PERSONA_MISMATCH'),
        detail: compactText('内在世界 user_id 与当前人格 user_id 未对齐，后台推进可能写入错误用户。', 180),
        ts_ms: tsMs,
        severity: 'critical',
        source: 'runtime',
        to: '/inner-world',
      })
    }
  }

  const interoceptionState = snapshot.interoception_state || {}
  const interoceptiveIndex = Number(interoceptionState.interoceptive_index || 0)
  const recoveryUrgency = Number(interoceptionState.recovery_urgency || 0)
  if (interoceptiveIndex >= 0.82 || recoveryUrgency >= 0.78) {
    alerts.push({
      reason_code: 'INTEROCEPTION_OVERLOAD',
      title: formatAlertTitle('INTEROCEPTION_OVERLOAD'),
      detail: compactText(
        `内感受负载升高：interoceptive_index=${interoceptiveIndex.toFixed(3)}，recovery_urgency=${recoveryUrgency.toFixed(3)}。`,
        180
      ),
      ts_ms: tsMs,
      severity: interoceptiveIndex >= 0.88 || recoveryUrgency >= 0.85 ? 'critical' : 'warning',
      source: 'runtime',
      to: '/overview',
    })
  }

  const vestibularState = snapshot.vestibular_state || {}
  const vestibularJitter = Number(vestibularState.jitter_index || 0)
  const vestibularStability = String(vestibularState.stability_state || '').toLowerCase()
  if (vestibularStability === 'critical' || vestibularStability === 'unstable' || vestibularJitter >= 0.72) {
    alerts.push({
      reason_code: 'VESTIBULAR_INSTABILITY',
      title: formatAlertTitle('VESTIBULAR_INSTABILITY'),
      detail: compactText(
        `前庭稳定性异常：state=${vestibularStability || 'unknown'}，jitter=${vestibularJitter.toFixed(3)}。`,
        180
      ),
      ts_ms: tsMs,
      severity: vestibularStability === 'critical' || vestibularJitter >= 0.82 ? 'critical' : 'warning',
      source: 'runtime',
      to: '/overview',
    })
  }

  const motorCortexState = snapshot.motor_cortex_state || {}
  if (
    motorCortexState.last_plan_id
    && motorCortexState.last_dispatch_ok === false
  ) {
    alerts.push({
      reason_code: 'MOTOR_DISPATCH_DEGRADED',
      title: formatAlertTitle('MOTOR_DISPATCH_DEGRADED'),
      detail: compactText(
        `动作分发失败：plan=${motorCortexState.last_plan_id}，reason=${motorCortexState.last_dispatch_reason || 'unknown'}。`,
        180
      ),
      ts_ms: tsMs,
      severity: 'warning',
      source: 'runtime',
      to: '/overview',
    })
  }

  const homeostasisReport = snapshot.homeostasis_report || {}
  const homeostasisStatus = String(homeostasisReport.status || '').toLowerCase()
  if (homeostasisStatus === 'warning' || homeostasisStatus === 'critical') {
    alerts.push({
      reason_code: 'HOMEOSTASIS_WARNING',
      title: formatAlertTitle('HOMEOSTASIS_WARNING'),
      detail: compactText(
        `生命体稳态告警：status=${homeostasisStatus}，minute_load=${homeostasisReport?.minute_window?.avg_cognitive_load_index ?? 'n/a'}，hour_stress=${homeostasisReport?.hour_window?.avg_stress_index ?? 'n/a'}。`,
        180
      ),
      ts_ms: tsMs,
      severity: homeostasisStatus === 'critical' ? 'critical' : 'warning',
      source: 'runtime',
      to: '/overview',
    })
  }

  const forgeRuntimeSummary = capabilityGapData?.forge_runtime_quality?.summary || {}
  const criticalProposals = Number(forgeRuntimeSummary.critical_proposals || 0)
  const unhealthyProposals = Number(forgeRuntimeSummary.unhealthy_proposals || 0)
  const trackedProposals = Number(forgeRuntimeSummary.tracked_proposals || 0)
  const avgHealthScore = Number(forgeRuntimeSummary.avg_health_score || 0)
  if (criticalProposals > 0) {
    alerts.push({
      reason_code: 'FORGE_RUNTIME_CRITICAL_PROPOSALS',
      title: formatAlertTitle('FORGE_RUNTIME_CRITICAL_PROPOSALS'),
      detail: compactText(
        `Forge runtime has ${criticalProposals} critical proposals (unhealthy=${unhealthyProposals}, tracked=${trackedProposals}, avg_health=${avgHealthScore.toFixed(1)}).`,
        180
      ),
      ts_ms: tsMs,
      severity: 'critical',
      source: 'forge-runtime',
      to: '/alerts?reason_code=FORGE_RUNTIME_CRITICAL_PROPOSALS',
    })
  } else if (unhealthyProposals > 0) {
    alerts.push({
      reason_code: 'FORGE_RUNTIME_UNHEALTHY_PROPOSALS',
      title: formatAlertTitle('FORGE_RUNTIME_UNHEALTHY_PROPOSALS'),
      detail: compactText(
        `Forge runtime has ${unhealthyProposals} unhealthy proposals (tracked=${trackedProposals}, avg_health=${avgHealthScore.toFixed(1)}).`,
        180
      ),
      ts_ms: tsMs,
      severity: 'warning',
      source: 'forge-runtime',
      to: '/alerts?reason_code=FORGE_RUNTIME_UNHEALTHY_PROPOSALS',
    })
  }

  return alerts.sort((a, b) => {
    const severityDelta = (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
    if (severityDelta !== 0) return severityDelta
    return Number(b.ts_ms || 0) - Number(a.ts_ms || 0)
  })
}

export function getDigitalLifeRuntimeSummary(snapshot) {
  const runtimeSummary = snapshot?.runtime_summary || {}
  const dominantMotiveRaw = snapshot?.motive_state?.dominant_motive
  const absenceBandRaw = snapshot?.offline_state?.absence_band || runtimeSummary?.current_state?.absence_band
  const mode = formatMode(snapshot?.sys_mode)
  const bondStage = formatStage(snapshot?.bond_state?.current_stage)
  const replyPosture = formatReplyPosture(runtimeSummary.reply_posture?.style)
  const dominantMotive = formatDominantMotive(dominantMotiveRaw)
  const riskLevel = formatRiskLevel(snapshot?.self_reflection_pulse?.narrative_guardrails?.risk_level)
  const absenceBand = formatAbsenceBand(absenceBandRaw)

  return {
    mode,
    bondStage,
    replyPosture,
    dominantMotive,
    riskLevel,
    absenceBand,
    headline: buildHeadline({
      mode,
      bondStage,
      replyPosture,
      dominantMotive,
      absenceBand,
      dominantMotiveRaw,
      absenceBandRaw,
    }),
    notableSignals: (runtimeSummary.notable_signals || []).map(formatRuntimeSignal).filter(Boolean),
  }
}

