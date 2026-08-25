import assert from 'node:assert/strict'
import test from 'node:test'

import {
  LOAD_STATES,
  ORGAN_STATUS,
  clampPercent,
  createOrganSystemModel,
  getOrganStatusMeta,
  metric,
  toFixed,
} from './organSystemCore.js'
import {
  buildBondOrganSystemModel,
  buildLimbicOrganSystemModel,
  buildMemoryOrganSystemModel,
  buildRuntimeModeOrganSystemModel,
  buildSelfOrganSystemModel,
} from './organSystemLifeModels.js'
import {
  buildEndocrineOrganSystemModel,
  buildMotorOrganSystemModel,
  buildToolsOrganSystemModel,
} from './organSystemExecutionModels.js'

test('生命器官模型保留真实异常、不可测值和下钻入口', () => {
  const limbic = buildLimbicOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 100,
    moodLabel: '受伤但克制',
    moodKey: 'critical',
    emotionTagLabel: 'contained_hurt',
    bondStageLabel: 'FRIEND',
    relationshipTemperature: 1.5,
    bondEventCount: null,
    gateLabel: '保护性门控',
    gateState: 'protective',
    degradeRate: 20,
    applyRate: null,
    talkGateAllowed: 3,
    sampleCount: 0,
    cortisolLevel: 0.62,
    emotionalStability: 71.4,
  })
  assert.equal(limbic.status, ORGAN_STATUS.CRITICAL)
  assert.match(limbic.summary, /contained_hurt/)
  assert.ok(limbic.symptoms.some((item) => item.includes('100°')))
  assert.ok(limbic.symptoms.some((item) => item.includes('不可测')))
  assert.equal(limbic.drilldownPath, '/life/relationship')

  const memory = buildMemoryOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 101,
    hasData: true,
    hasSamples: true,
    averageSuccessRate: 42,
    sampledSubsystemCount: 2,
    configuredSubsystemCount: 5,
    residentDocCount: 4,
    promptContextAvailable: false,
    observedDistillTerminalEvents: 7,
    episodicWrites: 8,
  })
  assert.equal(memory.status, ORGAN_STATUS.CRITICAL)
  assert.match(memory.riskLevel, /42%/)
  assert.ok(memory.downstreamImpacts.some((item) => item.includes('静态设定')))

  const bond = buildBondOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 102,
    healthStatus: '离线',
    selectedUser: '阿晓',
    userCount: 1,
    stageSampleCount: 3,
    currentStageLabel: '亲近',
    trendLabel: '下降',
    totalScore: 18,
    eventCount: 6,
    capabilityHint: '需要修复关系',
    lastEventLabel: '刚刚',
  })
  assert.equal(bond.status, ORGAN_STATUS.CRITICAL)
  assert.match(bond.summary, /阿晓/)
  assert.ok(bond.downstreamImpacts.some((item) => item.includes('最近关系事件')))

  const runtime = buildRuntimeModeOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 103,
    currentModeLabel: '锁定',
    currentModeKey: 'locked',
    schedulerLabel: '停止',
    idleMinutes: 12,
    validTransitionCount: 0,
    enabledRuleCount: 1,
    totalRuleCount: 4,
    isNightTime: true,
    degraded: false,
  })
  assert.equal(runtime.status, ORGAN_STATUS.CRITICAL)
  assert.equal(runtime.riskLevel, '锁定')
  assert.ok(runtime.symptoms.some((item) => item.includes('夜间窗口')))

  const self = buildSelfOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 104,
    recentMoodTrendLabel: '波动',
    riskLevelLabel: '高',
    riskLevelRaw: 'high',
    growthHeadline: '仍在整理刚才的误解',
    sharedMemoryCount: 4,
    milestoneCount: 2,
    growthCycleCount: 3,
    activeThemeCount: 1,
    personalityChangeCount: 0,
    promptDirectiveCount: 2,
    bondStageLabel: 'FRIEND',
    allowedStyleLabel: '克制',
  })
  assert.equal(self.status, ORGAN_STATUS.CRITICAL)
  assert.match(self.summary, /误解/)
  assert.ok(self.downstreamImpacts.some((item) => item.includes('2 条提示指令')))
})

test('生命器官模型不会把无样本、低成功率和降级伪装成稳定', () => {
  assert.equal(buildLimbicOrganSystemModel({ loadState: LOAD_STATES.LOADING }).status, ORGAN_STATUS.PENDING)
  assert.equal(buildMemoryOrganSystemModel({ loadState: LOAD_STATES.READY, hasData: true, hasSamples: true, averageSuccessRate: 70 }).status, ORGAN_STATUS.WARNING)
  assert.equal(buildMemoryOrganSystemModel({ loadState: LOAD_STATES.READY, hasData: true, hasSamples: true, averageSuccessRate: 90 }).status, ORGAN_STATUS.STABLE)
  assert.equal(buildBondOrganSystemModel({ loadState: LOAD_STATES.READY, healthStatus: '降级' }).status, ORGAN_STATUS.WARNING)
  assert.equal(buildRuntimeModeOrganSystemModel({ loadState: LOAD_STATES.READY, currentModeKey: 'maintenance' }).status, ORGAN_STATUS.WARNING)
  assert.equal(buildSelfOrganSystemModel({ loadState: LOAD_STATES.READY, riskLevelRaw: 'medium' }).status, ORGAN_STATUS.WARNING)
})

test('执行器官模型区分停摆、平台错误和工具健康', () => {
  const endocrine = buildEndocrineOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 200,
    schedulerState: { running: true, label: '运行', title: '节律推进正常' },
    taskCount: 6,
    enabledCount: 5,
    runningCount: 2,
    failedCount: 1,
    filteredCount: 2,
    hasTaskFilters: true,
    backgroundRuntime: { is_advancing: true, core_ticks_missing: [] },
  })
  assert.equal(endocrine.status, ORGAN_STATUS.CRITICAL)
  assert.match(endocrine.riskLevel, /1 个异常任务/)
  assert.ok(endocrine.symptoms.some((item) => item.includes('筛选命中 2')))

  const motor = buildMotorOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 201,
    platformCount: 3,
    activePlatformCount: 2,
    downPlatformCount: 1,
    degradedPlatformCount: 0,
    totalInbound: 5,
    totalOutbound: 4,
    totalErrors: 1,
    humanizeEnabled: true,
    humanizeTypingSpeed: 8,
    rateLimitCount: 2,
    recentEventCount: 7,
  })
  assert.equal(motor.status, ORGAN_STATUS.CRITICAL)
  assert.match(motor.summary, /入站 5/)
  assert.ok(motor.downstreamImpacts.some((item) => item.includes('答应了但没做到')))

  const tools = buildToolsOrganSystemModel({
    loadState: LOAD_STATES.READY,
    updatedAt: 202,
    healthStatus: 'DEGRADED',
    catalogSize: 9,
    readCount: 6,
    writeCount: 3,
    filter: 'WRITE',
    query: 'memory',
    runtimeSummary: 'provider 等待恢复',
  })
  assert.equal(tools.status, ORGAN_STATUS.WARNING)
  assert.match(tools.summary, /需要关注/)
  assert.ok(tools.symptoms.some((item) => item.includes('memory')))
  assert.ok(tools.downstreamImpacts.some((item) => item.includes('审批边界')))
})

test('执行器官模型覆盖无样本、推进异常和健康路径', () => {
  assert.equal(buildEndocrineOrganSystemModel({ loadState: LOAD_STATES.UNAVAILABLE }).status, ORGAN_STATUS.PENDING)
  assert.equal(buildEndocrineOrganSystemModel({ loadState: LOAD_STATES.READY, schedulerState: { running: false }, failedCount: 0, backgroundRuntime: { is_advancing: false, core_ticks_missing: ['inner_world'] } }).status, ORGAN_STATUS.WARNING)
  assert.equal(buildEndocrineOrganSystemModel({ loadState: LOAD_STATES.READY, schedulerState: { running: true }, failedCount: 0, backgroundRuntime: { is_advancing: true, core_ticks_missing: [] } }).status, ORGAN_STATUS.STABLE)
  assert.equal(buildMotorOrganSystemModel({ loadState: LOAD_STATES.NO_SAMPLE }).status, ORGAN_STATUS.PENDING)
  assert.equal(buildMotorOrganSystemModel({ loadState: LOAD_STATES.READY, totalInbound: 1, totalErrors: 0 }).status, ORGAN_STATUS.STABLE)
  assert.equal(buildToolsOrganSystemModel({ loadState: LOAD_STATES.NO_SAMPLE, healthStatus: 'OK' }).status, ORGAN_STATUS.PENDING)
  assert.equal(buildToolsOrganSystemModel({ loadState: LOAD_STATES.READY, healthStatus: 'OK' }).status, ORGAN_STATUS.STABLE)
})

test('器官核心格式化对空值、越界值和缺省字段有稳定语义', () => {
  assert.equal(clampPercent(null), '--')
  assert.equal(clampPercent(-5), '0%')
  assert.equal(clampPercent(105, 1), '100.0%')
  assert.equal(toFixed('bad'), '--')
  assert.equal(toFixed(1.234, 1), '1.2')
  assert.deepEqual(metric('m', '', null), { label: 'm', value: '--', hint: '—', tone: 'text-gray-900' })
  assert.equal(getOrganStatusMeta('missing').label, getOrganStatusMeta(ORGAN_STATUS.PENDING).label)

  const model = createOrganSystemModel({ id: 'empty', name: '空器官', symptoms: ['x', ''], downstreamImpacts: null })
  assert.equal(model.status, ORGAN_STATUS.PENDING)
  assert.equal(model.loadState, LOAD_STATES.UNAVAILABLE)
  assert.equal(model.summary, '等待器官系统样本接入。')
  assert.deepEqual(model.symptoms, ['x'])
  assert.deepEqual(model.downstreamImpacts, [])
  assert.equal(model.drilldownPath, '/')
})
