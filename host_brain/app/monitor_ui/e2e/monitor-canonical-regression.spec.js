import { expect, test } from '@playwright/test'
import { installOptionalOperatorSession } from './operator-session.js'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

async function fulfillJson(route, body, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function readBffPayload(route) {
  const request = route.request()
  const headers = request.headers()
  expect(headers['x-natsume-correlation-id']).toMatch(/^corr\./)
  expect(headers['x-natsume-idempotency-key']).toMatch(/^idem\./)
  expect(headers['x-natsume-nonce']).toMatch(/^nonce\./)
  expect(headers['x-natsume-signature']).toBeUndefined()
  expect(headers.authorization).toBeUndefined()
  const envelope = await request.postDataJSON()
  expect(Object.keys(envelope)).toEqual(['payload'])
  return envelope.payload
}

async function fulfillCommandReceipt(route, commandId, result = {}) {
  await fulfillJson(route, {
    command_id: commandId,
    allowed: true,
    executed: true,
    duplicate: false,
    reason_code: 'COMMAND_COMPLETED',
    status: 'completed',
    audit_ref: `audit://e2e/${commandId}`,
    claim_ref: `claim://e2e/${commandId}`,
    result_ref: `result://e2e/${commandId}`,
    certainty: 'certain',
    ...result,
  })
}

async function stubCommonReadApis(page) {
  await page.route('**/obs/runtime-snapshot**', (route) => fulfillJson(route, {
    timestamp: '2026-07-13T15:00:00Z',
    background_runtime: { status: 'healthy' },
  }))
  await page.route('**/obs/prompt-builder/stats**', (route) => fulfillJson(route, {
    total: 0,
    active_templates: 0,
  }))
  await page.route('**/obs/capability-gaps/stats**', (route) => fulfillJson(route, {
    detected_total: 0,
    active_runtime_capabilities: 0,
    hotspots: [],
    forge_candidates: [],
    forge_runtime_quality: null,
  }))
}

test.beforeEach(async ({ context, baseURL }) => {
  await installOptionalOperatorSession(context, baseURL)
})


async function stubOverviewApis(page) {
  // This fixture validates the HTTP projection deterministically. The live
  // production WebSocket is covered by the Core Release acceptance spec and
  // must not race this mocked snapshot with host-dependent service state.
  await page.addInitScript(() => {
    Object.defineProperty(window, 'WebSocket', {
      configurable: true,
      value: undefined,
    })
  })
  await stubCommonReadApis(page)
  await page.route('**/obs/services/health**', (route) => fulfillJson(route, {
    total: 3,
    up: 2,
    degraded: 1,
    down: 0,
    unknown: 0,
    services: [
      {
        service_id: 'edge-gateway',
        display_name: 'Edge Gateway',
        service_type: 'edge',
        status: 'UP',
        p95_ms: 42,
        success_rate: 99,
      },
      {
        service_id: 'llm-router',
        display_name: 'LLM Router',
        service_type: 'llm',
        status: 'DEGRADED',
        p95_ms: 380,
        success_rate: 82,
      },
    ],
  }))
  await page.route('**/obs/subsystems/stats**', (route) => fulfillJson(route, {
    subsystems: [
      {
        subsystem: 'trace.pipeline',
        display_name: 'Trace Pipeline',
        total: 12,
        success: 10,
        failed: 1,
        degraded: 1,
        success_rate: 83,
        last_ts: 1782260000000,
      },
    ],
  }))
  await page.route('**/obs/ui-assertion/stats**', (route) => fulfillJson(route, {
    total_assertions: 20,
    block_rate: 10,
    sensitive_blocked: 2,
    precondition_failed: 1,
    postcondition_failed: 1,
  }))
}

async function stubCockpitApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/obs/life-body**', (route) => fulfillJson(route, {
    timestamp: '2026-06-20T08:00:00Z',
    derived: {
      hasRuntimeSnapshot: true,
      hasMemoryData: true,
      hasMemorySamples: true,
      avgMemorySuccessRate: 91,
      sampledMemorySubsystemCount: 3,
      configuredMemorySubsystemCount: 4,
      currentEmotion: 'CALM',
      currentBondStage: 'FRIEND',
      replyPosture: 'steady',
      riskLevel: 'low',
      overviewUpdatedAt: '2026-06-20T08:00:00Z',
      runtimeUpdatedAt: '2026-06-20T08:00:00Z',
    },
    runtimeSnapshot: {
      timestamp: '2026-06-20T08:00:00Z',
      sys_mode: 'normal',
      background_runtime: { status: 'healthy' },
      emotion_context: { emotion_tag: 'CALM' },
      bond_state: { current_stage: 'FRIEND', trend: 'up' },
      runtime_summary: {
        headline: '情绪、记忆和执行层均有样本，当前需要先确认情绪层波动。',
        reply_posture: { style: 'steady' },
      },
      self_reflection_pulse: {
        narrative_guardrails: { risk_level: 'low' },
      },
    },
    memoryData: {
      subsystems: [
        { name: 'semantic', success_rate: 94 },
        { name: 'episodic', success_rate: 88 },
      ],
    },
    servicesData: {
      total: 2,
      up: 2,
      services: [],
    },
    model: {
      organSystems: [
        {
          id: 'emotion',
          name: '情绪层',
          status: 'warning',
          summary: '情绪样本存在短时振幅，需要先确认是否来自最近输入。',
          drilldownPath: '/life/emotion',
          drilldownLabel: '查看情绪',
        },
        {
          id: 'memory',
          name: '记忆层',
          status: 'stable',
          summary: '记忆召回保持稳定。',
          drilldownPath: '/life/memory',
          drilldownLabel: '查看记忆',
        },
      ],
      organStatusCounts: { warning: 1, stable: 1 },
      primaryOrgan: {
        id: 'emotion',
        name: '情绪层',
        status: 'warning',
        summary: '情绪样本存在短时振幅，需要先确认是否来自最近输入。',
        drilldownPath: '/life/emotion',
        drilldownLabel: '查看情绪',
      },
      pathologies: [
        {
          id: 'emotion-wave',
          organId: 'emotion',
          organLabel: '情绪层',
          title: '情绪振幅异常',
          severity: 'warning',
          symptom: '最近窗口出现连续情绪振幅升高。',
          impactLabel: '可能影响回复姿态和执行节律。',
          detailPath: '/life/pathologies/emotion-wave',
        },
      ],
      primaryPathology: {
        id: 'emotion-wave',
        organId: 'emotion',
        organLabel: '情绪层',
        title: '情绪振幅异常',
        severity: 'warning',
        symptom: '最近窗口出现连续情绪振幅升高。',
        impactLabel: '可能影响回复姿态和执行节律。',
        detailPath: '/life/pathologies/emotion-wave',
      },
      timelineEvents: [
        {
          id: 'evt-emotion-1',
          type: 'pathology',
          organId: 'emotion',
          organLabel: '情绪层',
          title: '情绪振幅升高',
          detail: '最近一次输入触发情绪振幅上升。',
          severity: 'warning',
          to: '/life/timeline?organ=emotion&type=pathology',
        },
        {
          id: 'evt-memory-1',
          type: 'memory',
          organId: 'memory',
          organLabel: '记忆层',
          title: '记忆召回成功',
          detail: '记忆召回命中率保持稳定。',
          severity: 'info',
          to: '/life/timeline?organ=memory',
        },
      ],
      diagnosticGuidance: {
        summary: '当前最该先处理的是情绪层的病灶，然后回到生命事件链确认影响范围。',
        actions: [
          {
            id: 'pathology',
            tone: 'warning',
            title: '情绪层 · 情绪振幅异常',
            detail: '最近窗口出现连续情绪振幅升高。',
            to: '/life/pathologies/emotion-wave',
            label: '进入病灶详情',
          },
          {
            id: 'organ',
            tone: 'warning',
            title: '情绪层',
            detail: '情绪样本存在短时振幅，需要先确认是否来自最近输入。',
            to: '/life/emotion',
            label: '查看情绪',
          },
        ],
      },
    },
  }))
  await page.route('**/obs/digital-life/wall-clock-soak**', (route) => fulfillJson(route, {
    status: 'PASS',
    verdict: 'PASS',
    updated_at: '2026-06-20T08:00:00Z',
  }))
  await page.route('**/obs/avatar/state**', (route) => fulfillJson(route, {
    ok: true,
    behavior_id: 'idle',
    updated_at: '2026-06-20T08:00:00Z',
  }))
}

async function stubLifeTimelineApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/obs/life-timeline**', (route) => fulfillJson(route, {
    timestamp: '2026-06-20T08:00:00Z',
    primaryOrgan: {
      id: 'emotion',
      name: '情绪层',
    },
    primaryPathology: {
      id: 'emotion-wave',
      organId: 'emotion',
      organLabel: '情绪层',
      title: '情绪振幅异常',
      impactLabel: '可能影响回复姿态和执行节律。',
      detailPath: '/life/pathologies/emotion-wave',
    },
    items: [
      {
        id: 'evt-emotion-1',
        type: 'pathology',
        typeLabel: '病灶',
        organId: 'emotion',
        organLabel: '情绪层',
        title: '情绪振幅升高',
        detail: '最近一次输入触发情绪振幅上升。',
        severity: 'warning',
        timestamp: '2026-06-20T08:00:00Z',
        to: '/life/pathologies/emotion-wave',
        linkLabel: '进入病灶详情',
      },
      {
        id: 'evt-memory-1',
        type: 'memory',
        typeLabel: '记忆',
        organId: 'memory',
        organLabel: '记忆层',
        title: '记忆召回成功',
        detail: '语义记忆召回命中率保持稳定。',
        severity: 'info',
        timestamp: '2026-06-20T07:58:00Z',
        to: '/life/memory',
        linkLabel: '查看记忆',
      },
    ],
  }))
}

async function stubPathologyCenterApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/obs/pathologies**', (route) => fulfillJson(route, {
    timestamp: '2026-06-20T08:00:00Z',
    organSystems: [
      { id: 'emotion', name: '情绪层' },
      { id: 'memory', name: '记忆层' },
    ],
    primaryPathology: {
      id: 'emotion-wave',
      organId: 'emotion',
      organLabel: '情绪层',
      title: '情绪振幅异常',
      severity: 'critical',
      symptom: '最近窗口出现连续情绪振幅升高。',
      durationLabel: '最近 6h',
      impactLabel: '可能影响回复姿态和执行节律。',
      source: 'runtime alerts',
      detailPath: '/life/pathologies/emotion-wave',
      recommendedPath: '/life/emotion',
      recommendedLabel: '查看情绪',
    },
    items: [
      {
        id: 'emotion-wave',
        organId: 'emotion',
        organLabel: '情绪层',
        title: '情绪振幅异常',
        severity: 'critical',
        symptom: '最近窗口出现连续情绪振幅升高。',
        durationLabel: '最近 6h',
        impactLabel: '可能影响回复姿态和执行节律。',
        source: 'runtime alerts',
        detailPath: '/life/pathologies/emotion-wave',
        recommendedPath: '/life/emotion',
        recommendedLabel: '查看情绪',
      },
      {
        id: 'memory-drift',
        organId: 'memory',
        organLabel: '记忆层',
        title: '记忆召回漂移',
        severity: 'warning',
        symptom: '部分记忆召回样本置信度下降。',
        durationLabel: '最近 24h',
        impactLabel: '可能影响上下文连续性。',
        source: 'memory health',
        detailPath: '/life/pathologies/memory-drift',
        recommendedPath: '/life/memory',
        recommendedLabel: '查看记忆',
      },
    ],
  }))
}

async function stubSecurityImmuneApis(page) {
  await stubCommonReadApis(page)
  const riskOrder = { low: 1, medium: 2, high: 3, critical: 4 }
  const allIncidents = [
    {
      incident_id: 'incident-high-powershell',
      title: 'Suspicious PowerShell execution',
      risk: 'high',
      detail: 'Encoded PowerShell launched from endpoint-1.',
      asset_id: 'endpoint-1',
      source: 'edr.rule.powershell.encoded',
      evidence_refs: ['evidence://security/powershell-encoded'],
      audit_ref: 'ledger://security/incident-high-powershell',
      recommendation: 'Isolate endpoint-1 and confirm the encoded command lineage.',
    },
    {
      incident_id: 'incident-low-smb',
      title: 'Legacy SMB service exposed',
      risk: 'low',
      detail: 'SMBv1-compatible service observed on lab subnet.',
      asset_id: 'edge-node-1',
      source: 'network.scan.smb',
    },
  ]
  await page.route('**/obs/security/runtime', (route) => fulfillJson(route, {
    asset_count: 2,
    incident_count: 2,
    risk_counts: { low: 1, high: 1 },
    generated_ts_ms: 1782260000000,
  }))
  await page.route('**/obs/security/assets', (route) => fulfillJson(route, {
    asset_count: 2,
    assets: [
      { asset_id: 'endpoint-1', hostname: 'endpoint-1', os_family: 'Windows' },
      { asset_id: 'edge-node-1', hostname: 'edge-node-1', os_family: 'Linux' },
    ],
    generated_ts_ms: 1782260000000,
  }))
  await page.route('**/obs/security/incidents**', (route) => {
    const minRisk = new URL(route.request().url()).searchParams.get('min_risk') || 'low'
    const threshold = riskOrder[minRisk] || riskOrder.low
    const incidents = allIncidents.filter((incident) => (riskOrder[incident.risk] || 0) >= threshold)
    return fulfillJson(route, {
      incident_count: incidents.length,
      incidents,
      risk_counts: { low: 1, high: 1 },
      generated_ts_ms: 1782260000000,
    })
  })
  await page.route('**/v1/host/operator-command-bff/security.hunt.run_once', async (route) => {
    const payload = await readBffPayload(route)
    await fulfillCommandReceipt(route, 'security.hunt.run_once', {
      reason: payload.reason,
      asset_count: 2,
      incident_count: 2,
    })
  })
}

async function stubPromptConfigApis(page) {
  await stubCommonReadApis(page)
  const templates = [
    {
      template_id: 'react_loop.prompt_templates',
      description: 'Governed planner prompts',
      purpose: 'Plan tool use and response strategy.',
      scope: 'agent',
      category: 'planner',
      source: 'prompt_spec',
      content_type: 'json',
      locale: '*',
      enabled: true,
      has_override: false,
      version: 3,
      updated_at_ts_ms: 1782260000000,
      default_content: { system: 'Plan carefully.', user: 'Use available tools.' },
      resolved_content: { system: 'Plan carefully.', user: 'Use available tools.' },
      override_content: null,
    },
    {
      template_id: 'safety.guard.fallback',
      description: 'Safety guard fallback response',
      purpose: 'Provide a fallback when guardrails block execution.',
      scope: 'safety',
      category: 'guard_fallback',
      source: 'override',
      content_type: 'text',
      locale: '*',
      enabled: true,
      has_override: true,
      version: 7,
      updated_at_ts_ms: 1782260001000,
      default_content: 'Default guard fallback.',
      resolved_content: 'Runtime override guard fallback.',
      override_content: 'Runtime override guard fallback.',
    },
    {
      template_id: 'persona.response.style',
      description: 'Persona response style prompt',
      purpose: 'Control persona style.',
      scope: 'persona',
      category: 'style',
      source: 'prompt_spec',
      content_type: 'text',
      locale: '*',
      enabled: true,
      has_override: false,
      version: 2,
      updated_at_ts_ms: 1782260002000,
      default_content: 'Default persona style.',
      resolved_content: 'Default persona style.',
      override_content: null,
    },
  ]

  await page.route('**/prompt-config/templates**', (route) => {
    const url = new URL(route.request().url())
    const match = url.pathname.match(/\/prompt-config\/templates\/(.+)$/)
    if (match) {
      const templateId = decodeURIComponent(match[1])
      const item = templates.find((template) => template.template_id === templateId)
      return fulfillJson(route, { item: item || templates[0] })
    }

    const keyword = url.searchParams.get('keyword')?.toLowerCase() || ''
    const scope = url.searchParams.get('scope')?.toLowerCase() || ''
    const category = url.searchParams.get('category')?.toLowerCase() || ''
    const source = url.searchParams.get('source')?.toLowerCase() || ''
    const items = templates
      .filter((template) => !keyword || `${template.template_id} ${template.description} ${template.purpose}`.toLowerCase().includes(keyword))
      .filter((template) => !scope || template.scope.toLowerCase().includes(scope))
      .filter((template) => !category || template.category.toLowerCase().includes(category))
      .filter((template) => !source || template.source.toLowerCase().includes(source))
      .map(({ default_content, resolved_content, override_content, ...template }) => template)
    return fulfillJson(route, { items })
  })
}

async function stubAutomationApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/scheduler/tasks**', (route) => fulfillJson(route, [
    {
      task: {
        task_id: 'memory.distill',
        description: 'Memory distillation smoke task',
        enabled: true,
        priority: 'HIGH',
        trigger_mode: 'interval',
        resource_profile: {
          resource_class: 'memory_heavy',
          uses_llm: true,
        },
      },
      health: {
        status: 'RUNNING',
        run_count: 4,
        error_count: 0,
      },
      last_gate_decision: {
        reason_code: 'ALLOW_MEMORY',
        gate_state: 'allow',
      },
      last_endocrine_cortisol: 12,
      last_endocrine_energy: 88,
    },
    {
      task: {
        task_id: 'browser.collect',
        description: 'Browser collection smoke task',
        enabled: false,
        priority: 'LOW',
        trigger_mode: 'manual',
        resource_profile: {
          resource_class: 'browser_heavy',
          uses_gpu: false,
        },
      },
      health: {
        status: 'IDLE',
        run_count: 1,
        error_count: 0,
      },
      last_gate_decision: {
        reason_code: 'DEFER_BROWSER',
        gate_state: 'defer',
      },
    },
  ]))
  await page.route('**/scheduler/health**', (route) => fulfillJson(route, {
    running: true,
    scheduler_status: 'HEALTHY',
  }))
  await page.route('**/obs/resource-manager/status**', (route) => fulfillJson(route, {
    status: 'OK',
    running: true,
    source: 'smoke-fixture',
    last_gate_decisions: [],
    last_snapshot: { pressure: { status: 'NORMAL' } },
  }))
}

async function stubInsightsApis(page) {
  await stubCommonReadApis(page)
  const empty = {}
  for (const pattern of [
    '**/obs/errors**',
    '**/obs/subsystems/stats**',
    '**/obs/vts/stats**',
    '**/obs/llm/call-stats**',
    '**/obs/insights/summary-snapshot**',
  ]) {
    await page.route(pattern, (route) => fulfillJson(route, empty))
  }
}

async function stubTraceApis(page) {
  await stubCommonReadApis(page)
  const requestId = 'req_trace_smoke'
  const traceSnapshot = {
    request_id: requestId,
    status: 'DEGRADED',
    total_latency_ms: 128,
    timestamp: '2026-06-20T08:00:00Z',
    summary: {
      session_id: 'sess_trace_smoke',
      conversation_id: 'conv_trace_smoke',
      request_id: requestId,
      decision: 'review',
      task_type: 'trace_smoke',
    },
    nodes: [
      {
        node_id: 'input',
        id: 'input',
        name: 'Input capture',
        stage: 'input',
        status: 'OK',
        latency_ms: 12,
        output: 'User request normalized',
        ts_ms: 1781942400000,
      },
      {
        node_id: 'planner',
        id: 'planner',
        name: 'Planner guard',
        stage: 'plan',
        status: 'DEGRADED',
        latency_ms: 48,
        output: 'Route selected with fallback policy',
        ts_ms: 1781942400100,
      },
    ],
  }
  await page.route('**/obs/trace-feed/snapshot**', (route) => fulfillJson(route, {
    items: [
      {
        session: {
          request_id: requestId,
          session_id: 'sess_trace_smoke',
          conversation_id: 'conv_trace_smoke',
          status: 'OK',
          last_ts: '2026-06-20T08:00:00Z',
        },
        trace: traceSnapshot,
      },
    ],
    last_ts: '2026-06-20T08:00:00Z',
  }))
  await page.route('**/obs/trace-feed/live**', (route) => fulfillJson(route, {
    items: [
      {
        session: {
          request_id: requestId,
          session_id: 'sess_trace_smoke',
          conversation_id: 'conv_trace_smoke',
          status: 'OK',
          last_ts: '2026-06-20T08:00:00Z',
        },
        trace: traceSnapshot,
      },
    ],
    last_ts: '2026-06-20T08:00:00Z',
  }))
  await page.route('**/obs/trace/**/stream_events', (route) => fulfillJson(route, {
    events: [
      {
        event_id: 'stream-1',
        type: 'node.degraded',
        node_id: 'planner',
        message: 'Planner guard used fallback policy',
        ts_ms: 1781942400100,
      },
    ],
  }))
  await page.route('**/obs/trace/req_trace_smoke', (route) => fulfillJson(route, traceSnapshot))
  await page.route('**/obs/trace-snapshot/**', (route) => fulfillJson(route, traceSnapshot))
  await page.route('**/obs/retrieval/runtime**', (route) => fulfillJson(route, {
    suite_profile: 'smoke',
    read_write_model_aligned: false,
    recent_embedding_fallbacks: 1,
    latest: {
      embedding_model_id: 'text-embedding-smoke',
      effective_embedding_provider: 'fixture',
      fallback_embedding_used: true,
      fallback_embedding_reason_code: 'fixture_fallback',
      ts_ms: 1781942400200,
    },
  }))
  await page.route('**/obs/immune-gate/replay/**', (route) => fulfillJson(route, {
    request_id: requestId,
    events: [
      {
        event_id: 'immune-1',
        decision: 'review',
        risk_level: 'medium',
        reason_code: 'planner_fallback',
        ts_ms: 1781942400100,
      },
    ],
  }))
  await page.route('**/obs/sessions**', (route) => fulfillJson(route, {
    items: [
      {
        request_id: requestId,
        session_id: 'sess_trace_smoke',
        conversation_id: 'conv_trace_smoke',
        status: 'OK',
        last_ts: '2026-06-20T08:00:00Z',
        message: 'Trace smoke request',
      },
    ],
    total: 1,
  }))
  await page.route('**/obs/tribe/events**', (route) => fulfillJson(route, { items: [] }))
}

async function stubSessionsApis(page) {
  await stubCommonReadApis(page)
  const sessions = [
    {
      request_id: 'req_session_error',
      session_id: 'sess_error',
      conversation_id: 'conv_error',
      status: 'error',
      input_preview: 'Planner failed while resolving tool evidence',
      reason_code: 'planner_timeout',
      total_latency_ms: 1880,
      ts_ms: 1781942410000,
      last_ts: '2026-06-20T08:00:10Z',
    },
    {
      request_id: 'req_session_rejected',
      session_id: 'sess_rejected',
      conversation_id: 'conv_rejected',
      status: 'rejected',
      input_preview: 'Request rejected by policy gate',
      reason_code: 'policy_gate',
      total_latency_ms: 420,
      ts_ms: 1781942405000,
      last_ts: '2026-06-20T08:00:05Z',
    },
    {
      request_id: 'req_session_ok',
      session_id: 'sess_ok',
      conversation_id: 'conv_ok',
      status: 'ok',
      input_preview: 'Healthy memory lookup and response',
      total_latency_ms: 220,
      ts_ms: 1781942400000,
      last_ts: '2026-06-20T08:00:00Z',
    },
  ]
  await page.route('**/obs/sessions**', (route) => {
    const url = new URL(route.request().url())
    const status = url.searchParams.get('status')
    const keyword = (url.searchParams.get('keyword') || '').toLowerCase()
    const items = sessions.filter((item) => {
      const statusMatch = !status || item.status === status
      const keywordMatch = !keyword
        || item.request_id.toLowerCase().includes(keyword)
        || item.session_id.toLowerCase().includes(keyword)
        || item.input_preview.toLowerCase().includes(keyword)
      return statusMatch && keywordMatch
    })
    return fulfillJson(route, {
      items,
      total: items.length,
      page: 1,
    })
  })
}


async function stubTribeApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/internal/tribe_state/summary', (route) => fulfillJson(route, {
    status: 'healthy',
    total_agents: 3,
    agents: ['agent.alpha', 'agent.planner', 'tool.runner'],
  }))
  await page.route('**/internal/tribe_event/recent', (route) => fulfillJson(route, {
    items: [
      {
        event_id: 'evt-plan-1',
        event_type: 'agent.plan.created',
        source_agent: 'agent.alpha',
        target_agent: 'agent.planner',
        topic: 'planning',
        ts_ms: 1782260000000,
      },
      {
        event_id: 'evt-tool-1',
        event_type: 'agent.tool.result',
        source_agent: 'tool.runner',
        target_agent: 'agent.alpha',
        topic: 'tools',
        ts_ms: 1782260001000,
      },
    ],
  }))
  await page.route('**/internal/tribe_event/subscribers', (route) => fulfillJson(route, {
    total_handlers: 2,
    handlers: [
      { handler: 'planner.handler', event_type: 'agent.plan.created' },
      { handler: 'tool.handler', event_type: 'agent.tool.result' },
    ],
  }))
}

async function stubGatewayApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/obs/gateway/stats**', (route) => fulfillJson(route, {
    total_inbound: 12,
    total_outbound: 8,
    total_errors: 2,
    humanize_enabled: true,
    humanize_typing_speed_cps: 8,
    platforms: [
      {
        platform: 'wechat',
        status: 'UP',
        inbound_count: 8,
        outbound_count: 5,
        error_count: 0,
        avg_latency_ms: 42,
        bridge_status: 'connected',
        last_event_ts_ms: 1782260000000,
      },
      {
        platform: 'telegram',
        status: 'DEGRADED',
        inbound_count: 3,
        outbound_count: 2,
        error_count: 2,
        avg_latency_ms: 180,
        status_detail: 'Webhook retrying',
        bridge_status: 'retrying',
        last_event_ts_ms: 1782260001000,
      },
      {
        platform: 'qq',
        status: 'DOWN',
        inbound_count: 1,
        outbound_count: 1,
        error_count: 0,
        status_detail: 'adapter offline',
      },
    ],
  }))
  await page.route('**/obs/wechat/stats**', (route) => fulfillJson(route, {
    rate_limits: [
      {
        key: 'send_text',
        current: 10,
        limit: 10,
        remaining: 0,
      },
    ],
    recent_events: [
      {
        ts_ms: 1782260000000,
        event_type: 'wechat.message.received',
      },
    ],
  }))
}

async function stubBodyIntegrationApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/v1/mateengine/capabilities', (route) => fulfillJson(route, {
    actions: ['state', 'pose', 'say'],
    events: ['body.state.changed', 'body.say.completed'],
    presets: ['idle', 'wave'],
  }))
  await page.route('**/v1/mateengine/health', (route) => fulfillJson(route, {
    ok: true,
    status: 'healthy',
    host: '127.0.0.1',
    port: 18770,
  }))
  await page.route('**/v1/host/operator-command-bff/mate.action.submit', async (route) => {
    const payload = await readBffPayload(route)
    await fulfillCommandReceipt(route, 'mate.action.submit', {
      request_id: 'mate-smoke-action',
      echo: payload,
    })
  })
  await page.route('**/v1/host/operator-command-bff/mate.say.submit', async (route) => {
    const payload = await readBffPayload(route)
    await fulfillCommandReceipt(route, 'mate.say.submit', {
      request_id: 'mate-smoke-say',
      echo: payload,
    })
  })
}

async function stubBodyIntegrationDegradedApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/v1/mateengine/capabilities', (route) => fulfillJson(route, {
    actions: ['state', 'say'],
    events: ['body.state.changed'],
    presets: ['idle'],
  }))
  await page.route('**/v1/mateengine/health', (route) => fulfillJson(route, {
    ok: false,
    status: 'degraded',
    host: '127.0.0.1',
    port: 18770,
  }))
}

function taskRuntimeReview(reviewId, status, { expired = false } = {}) {
  return {
    review_id: reviewId,
    review_type: 'PRE_EXECUTION_APPROVAL',
    subject_ref: `task-node://graph-smoke/${reviewId}`,
    subject_version: 4,
    requester: 'runtime',
    reason: `Governed review ${reviewId}`,
    risk_level: 4,
    status,
    persisted_status: status,
    status_version: 1,
    expires_at: expired ? 1 : 4_102_444_800_000,
    expired,
    resume_phase: 'PRE_EXECUTION',
    checkpoint_id: `checkpoint://graph-smoke/${reviewId}/4`,
    evidence_refs: [`evidence://graph-smoke/${reviewId}`],
    actions: {
      approve: status === 'PENDING' && !expired,
      reject: status === 'PENDING' && !expired,
      resume: status === 'APPROVED' && !expired,
      resume_requires_token: true,
      resume_binding_fields: [
        'resume_token_ref',
        'subject_ref',
        'subject_version',
        'resume_phase',
        'checkpoint_id',
      ],
    },
  }
}

async function stubTaskRuntimeApis(page) {
  await stubCommonReadApis(page)
  const state = {
    statuses: {
      'review-pending': 'PENDING',
      'review-reject': 'PENDING',
      'review-approved': 'APPROVED',
      'review-expired': 'EXPIRED',
    },
    approveCalls: [],
    rejectCalls: [],
    resumeCalls: [],
  }
  await page.route('**/obs/task-runtime/executions**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/obs/task-runtime/executions') {
      return fulfillJson(route, {
        schema_version: 'Natsume.operator.execution_index.v1',
        generated_at_ms: 1_782_260_000_000,
        read_only: true,
        executions: [{
          graph_id: 'graph-smoke',
          revision: 7,
          root_work_order_id: 'work-smoke',
          status: 'COMPLETED',
          terminal: true,
          node_count: 3,
          updated_ts_ms: 1_782_260_000_000,
        }],
      })
    }
    const reviews = Object.entries(state.statuses).map(([reviewId, status]) => (
      taskRuntimeReview(reviewId, status, { expired: reviewId === 'review-expired' })
    ))
    return fulfillJson(route, {
      schema_version: 'Natsume.operator.execution_review.v1',
      generated_at_ms: 1_782_260_000_000,
      read_only: true,
      mutation_contract: {
        direct_database_write: false,
        review_commands: ['approve', 'reject', 'resume'],
        resume_requires_bound_token: true,
        automatic_resume: false,
      },
      execution: {
        graph_id: 'graph-smoke',
        revision: 7,
        root_work_order_id: 'work-smoke',
        planner_ref: 'planner://smoke/7',
        status: 'COMPLETED',
        terminal: true,
      },
      dag: {
        nodes: [
          {
            node_id: 'plan',
            dependencies: [],
            route: 'worker.plan',
            risk_level: 1,
            status: 'ACCEPTED',
            attempts: [{ attempt_id: 'attempt-plan', attempt_no: 1, status: 'SUCCEEDED' }],
            verification: { status: 'PASS', evidence_refs: ['evidence://plan/verify'] },
          },
          {
            node_id: 'execute',
            dependencies: ['plan'],
            route: 'worker.execute',
            risk_level: 4,
            status: 'ACCEPTED',
            attempts: [{ attempt_id: 'attempt-execute', attempt_no: 2, status: 'SUCCEEDED' }],
            verification: { status: 'PASS', evidence_refs: ['evidence://execute/verify'] },
          },
          {
            node_id: 'publish',
            dependencies: ['execute'],
            route: 'worker.publish',
            risk_level: 2,
            status: 'ACCEPTED',
            attempts: [{ attempt_id: 'attempt-publish', attempt_no: 1, status: 'SUCCEEDED' }],
            verification: { status: 'PASS', evidence_refs: ['evidence://publish/verify'] },
          },
        ],
        edges: [{ from: 'plan', to: 'execute' }, { from: 'execute', to: 'publish' }],
      },
      attempts: [
        { attempt_id: 'attempt-plan', node_id: 'plan', attempt_no: 1, status: 'SUCCEEDED' },
        { attempt_id: 'attempt-execute', node_id: 'execute', attempt_no: 2, status: 'SUCCEEDED' },
        { attempt_id: 'attempt-publish', node_id: 'publish', attempt_no: 1, status: 'SUCCEEDED' },
      ],
      budgets: [{
        budget_id: 'budget-smoke',
        scope: 'graph-smoke',
        node_id: 'execute',
        status: 'CONSUMED',
        consumed: { token_count: 420, tool_call_count: 3, time_ms: 900 },
        limits: { token_count: 1000, tool_call_count: 10, time_ms: 5000 },
        exceeded_dimensions: [],
      }],
      verification: [
        { node_id: 'plan', status: 'PASS', source: 'runtime_event', evidence_refs: ['evidence://plan/verify'] },
        { node_id: 'execute', status: 'PASS', source: 'runtime_event', evidence_refs: ['evidence://execute/verify'] },
        { node_id: 'publish', status: 'PASS', source: 'runtime_event', evidence_refs: ['evidence://publish/verify'] },
      ],
      reviews,
      timeline: [
        { cursor: 1, event_id: 'event-plan', event_type: 'TASK_GRAPH_CREATED', summary: 'Plan created', payload_ref: 'taskstore://event/1', evidence_refs: [], dedupe_key: 'd1', ts_ms: 1_782_260_000_001 },
        { cursor: 7, event_id: 'event-complete', event_type: 'TASK_GRAPH_COMPLETED', summary: 'Execution terminal', payload_ref: 'taskstore://event/7', evidence_refs: ['evidence://complete'], dedupe_key: 'd7', ts_ms: 1_782_260_000_007 },
      ],
      cursor_page: { after: 0, next_cursor: 7, event_count: 2, has_more: false, dedupe_field: 'dedupe_key' },
    })
  })
  return state
}


test.describe('Canonical monitor regression coverage', () => {

  test('Observability overview exposes triage rail and Trace route', async ({ page, baseURL }) => {
    await stubOverviewApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/overview'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '排障入口' })).toBeVisible()
    await expect(page.getByText('观测 Gate')).toBeVisible()
    await expect(page.getByText('先看告警与服务状态')).toBeVisible()
    const handoff = page.getByRole('region', { name: 'Observability Evidence Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('Observability Evidence Handoff')).toBeVisible()
    await expect(handoff.getByText('先确认 LLM Router')).toBeVisible()
    await expect(handoff.getByText('3 services · 1 degraded · 0 down')).toBeVisible()
    await expect(handoff.getByText('Trace Pipeline 是首个活跃子系统。')).toBeVisible()
    await expect(handoff.getByRole('link', { name: /进入告警中心/ })).toHaveAttribute('href', '#/observability/alerts')
    await expect(handoff.getByRole('link', { name: /Trace/ })).toHaveAttribute('href', '#/observability/trace')

    await page.locator('a[href="#/observability/trace"]').filter({ hasText: '按 request / node / stage 追调用链' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace')
  })

  test('Cockpit command rail exposes Life Systems quick routes', async ({ page, baseURL }) => {
    await stubCockpitApis(page)
    await page.goto(monitorUrl(baseURL, '/cockpit'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '现在先处理什么' })).toBeVisible()
    const mission = page.getByRole('region', { name: 'Cockpit Mission Control' })
    await expect(mission).toBeVisible()
    await expect(mission.getByText('Mission brief')).toBeVisible()
    await expect(mission.getByText('先处理 情绪层 · 情绪振幅异常')).toBeVisible()
    await expect(mission.getByText('观测可信度')).toBeVisible()
    await expect(mission.getByText('首要入口')).toBeVisible()
    await expect(mission.getByRole('link', { name: '进入主病灶', exact: true })).toHaveAttribute('href', '#/life/pathologies/emotion-wave')
    const readout = page.getByRole('region', { name: 'Cockpit Decision Readout' })
    await expect(readout).toBeVisible()
    await expect(readout.getByText('Next action')).toBeVisible()
    await expect(readout.getByText('Why this first')).toBeVisible()
    await expect(readout.getByText('Data confidence')).toBeVisible()
    await expect(readout.getByRole('link', { name: /查看证据/ })).toHaveAttribute('href', '#/life/pathologies/emotion-wave')

    const lens = page.getByRole('region', { name: 'Cockpit Focus Lens' })
    await expect(lens).toBeVisible()
    await expect(lens.getByText('当前聚焦 情绪层')).toBeVisible()
    await expect(page.getByText('记忆召回成功')).toBeHidden()
    await lens.getByRole('button', { name: /事件/ }).click()
    await expect(lens.getByText('按最近事件回看生命链')).toBeVisible()
    await expect(page.getByText('记忆召回成功')).toBeVisible()
    await lens.getByRole('button', { name: /全景/ }).click()
    await expect(lens.getByText('全景查看所有生命体样本')).toBeVisible()
    await expect(page.getByText('记忆层')).toBeVisible()

    await mission.locator('a[href="#/life/pathologies"]').filter({ hasText: '只看已经形成的异常模式' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/pathologies')
  })

  test('Cockpit keeps a healthy direct Runtime visible while Life Body rebuilds', async ({ page, baseURL }) => {
    await stubCockpitApis(page)
    await page.unroute('**/obs/life-body**')
    await page.route('**/obs/life-body**', (route) => fulfillJson(route, {
      window_minutes: 360,
      timestamp: '2026-08-03T14:00:00Z',
      meta: {
        projection: {
          available: false,
          degraded_state: 'SOURCE_UNAVAILABLE',
          reason_code: 'OBS_LIFE_BODY_ROOT_STALE',
        },
      },
    }))
    await page.unroute('**/obs/runtime-snapshot**')
    let runtimeReads = 0
    await page.route('**/obs/runtime-snapshot**', (route) => {
      runtimeReads += 1
      return fulfillJson(route, {
        timestamp: 1_785_766_400_000,
        sys_mode: 'normal',
        background_runtime: { status: 'healthy' },
        emotion_context: { emotion_tag: 'CALM' },
        slot_status: { hard_state: 'live' },
        runtime_summary: {
          headline: 'canonical runtime is live',
          reply_posture: { style: 'steady' },
        },
      })
    })

    await page.goto(monitorUrl(baseURL, '/cockpit'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByText('canonical runtime is live', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: /待连接/ })).toHaveCount(0)
    await expect(page.getByText('Runtime 待恢复', { exact: true })).toHaveCount(0)
    await expect.poll(() => runtimeReads).toBeGreaterThan(0)
  })

  test('Life timeline command rail follows filters and clears scope', async ({ page, baseURL }) => {
    await stubLifeTimelineApis(page)
    await page.goto(monitorUrl(baseURL, '/life/timeline'), { waitUntil: 'domcontentloaded' })
    const rail = page.getByRole('region', { name: 'Life Timeline Command Rail' })
    await expect(rail).toBeVisible()
    await expect(rail.getByRole('heading', { name: '下一步追哪条链' })).toBeVisible()
    await expect(rail.getByText('先追 情绪振幅升高')).toBeVisible()
    await expect(rail.getByText('可见事件')).toBeVisible()

    await page.getByRole('button', { name: /记忆 · 1/ }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/timeline?type=memory')
    await expect(rail.getByText('先追 记忆召回成功')).toBeVisible()

    await rail.getByRole('link', { name: '清除过滤' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/timeline')
  })

  test('Pathology center command rail exposes primary pathology and filter recovery', async ({ page, baseURL }) => {
    await stubPathologyCenterApis(page)
    await page.goto(monitorUrl(baseURL, '/life/pathologies'), { waitUntil: 'domcontentloaded' })
    const rail = page.getByRole('region', { name: 'Pathology Command Rail' })
    await expect(rail).toBeVisible()
    await expect(rail.getByRole('heading', { name: '先处理哪个病灶' })).toBeVisible()
    await expect(rail.getByText('先处理 情绪层 · 情绪振幅异常')).toBeVisible()
    await expect(rail.getByRole('link', { name: /进入主病灶/ })).toHaveAttribute('href', '#/life/pathologies/emotion-wave')

    await page.getByRole('button', { name: /高风险 · 1/ }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/pathologies?severity=critical')
    await expect(rail.getByText('可见病灶')).toBeVisible()

    await rail.getByRole('link', { name: '清除过滤' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/pathologies')
  })


  test('Security Immune gate filters incidents and keeps retired hunt read-only', async ({ page, baseURL }) => {
    await stubSecurityImmuneApis(page)
    await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '安全免疫', exact: true })).toBeVisible()

    const heroStatus = page.getByRole('region', { name: 'Security Immune hero status' })
    await expect(heroStatus).toBeVisible()
    await expect(heroStatus.getByText('存在 High incident')).toBeVisible()
    await expect(heroStatus.getByText('connected')).toBeVisible()
    await expect(heroStatus.getByText('2 assets')).toBeVisible()
    await expect(heroStatus.getByText('2 open')).toBeVisible()
    await expect(heroStatus.getByText('1 个 high+ 需要优先处理')).toBeVisible()

    const gate = page.getByRole('region', { name: 'Security Immune Gate' })
    await expect(gate).toBeVisible()
    await expect(gate.getByText('存在 High incident')).toBeVisible()
    await expect(gate.getByText('2 assets')).toBeVisible()
    await expect(gate.getByText('2 open')).toBeVisible()
    const triage = page.getByRole('region', { name: 'Security Immune Triage' })
    await expect(triage).toBeVisible()
    await expect(triage.getByText('先处理 Suspicious PowerShell execution')).toBeVisible()
    await expect(triage.getByText('1 evidence refs')).toBeVisible()
    const incidentsSection = page.locator('#security-immune-incidents')
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()

    const evidenceHandoff = page.getByRole('region', { name: 'Security Immune Evidence Handoff' })
    await expect(evidenceHandoff).toBeVisible()
    await expect(evidenceHandoff.getByText('Security Evidence Handoff')).toBeVisible()
    await expect(evidenceHandoff.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(evidenceHandoff.getByText('evidence://security/powershell-encoded')).toBeVisible()
    await expect(evidenceHandoff.getByText('ledger linked')).toBeVisible()
    await expect(evidenceHandoff.getByText('隔离/确认')).toBeVisible()
    await evidenceHandoff.getByRole('button', { name: '只看缺证据' }).click()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeHidden()
    await evidenceHandoff.getByRole('button', { name: '只看 High+' }).click()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeHidden()
    await page.getByRole('button', { name: '全部' }).click()

    await page.getByLabel('搜索 Security Incidents').fill('smb')
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeHidden()
    await page.getByLabel('清除 Security Incident 搜索').click()
    await page.getByRole('button', { name: '缺证据', exact: true }).click()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeHidden()
    await page.getByRole('button', { name: '全部', exact: true }).click()

    await page.getByLabel('Security Immune minimum risk').selectOption('high')
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeHidden()

    await gate.getByRole('button', { name: /Incidents/ }).click()
    await expect(page.locator('#security-immune-incidents')).toBeVisible()

    await expect(gate.getByRole('button', { name: 'Hunt 已迁移' })).toBeDisabled()
    await expect(page.getByText('Manual Hunt 写入口已迁移')).toBeVisible()
  })

  test('Prompt config decision rail filters templates and surfaces overrides', async ({ page, baseURL }) => {
    await stubPromptConfigApis(page)
    await page.goto(monitorUrl(baseURL, '/operations/prompt'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '提示与兜底模板总览' })).toBeVisible()

    const rail = page.getByRole('region', { name: 'PromptSpec Decision Rail' })
    await expect(rail).toBeVisible()
    await expect(rail.getByText('目录可读，存在覆盖模板')).toBeVisible()
    await expect(rail.getByText('3 templates')).toBeVisible()
    await expect(rail.getByText('code-side')).toBeVisible()

    const catalog = page.locator('#prompt-template-catalog')
    const detail = page.locator('#prompt-template-detail')
    await expect(catalog.getByRole('button').filter({ hasText: 'react_loop.prompt_templates' })).toBeVisible()
    await expect(catalog.getByRole('button').filter({ hasText: 'persona.response.style' })).toBeVisible()
    await page.getByLabel('Prompt category filter').selectOption('guard_fallback')
    await expect(rail.getByText('当前模板存在 runtime override')).toBeVisible()
    await expect(catalog.getByRole('button').filter({ hasText: 'safety.guard.fallback' })).toBeVisible()
    await expect(catalog.getByRole('button').filter({ hasText: 'react_loop.prompt_templates' })).toBeHidden()
    await expect(detail.getByText('当前存在运行时覆盖')).toBeVisible()
    await expect(detail.getByText('Runtime override guard fallback.').first()).toBeVisible()

    await rail.getByRole('button', { name: /定位 Detail/ }).click()
    await expect(detail).toBeVisible()
    await expect(rail.getByRole('link', { name: /人格运营/ })).toHaveAttribute('href', '#/operations/persona')
  })

  test('Automation filters tasks and exposes clear-filter state', async ({ page, baseURL }) => {
    await stubAutomationApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/automation'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '器官执行台' })).toBeVisible()

    const searchInput = page.getByPlaceholder('搜索任务、画像、LLM/GPU、原因、gate 或 immune reason...')
    const memoryTaskRow = page.getByRole('button').filter({ hasText: 'memory.distill' })
    const browserTaskRow = page.getByRole('button').filter({ hasText: 'browser.collect' })
    await expect(memoryTaskRow).toBeVisible()
    await expect(browserTaskRow).toBeVisible()

    await searchInput.fill('browser')
    await expect(browserTaskRow).toBeVisible()
    await expect(memoryTaskRow).toBeHidden()
    await expect(page.getByText('已应用节律筛选')).toBeVisible()

    await page.getByRole('button', { name: '清除筛选' }).click()
    await expect(searchInput).toHaveValue('')
    await expect(memoryTaskRow).toBeVisible()
  })

  test('Insights group cards navigate to focused subroutes and back', async ({ page, baseURL }) => {
    await stubInsightsApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/insights'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '皮层洞察' })).toBeVisible()

    await page.getByRole('button', { name: /安全与护栏/ }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/insights/safety')
    await expect(page.getByRole('heading', { name: '安全与护栏' })).toBeVisible()

    const backButton = page.getByRole('button', { name: '返回分组' })
    await expect(backButton).toBeVisible()
    await backButton.evaluate((button) => button.click())
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/insights')
  })

  test('Sessions triage rail filters issue requests and opens Trace evidence', async ({ page, baseURL }) => {
    await stubSessionsApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/sessions'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '神经会话沉积' })).toBeVisible()
    const triageRail = page.locator('[aria-label="Sessions Triage Rail"]')
    await expect(triageRail).toBeVisible()
    await expect(triageRail.getByText('先处理 1 条异常请求')).toBeVisible()
    await expect(triageRail.getByText('req_session_error', { exact: true })).toBeVisible()

    await triageRail.getByRole('button', { name: '只看异常' }).click()
    await expect(page.getByLabel('会话状态筛选')).toHaveValue('error')
    await expect(triageRail.getByRole('button', { name: '打开推荐 Trace' })).toBeVisible()
    await expect(page.getByText('req_session_rejected')).toHaveCount(0)
    await expect(triageRail.getByText('当前页样本')).toBeVisible()

    await triageRail.getByRole('button', { name: '清空筛选' }).click()
    await expect(page.getByLabel('会话状态筛选')).toHaveValue('')
    await expect(triageRail.getByText('3/3')).toBeVisible()
    await expect(triageRail.getByText('当前查看全部状态。')).toBeVisible()

    await triageRail.getByRole('button', { name: '打开推荐 Trace' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace/req_session_error')
  })


  test('Trace home exposes triage rail and full-chain route', async ({ page, baseURL }) => {
    await stubTraceApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/trace'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Trace 排障入口' })).toBeVisible()
    await expect(page.getByText('Trace Gate')).toBeVisible()
    const fullChainLink = page.getByRole('link', { name: /完整链路图 打开大图模式/ })
    await expect(fullChainLink).toBeVisible()

    await fullChainLink.click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace/full')
  })

  test('Trace request detail triage rail focuses issue evidence', async ({ page, baseURL }) => {
    await stubTraceApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/trace/req_trace_smoke'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '请求级 Trace 深入查看' })).toBeVisible()
    const triageRail = page.locator('[aria-label="Trace Request Triage Rail"]')
    await expect(triageRail).toBeVisible()
    await expect(triageRail.getByText('Trace Triage')).toBeVisible()
    await expect(triageRail.getByText('先处理 1 个异常/降级节点')).toBeVisible()
    await expect(triageRail.getByText('读写漂移')).toHaveCount(2)
    const handoff = page.getByRole('region', { name: 'Trace Evidence Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('Snapshot')).toBeVisible()
    await expect(handoff.getByText('Issue Node')).toBeVisible()
    await expect(handoff.getByText('Evidence Feed')).toBeVisible()
    await expect(handoff.getByText('Runtime Context')).toBeVisible()
    await expect(handoff.getByText('Repair Handoff')).toBeVisible()
    await expect(handoff.getByText('3/5 ready')).toBeVisible()
    await expect(handoff.getByText('1 refs')).toBeVisible()

    await triageRail.getByRole('button', { name: '定位异常节点' }).click()
    await expect(page.locator('#trace-node-detail')).toContainText('降级')
    await expect(page.locator('#trace-node-detail')).toContainText('Route selected with fallback policy')
    await handoff.getByRole('button', { name: '定位节点' }).click()
    await expect(page.locator('#trace-node-detail')).toContainText('planner')

    await expect(triageRail.getByRole('link', { name: '打开 Immune Ops' }).first()).toHaveAttribute(
      'href',
      /#\/execution\/automation\?immune_replay_request_id=req_trace_smoke/,
    )
    await expect(handoff.getByRole('link', { name: '打开 Immune Ops' })).toHaveAttribute(
      'href',
      /#\/execution\/automation\?immune_replay_request_id=req_trace_smoke/,
    )
  })

  test('Trace request detail stays readable on mobile viewport', async ({ page, baseURL }) => {
    await page.setViewportSize({ width: 390, height: 1000 })
    await stubTraceApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/trace/req_trace_smoke'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '请求级 Trace 深入查看' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Trace Evidence Handoff' })).toBeVisible()
    await expect(page.locator('#trace-stage-overview')).toBeVisible()
    await expect(page.locator('#trace-node-detail')).toBeVisible()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  })


  test('Tribe console filters canonical collaboration event samples', async ({ page, baseURL }) => {
    await stubTribeApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/tribe'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Tribe 协作入口' })).toBeVisible()
    const gate = page.getByRole('region', { name: 'Tribe Collaboration Gate' })
    await expect(gate).toBeVisible()
    await expect(gate.getByText('Tribe Gate')).toBeVisible()
    const handoff = page.getByRole('region', { name: 'Tribe Collaboration Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('Subscriber Coverage')).toBeVisible()
    await expect(handoff.getByText('2/2 covered')).toBeVisible()
    const coverage = page.getByRole('region', { name: 'Tribe Subscriber Coverage' })
    await expect(coverage.getByText('planner.handler')).toBeVisible()
    await expect(coverage.getByText('tool.handler')).toBeVisible()
    const eventCards = page.locator('article')
    const planEvent = eventCards.filter({ hasText: 'agent.plan.created' })
    const toolEvent = eventCards.filter({ hasText: 'agent.tool.result' })
    await expect(planEvent).toBeVisible()
    await expect(toolEvent).toBeVisible()

    const searchInput = page.getByLabel('搜索 Tribe 事件')
    await searchInput.fill('tool.runner')
    await expect(toolEvent).toBeVisible()
    await expect(planEvent).toBeHidden()
    await expect(page.getByText('已应用事件筛选：1/2')).toBeVisible()
    await expect(handoff.getByText('1/2 selected')).toBeVisible()
    await expect(handoff.getByText('1/1 covered')).toBeVisible()

    await page.getByRole('button', { name: '清除' }).click()
    await expect(searchInput).toHaveValue('')
    await expect(planEvent).toBeVisible()

    await page.getByRole('button', { name: 'agent.plan.created' }).click()
    await expect(planEvent).toBeVisible()
    await expect(toolEvent).toBeHidden()
    await expect(page.getByText('已应用事件筛选：1/2')).toBeVisible()
    await page.getByRole('button', { name: '全部' }).click()
    await expect(toolEvent).toBeVisible()

  })

  test('Gateway routing gate surfaces platform risk from projections', async ({ page, baseURL }) => {
    await stubGatewayApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/gateway'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '统一消息网关' })).toBeVisible()
    const gate = page.getByRole('region', { name: 'Gateway Routing Gate' })
    await expect(gate).toBeVisible()
    await expect(gate.getByText('Gateway Routing Gate')).toBeVisible()
    await expect(gate.getByText('Platform Health')).toBeVisible()
    await expect(gate.getByText('3/3 active，1 down，1 degraded，0 unknown。')).toBeVisible()

    const handoff = page.getByRole('region', { name: 'Gateway Routing Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('Platform Coverage')).toBeVisible()
    await expect(handoff.getByText('1 down / 1 degraded')).toBeVisible()
    await expect(handoff.getByText('WeChat Channel')).toBeVisible()
    await expect(handoff.getByText('1 limits / 1 events')).toBeVisible()

    await gate.getByRole('button', { name: /定位平台健康/ }).click()
    await expect(page.locator('#gateway-platform-health')).toBeVisible()
    await expect(page.getByText('Telegram')).toBeVisible()
    await expect(page.getByText('adapter offline')).toBeVisible()

    const wechatDetail = page.locator('#gateway-wechat-detail')
    await expect(wechatDetail.getByText('send_text')).toBeVisible()
    await expect(wechatDetail.getByText('wechat.message.received')).toBeVisible()
    await expect(page.getByRole('button', { name: '重置频率限制' })).toHaveCount(0)

    await gate.getByRole('link', { name: /Trace 追踪 Gateway/ }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace')
  })

  test('Body integration keeps real health visible and retires direct MateEngine writes', async ({ page, baseURL }) => {
    await stubBodyIntegrationApis(page)
    await page.goto(monitorUrl(baseURL, '/integrations/body'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '身体集成' })).toBeVisible()
    const gate = page.locator('[aria-label="Body Bridge Gate"]')
    await expect(gate).toBeVisible()
    await expect(gate.getByText('身体桥在线，人工写入口已迁移')).toBeVisible()
    await expect(gate.getByText('3 个动作可见')).toBeVisible()
    await expect(gate.getByText('2 个事件类型可见')).toBeVisible()
    const handoff = page.getByRole('region', { name: 'Body Dispatch Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('Bridge Health')).toBeVisible()
    await expect(handoff.getByText('Capability Catalog')).toBeVisible()
    await expect(handoff.getByText('Current Intent')).toBeVisible()
    await expect(handoff.getByText('Dispatch Result')).toBeVisible()
    await expect(handoff.getByText('2/4 ready')).toBeVisible()
    await expect(handoff.getByText('migrated')).toBeVisible()

    await gate.getByRole('button', { name: /定位迁移说明/ }).click()
    await page.getByLabel('Body action').selectOption('say')
    const sayInput = page.getByLabel('Body say text')
    await sayInput.fill('')
    await expect(sayInput).toHaveAttribute('aria-invalid', 'false')
    await expect(page.getByRole('button', { name: '人工发送已迁移' })).toBeDisabled()
    await sayInput.fill('身体桥 smoke say')
    await expect(page.getByText('MateEngine 人工直推已迁移')).toBeVisible()
  })

  test('Body integration gate locks dispatch when MateEngine health is degraded', async ({ page, baseURL }) => {
    await stubBodyIntegrationDegradedApis(page)
    await page.goto(monitorUrl(baseURL, '/integrations/body'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '身体集成' })).toBeVisible()
    const gate = page.locator('[aria-label="Body Bridge Gate"]')
    await expect(gate).toBeVisible()
    await expect(gate.getByText('MateEngine bridge 未确认在线')).toBeVisible()
    await expect(gate.getByText('身体桥需确认')).toBeVisible()
    const handoff = page.getByRole('region', { name: 'Body Dispatch Handoff' })
    await expect(handoff).toBeVisible()
    await expect(handoff.getByText('degraded')).toBeVisible()
    await expect(handoff.getByText('migrated', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '人工发送已迁移' })).toBeDisabled()
  })

  test('Task Runtime replays a complex graph from plan to terminal cursor', async ({ page, baseURL }) => {
    await stubTaskRuntimeApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/task-runtime'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '执行审查' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'graph-smoke@7 · COMPLETED' })).toBeVisible()
    const dag = page.getByRole('region', { name: 'TaskGraph DAG' })
    await expect(dag).toBeVisible()
    await expect(dag.getByText('plan', { exact: true })).toBeVisible()
    await expect(dag.getByText('execute', { exact: true })).toBeVisible()
    await expect(dag.getByText('publish', { exact: true })).toBeVisible()
    await expect(dag.getByText('Attempt #2 · attempt-execute')).toBeVisible()

    const budgets = page.getByRole('region', { name: 'Task budget reservations' })
    await expect(budgets.getByText('budget-smoke')).toBeVisible()
    await expect(budgets.getByText(/Consumed · 420 tokens · 3 tools · 900ms/)).toBeVisible()

    const timeline = page.getByRole('region', { name: 'Runtime cursor timeline' })
    await expect(timeline.getByText('TASK_GRAPH_CREATED')).toBeVisible()
    await expect(timeline.getByText('TASK_GRAPH_COMPLETED')).toBeVisible()
    await expect(timeline.getByText('after 0 → next 7')).toBeVisible()

    const reviews = page.getByRole('region', { name: 'Review command rail' })
    await expect(reviews.getByText('review-expired', { exact: true })).toBeVisible()
    await expect(reviews.getByText('已过期，approve / reject / resume 操作入口关闭。')).toBeVisible()
    await expect(reviews.getByRole('button', { name: '批准 review-expired' })).toHaveCount(0)
    await expect(reviews.getByRole('button', { name: '恢复 review-expired' })).toHaveCount(0)
  })


})
