import { expect, test } from '@playwright/test'

function monitorUrl(baseURL, routePath) {
  return new URL(`#${routePath}`, baseURL).toString()
}

async function fulfillJson(route, body) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function stubCommonReadApis(page) {
  await page.route('**/obs/runtime-snapshot**', (route) => fulfillJson(route, {
    timestamp: '2026-06-20T08:00:00Z',
    background_runtime: { status: 'healthy' },
  }))
  await page.route('**/obs/prompt-builder/stats**', (route) => fulfillJson(route, {
    total: 0,
    active_templates: 0,
  }))
  await page.route('**/v1/pentest/capabilities**', (route) => fulfillJson(route, {
    count: 2,
    tools_by_phase: {
      phase1_passive: [
        {
          id: 'pentest.http_headers',
          name: 'HTTP Headers',
          phase: 'phase1_passive',
          risk: 'low',
          readonly: true,
          side_effect: false,
        },
      ],
      phase4_exploit: [
        {
          id: 'pentest.safe_lab_exploit',
          name: 'Safe Lab Exploit',
          phase: 'phase4_exploit',
          risk: 'high',
          readonly: false,
          side_effect: true,
        },
      ],
    },
  }))
}

async function stubOverviewApis(page) {
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
  await page.route('**/obs/agent_loop/stats**', (route) => fulfillJson(route, {
    total_loops: 8,
    achieved: 6,
    failed: 2,
    running_count: 1,
    achievement_rate: 75,
    avg_rounds: 3.2,
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

async function stubPentestApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/v1/pentest/environment**', (route) => fulfillJson(route, {
    lab_mode: false,
    allow_exploit: false,
    use_kali: false,
    oob_enabled: false,
  }))
  await page.route('**/v1/pentest/runs**', async (route) => {
    if (route.request().method() === 'POST') {
      await fulfillJson(route, {
        run_id: 'smoke-readonly-run',
        status: 'completed',
        target: 'http://127.0.0.1:4173/monitor/',
      })
      return
    }
    await fulfillJson(route, { runs: [] })
  })
}

async function stubPentestRunDetailApis(page) {
  await stubCommonReadApis(page)
  await page.addInitScript(() => {
    window.EventSource = class StubEventSource {
      constructor() {
        setTimeout(() => this.onerror?.({ type: 'error' }), 0)
      }

      addEventListener() {}

      close() {}
    }
  })
  await page.route('**/v1/pentest/environment**', (route) => fulfillJson(route, {
    lab_mode: true,
    allow_exploit: true,
    use_kali: false,
    oob_enabled: false,
  }))
  await page.route('**/v1/pentest/runs/smoke-lab-run/report**', async (route) => {
    const format = new URL(route.request().url()).searchParams.get('format')
    if (format === 'json') {
      await fulfillJson(route, {
        run_id: 'smoke-lab-run',
        findings: 2,
        artifact_ref: 'artifacts/pentest/smoke-lab-run.md',
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/markdown',
      body: '# Pentest smoke report\n\nFinding: Debug endpoint exposed.\n\nArtifact: artifacts/pentest/smoke-lab-run.md',
    })
  })
  await page.route('**/v1/pentest/runs/smoke-lab-run', (route) => fulfillJson(route, {
    run_id: 'smoke-lab-run',
    status: 'waiting_approval',
    target: 'http://127.0.0.1:4173/monitor/',
    scope: ['127.0.0.1', 'localhost'],
    mode: 'lab_full',
    phases: ['phase1_passive', 'phase2_active', 'phase3_vuln', 'phase4_exploit'],
    approval_required: true,
    pending_side_effect_steps: [
      {
        phase: 'phase4_exploit',
        tool_id: 'pentest.safe_lab_exploit',
        target: 'http://127.0.0.1:4173/monitor/',
        execution_mode: 'lab',
        status: 'pending',
      },
    ],
    guard_stats: {
      scope_allowed: 4,
      side_effect_blocked: 1,
    },
    findings: [
      {
        title: 'Debug endpoint exposed',
        severity: 'HIGH',
        description: 'A debug-only route is reachable inside the authorized lab scope.',
        target: 'http://127.0.0.1:4173/monitor/debug',
        evidence_refs: ['evidence://smoke/debug-route', 'artifact://headers/debug'],
        recommendation: 'Disable debug routes outside the isolated lab scope.',
      },
      {
        title: 'TLS info disclosure',
        severity: 'LOW',
        description: 'A non-sensitive TLS banner is visible during passive collection.',
        target: 'http://127.0.0.1:4173/monitor/',
      },
    ],
    events: [
      { seq: 1, type: 'run_created', ts_ms: 1782260000000, message: 'Run created' },
      { seq: 2, type: 'approval_required', ts_ms: 1782260001000, message: 'Phase4 requires approval' },
    ],
    audit_ref: 'audit://pentest/smoke-lab-run',
    artifact_refs: {
      markdown: 'artifacts/pentest/smoke-lab-run.md',
    },
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
    },
    {
      incident_id: 'incident-low-smb',
      title: 'Legacy SMB service exposed',
      risk: 'low',
      detail: 'SMBv1-compatible service observed on lab subnet.',
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
  await page.route('**/internal/security/hunt/run-once', async (route) => {
    await fulfillJson(route, {
      ok: true,
      reason: (await route.request().postDataJSON()).reason,
      asset_count: 2,
      incident_count: 2,
    })
  })
}

async function stubToolsApis(page) {
  await stubCommonReadApis(page)
  const tools = [
    {
      id: 'read.echo',
      name: 'Read Echo',
      desc: 'Readonly smoke tool',
      side_effect_level: 'READ',
      auth_level: 'NONE',
      version: '1.0.0',
      schema_hash: 'abcdef0123456789abcdef',
      domain_tags: ['smoke', 'read'],
    },
    {
      id: 'write.note',
      name: 'Write Note',
      desc: 'Write smoke tool',
      side_effect_level: 'WRITE',
      auth_level: 'APPROVAL_REQUIRED',
      version: '1.0.0',
      schema_hash: '1234567890abcdef123456',
      domain_tags: ['smoke', 'write'],
    },
  ]
  await page.route('**/tools/health**', (route) => fulfillJson(route, {
    status: 'OK',
    catalog_size: 2,
    read_tools: 1,
    write_tools: 1,
    updated_at: '2026-06-20T08:00:00Z',
  }))
  await page.route('**/tools/meta-pack**', (route) => {
    const query = new URL(route.request().url()).searchParams.get('query')?.toLowerCase() || ''
    const filteredTools = query
      ? tools.filter((tool) => `${tool.id} ${tool.name} ${tool.desc}`.toLowerCase().includes(query))
      : tools
    return fulfillJson(route, { tool_meta_pack: filteredTools })
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
    '**/obs/agent_loop/stats**',
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
    status: 'OK',
    total_latency_ms: 128,
    timestamp: '2026-06-20T08:00:00Z',
    summary: {
      session_id: 'sess_trace_smoke',
      conversation_id: 'conv_trace_smoke',
      request_id: requestId,
    },
    nodes: [
      {
        id: 'input',
        name: 'Input capture',
        stage: 'input',
        status: 'OK',
        latency_ms: 12,
        output: 'User request normalized',
      },
      {
        id: 'planner',
        name: 'Planner',
        stage: 'plan',
        status: 'OK',
        latency_ms: 48,
        output: 'Route selected',
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
  await page.route('**/obs/trace-snapshot/**', (route) => fulfillJson(route, traceSnapshot))
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

async function stubEdgeCommandApis(page) {
  await stubCommonReadApis(page)
  await page.route('**/internal/edge_command/status', (route) => fulfillJson(route, {
    enabled: true,
    edge_base_url: 'http://127.0.0.1:19090',
    queue_depth: 0,
  }))
  await page.route('**/internal/edge_command/push', async (route) => {
    await fulfillJson(route, {
      ok: true,
      command_id: 'edge-smoke-command',
      echo: await route.request().postDataJSON(),
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

test.describe('Monitor interaction smoke', () => {
  test('Observability overview exposes triage rail and Trace route', async ({ page, baseURL }) => {
    await stubOverviewApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/overview'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '排障入口' })).toBeVisible()
    await expect(page.getByText('观测 Gate')).toBeVisible()
    await expect(page.getByText('先看告警与服务状态')).toBeVisible()

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
    await expect(mission.getByRole('link', { name: /进入主病灶/ })).toHaveAttribute('href', '#/life/pathologies/emotion-wave')

    await mission.locator('a[href="#/life/pathologies"]').filter({ hasText: '只看已经形成的异常模式' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/pathologies')
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

  test('Pentest wizard toggles lab-only controls and keeps readonly path available', async ({ page, baseURL }) => {
    await stubPentestApis(page)
    await page.goto(monitorUrl(baseURL, '/security/pentest'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Run Wizard' })).toBeVisible()
    const mission = page.getByRole('region', { name: 'Pentest Mission Control' })
    await expect(mission).toBeVisible()
    await expect(mission.getByText('可以创建 readonly run')).toBeVisible()

    const targetInput = page.getByLabel('Target')
    const scopeInput = page.getByLabel('Scope')
    const modeSelect = page.getByLabel('Mode')
    const createRunButton = page.getByRole('button', { name: '创建 Run' })

    await expect(targetInput).toHaveValue('http://127.0.0.1:5173/monitor/')
    await targetInput.fill('http://127.0.0.1:4173/monitor/')
    await scopeInput.fill('127.0.0.1')
    await expect(createRunButton).toBeEnabled()

    await modeSelect.selectOption('lab_full')
    await expect(page.getByText('Lab Mode OFF，lab_full 已禁用')).toBeVisible()
    await expect(mission.getByText('Lab Mode OFF 阻断 lab_full')).toBeVisible()
    await expect(createRunButton).toBeDisabled()

    await modeSelect.selectOption('readonly')
    await targetInput.fill('http://example.com/')
    await expect(mission.getByText('Target host 未命中 scope')).toBeVisible()
    await expect(createRunButton).toBeDisabled()

    await targetInput.fill('http://127.0.0.1:4173/monitor/')
    await expect(createRunButton).toBeEnabled()
    await mission.getByRole('button', { name: /Phase 矩阵/ }).click()
    await expect(page.locator('#pentest-phase-matrix')).toBeVisible()
  })

  test('Pentest run detail summarizes attack path and approval gate', async ({ page, baseURL }) => {
    await stubPentestRunDetailApis(page)
    await page.goto(monitorUrl(baseURL, '/security/pentest/runs/smoke-lab-run'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: /渗透任务详情/ })).toBeVisible()
    await expect(page.getByText('Run Mission Brief')).toBeVisible()
    await expect(page.getByText('需要记录 Side-effect Approval')).toBeVisible()
    await expect(page.getByText('ScopeGuard').first()).toBeVisible()
    await expect(page.getByText('phase4_exploit').first()).toBeVisible()
    const sidePanel = page.getByRole('complementary')
    await expect(sidePanel.getByText('Debug endpoint exposed')).toBeVisible()
    await expect(sidePanel.getByText('TLS info disclosure')).toBeVisible()
    await expect(page.getByText('攻击路径可视化')).toBeVisible()
    await expect(page.getByRole('button', { name: '记录 Side-effect Approval' })).toBeEnabled()

    await sidePanel.getByLabel('搜索 Findings').fill('debug')
    await expect(sidePanel.getByText('Debug endpoint exposed')).toBeVisible()
    await expect(sidePanel.getByText('TLS info disclosure')).toBeHidden()
    await sidePanel.getByRole('button', { name: '清除' }).click()
    await sidePanel.getByRole('button', { name: 'High+' }).click()
    await expect(sidePanel.getByText('Debug endpoint exposed')).toBeVisible()
    await expect(sidePanel.getByText('TLS info disclosure')).toBeHidden()

    await page.getByRole('button', { name: 'json' }).click()
    await expect(page.getByText('Artifact ref：')).toBeVisible()
    await expect(page.getByText('artifact_ref')).toBeVisible()
  })

  test('Security Immune gate filters incidents and runs a manual hunt', async ({ page, baseURL }) => {
    await stubSecurityImmuneApis(page)
    await page.goto(monitorUrl(baseURL, '/security/immune'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Security Immune', exact: true })).toBeVisible()

    const gate = page.getByRole('region', { name: 'Security Immune Gate' })
    await expect(gate).toBeVisible()
    await expect(gate.getByText('存在 High incident')).toBeVisible()
    await expect(gate.getByText('2 assets')).toBeVisible()
    await expect(gate.getByText('2 open')).toBeVisible()
    await expect(page.getByText('Suspicious PowerShell execution')).toBeVisible()
    await expect(page.getByText('Legacy SMB service exposed')).toBeVisible()

    await page.getByLabel('Security Immune minimum risk').selectOption('high')
    await expect(page.getByText('Suspicious PowerShell execution')).toBeVisible()
    await expect(page.getByText('Legacy SMB service exposed')).toBeHidden()

    await gate.getByRole('button', { name: /Incidents/ }).click()
    await expect(page.locator('#security-immune-incidents')).toBeVisible()

    await gate.getByRole('button', { name: 'Run Hunt' }).click()
    await expect(page.getByText('Hunt 已完成')).toBeVisible()
    await expect(page.getByText('manual_web_security_immune')).toBeVisible()
  })

  test('Tools catalog supports search, side-effect filters, and detail selection', async ({ page, baseURL }) => {
    await stubToolsApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/tools'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '工具管理' })).toBeVisible()

    const searchInput = page.getByPlaceholder('搜索工具名或描述...')
    await expect(page.getByText('Read Echo')).toBeVisible()
    await expect(page.getByText('Write Note')).toBeVisible()

    await searchInput.fill('write')
    await expect(page.getByText('Write Note')).toBeVisible()
    await expect(page.getByText('Read Echo')).toBeHidden()

    await page.getByRole('button', { name: '写入（1）' }).click()
    await expect(page.getByText('Write Note')).toBeVisible()
    await page.getByRole('button', { name: /Write Note/ }).click()
    await expect(page.getByRole('button', { name: '执行该工具' })).toBeVisible()
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
    await backButton.focus()
    await page.keyboard.press('Enter')
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/insights')
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

  test('Edge command validates payload and requires the control gate', async ({ page, baseURL }) => {
    await stubEdgeCommandApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/edge-command'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Edge Command Gate' })).toBeVisible()
    await expect(page.getByText('发送 Gate')).toBeVisible()

    const sendButton = page.getByRole('button', { name: '发送到 Edge' })
    await expect(sendButton).toBeDisabled()
    await expect(page.getByText('开启控制门后才能发送。')).toBeVisible()

    await page.getByRole('checkbox', { name: '开启控制门' }).check()
    await expect(sendButton).toBeEnabled()

    const payloadInput = page.getByLabel('payload JSON')
    await payloadInput.fill('{bad json')
    await expect(payloadInput).toHaveAttribute('aria-invalid', 'true')
    await expect(page.getByText(/payload JSON 无效/).first()).toBeVisible()
    await expect(sendButton).toBeDisabled()

    await payloadInput.fill('{"emotion_tag":"CALM","intensity":0.2}')
    await expect(sendButton).toBeEnabled()
    await sendButton.click()
    await expect(page.getByText('Push 完成')).toBeVisible()
    await expect(page.getByText('edge-smoke-command')).toBeVisible()
  })

  test('Tribe console filters event bus samples and links to Agent Loop', async ({ page, baseURL }) => {
    await stubTribeApis(page)
    await page.goto(monitorUrl(baseURL, '/execution/tribe'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Tribe 协作入口' })).toBeVisible()
    await expect(page.getByText('Tribe Gate')).toBeVisible()
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

    await page.getByRole('button', { name: '清除' }).click()
    await expect(searchInput).toHaveValue('')
    await expect(planEvent).toBeVisible()

    await page.getByRole('link', { name: /Agent Loop 查看智能体循环/ }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/execution/agent-loop')
  })
})
