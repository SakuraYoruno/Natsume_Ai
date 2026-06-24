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
  await page.route('**/obs/capability-gaps/stats**', (route) => fulfillJson(route, {
    detected_total: 0,
    active_runtime_capabilities: 0,
    hotspots: [],
    forge_candidates: [],
    forge_runtime_quality: null,
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
      asset_id: 'endpoint-1',
      source: 'edr.rule.powershell.encoded',
      evidence_refs: ['evidence://security/powershell-encoded'],
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
  await page.route('**/tools/execute', async (route) => {
    const body = await route.request().postDataJSON()
    await fulfillJson(route, {
      ok: true,
      tool_id: body.tool_id,
      intent_id: body.intent_id,
      session_id: body.session_id,
      args: body.args,
      approval_token_ref: body.approval_token_ref || null,
    })
  })
}

async function stubPromptConfigApis(page) {
  await stubCommonReadApis(page)
  const templates = [
    {
      template_id: 'react_loop.prompt_templates',
      description: 'Agent loop planner prompts',
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

async function stubChatDebugApis(page) {
  await stubCommonReadApis(page)
  const requestId = 'req_chat_debug_smoke'
  const traceSnapshot = {
    request_id: requestId,
    status: 'OK',
    total_latency_ms: 340,
    summary: {
      input_text: '请用一句话说明当前系统状态，并触发一次可追踪的调试链路。',
      output_text: '系统处于可追踪调试状态。',
    },
    nodes: [
      {
        node_id: 'input',
        id: 'input',
        name: 'Input capture',
        stage: 'input',
        status: 'OK',
        ts_ms: 1781942420000,
      },
      {
        node_id: 'response',
        id: 'response',
        name: 'Response render',
        stage: 'output',
        status: 'OK',
        ts_ms: 1781942420300,
        output: '系统处于可追踪调试状态。',
      },
    ],
  }
  await page.route('**/v1/chat', (route) => route.fulfill({
    status: 200,
    contentType: 'text/event-stream',
    body: [
      `data: ${JSON.stringify({ event: 'request.started', phase: 'PRESENCE', request_id: requestId, ts_ms: 1781942420000 })}`,
      '',
      `data: ${JSON.stringify({ event: 'node.input', phase: 'NODE', node_id: 'input', request_id: requestId, ts_ms: 1781942420100 })}`,
      '',
      `data: ${JSON.stringify({ event: 'node.output', phase: 'NODE', node_id: 'response', request_id: requestId, ts_ms: 1781942420300 })}`,
      '',
      `data: ${JSON.stringify({ event: 'message.final', phase: 'COMMIT', content: '系统处于可追踪调试状态。', request_id: requestId, ts_ms: 1781942420400 })}`,
      '',
      '',
    ].join('\n'),
  }))
  await page.route(`**/obs/trace/${requestId}`, (route) => fulfillJson(route, traceSnapshot))
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
  await page.route('**/v1/mateengine/action', async (route) => {
    await fulfillJson(route, {
      ok: true,
      accepted: true,
      request_id: 'mate-smoke-action',
      echo: await route.request().postDataJSON(),
    })
  })
  await page.route('**/v1/mateengine/say', async (route) => {
    await fulfillJson(route, {
      ok: true,
      accepted: true,
      request_id: 'mate-smoke-say',
      echo: await route.request().postDataJSON(),
    })
  })
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
    const plannedNarrative = page.getByRole('region', { name: 'Planned Attack Path Narrative' })
    await expect(plannedNarrative).toBeVisible()
    await expect(plannedNarrative.getByText('Readonly run 可直接创建')).toBeVisible()

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
    await expect(plannedNarrative.getByText('先处理 Approval Rail')).toBeVisible()
    await expect(createRunButton).toBeDisabled()

    await modeSelect.selectOption('readonly')
    await targetInput.fill('http://example.com/')
    await expect(mission.getByText('Target host 未命中 scope')).toBeVisible()
    await expect(plannedNarrative.getByText('先处理 ScopeGuard')).toBeVisible()
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
    const decisionRail = page.getByRole('region', { name: 'Pentest Run Decision Rail' })
    await expect(decisionRail).toBeVisible()
    await expect(decisionRail.getByText('先记录 Side-effect Approval')).toBeVisible()
    await expect(decisionRail.getByText('1 High+ · 2 refs')).toBeVisible()
    await decisionRail.getByRole('button', { name: /处理 Approval Rail/ }).click()
    await expect(page.getByRole('button', { name: '记录 Side-effect Approval' })).toBeFocused()
    await decisionRail.getByRole('button', { name: /Reports/ }).click()
    await expect(page.getByRole('button', { name: 'markdown' })).toBeFocused()
    await expect(page.getByText('Run Mission Brief')).toBeVisible()
    await expect(page.getByText('需要记录 Side-effect Approval')).toBeVisible()
    await expect(page.getByText('ScopeGuard').first()).toBeVisible()
    await expect(page.getByText('phase4_exploit').first()).toBeVisible()
    const sidePanel = page.getByRole('complementary')
    await expect(sidePanel.getByText('Debug endpoint exposed')).toBeVisible()
    await expect(sidePanel.getByText('TLS info disclosure')).toBeVisible()
    await expect(page.getByText('攻击路径可视化')).toBeVisible()
    const runtimeNarrative = page.getByRole('region', { name: 'Run Attack Path Narrative' })
    await expect(runtimeNarrative).toBeVisible()
    await expect(runtimeNarrative.getByText('等待 Side-effect Approval')).toBeVisible()
    await expect(runtimeNarrative.getByText('处理 Approval Rail')).toBeVisible()
    const approvalRail = page.getByRole('region', { name: 'Pentest Approval Rail' })
    await expect(approvalRail).toBeVisible()
    await expect(approvalRail.getByText('等待审批记录')).toBeVisible()
    await expect(approvalRail.getByText('Approval Gate Timeline')).toBeVisible()
    await expect(approvalRail.getByText('approval pending')).toBeVisible()
    await expect(approvalRail.getByText('Audit Packet')).toBeVisible()
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
    await expect(page.getByRole('heading', { name: '安全免疫', exact: true })).toBeVisible()

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

    await page.getByLabel('搜索 Security Incidents').fill('smb')
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeHidden()
    await page.getByLabel('清除 Security Incident 搜索').click()
    await page.getByRole('button', { name: '缺证据' }).click()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeHidden()
    await page.getByRole('button', { name: '全部' }).click()

    await page.getByLabel('Security Immune minimum risk').selectOption('high')
    await expect(incidentsSection.getByText('Suspicious PowerShell execution', { exact: true })).toBeVisible()
    await expect(incidentsSection.getByText('Legacy SMB service exposed', { exact: true })).toBeHidden()

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
    const gate = page.getByRole('region', { name: 'Tools Execution Gate' })
    await expect(gate).toBeVisible()
    await expect(gate.getByText('目录可执行，写入工具需要审批')).toBeVisible()
    await expect(gate.getByText('2 tools')).toBeVisible()

    const searchInput = page.getByPlaceholder('搜索工具名或描述...')
    await expect(page.getByText('Read Echo')).toBeVisible()
    await expect(page.getByText('Write Note')).toBeVisible()

    await searchInput.fill('write')
    await expect(page.getByText('Write Note')).toBeVisible()
    await expect(page.getByText('Read Echo')).toBeHidden()

    await page.getByRole('button', { name: '写入（1）' }).click()
    await expect(page.getByText('Write Note')).toBeVisible()
    await page.getByRole('button', { name: /Write Note/ }).click()
    await expect(gate.getByText('写入工具需要审批令牌')).toBeVisible()
    await expect(page.locator('#tools-detail-panel').getByText('Execution Gate：写入工具需要审批令牌')).toBeVisible()
    await expect(page.getByText('审批确认').first()).toBeVisible()
    await expect(page.getByRole('button', { name: '执行该工具' })).toBeVisible()
    await page.getByRole('button', { name: '执行该工具' }).click()
    await expect(page.getByText('执行工具：Write Note')).toBeVisible()
    await expect(page.getByText('写入工具必须填写审批令牌引用。')).toBeVisible()
    const submitExecutionButton = page.getByRole('button', { name: '执行', exact: true })
    await expect(submitExecutionButton).toBeDisabled()
    await page.getByPlaceholder('approval_token_ref').fill('approval://smoke')
    await expect(submitExecutionButton).toBeEnabled()
    await submitExecutionButton.click()
    await expect(page.getByText('执行完成')).toBeVisible()
    await expect(page.locator('pre').filter({ hasText: '"tool_id": "write.note"' })).toBeVisible()
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
    await backButton.focus()
    await page.keyboard.press('Enter')
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

  test('Chat debug command rail sends a traceable smoke request', async ({ page, baseURL }) => {
    await stubChatDebugApis(page)
    await page.goto(monitorUrl(baseURL, '/observability/chat-debug'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '神经反射调试', exact: true })).toBeVisible()
    const commandRail = page.locator('[aria-label="Chat Debug Command Rail"]')
    await expect(commandRail).toBeVisible()
    await expect(commandRail.getByText('先输入一条消息，启动一次可追踪调试')).toBeVisible()

    await commandRail.getByRole('button', { name: '填入调试样例' }).click()
    await expect(page.getByLabel('聊天调试输入')).toHaveValue(/可追踪的调试链路/)
    await commandRail.getByRole('button', { name: '发送调试消息' }).click()

    await expect(commandRail.getByText('已拿到 Request ID，可进入 Trace 复核')).toBeVisible()
    await expect(commandRail.getByText('req_chat_debug_smoke', { exact: true })).toBeVisible()
    await expect(page.locator('pre').filter({ hasText: '系统处于可追踪调试状态。' })).toBeVisible()
    await expect(page.getByText('4 条')).toBeVisible()

    await commandRail.getByRole('link', { name: '打开 Trace 详情' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/observability/trace/req_chat_debug_smoke')
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
    await expect(triageRail.getByText('读写漂移')).toBeVisible()

    await triageRail.getByRole('button', { name: '定位异常节点' }).click()
    await expect(page.locator('#trace-node-detail')).toContainText('降级')
    await expect(page.locator('#trace-node-detail')).toContainText('Route selected with fallback policy')

    await expect(triageRail.getByRole('link', { name: '打开 Immune Ops' })).toHaveAttribute(
      'href',
      /#\/execution\/automation\?immune_replay_request_id=req_trace_smoke/,
    )
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

  test('Body integration gate validates say text and sends through MateEngine', async ({ page, baseURL }) => {
    await stubBodyIntegrationApis(page)
    await page.goto(monitorUrl(baseURL, '/integrations/body'), { waitUntil: 'domcontentloaded' })

    await expect(page.getByRole('heading', { name: '身体集成' })).toBeVisible()
    const gate = page.locator('[aria-label="Body Bridge Gate"]')
    await expect(gate).toBeVisible()
    await expect(gate.getByText('身体动作链路可发送')).toBeVisible()
    await expect(gate.getByText('3 个动作可见')).toBeVisible()
    await expect(gate.getByText('2 个事件类型可见')).toBeVisible()

    await gate.getByRole('button', { name: /定位动作控制台/ }).click()
    await page.getByLabel('Body action').selectOption('say')
    const sayInput = page.getByLabel('Body say text')
    await sayInput.fill('')
    await expect(sayInput).toHaveAttribute('aria-invalid', 'true')
    await expect(page.getByRole('button', { name: '发送 say' })).toBeDisabled()
    await expect(gate.getByText('Say 动作需要先填写文本')).toBeVisible()

    await gate.getByRole('button', { name: /定位文本框/ }).click()
    await expect(sayInput).toBeFocused()
    await sayInput.fill('身体桥 smoke say')
    await expect(page.getByRole('button', { name: '发送 say' })).toBeEnabled()
    await page.getByRole('button', { name: '发送 say' }).click()

    await expect(page.getByText('发送完成')).toBeVisible()
    await expect(page.getByText('mate-smoke-say')).toBeVisible()
  })
})
