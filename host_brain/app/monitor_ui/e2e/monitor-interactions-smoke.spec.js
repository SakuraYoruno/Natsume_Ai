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
    await stubCommonReadApis(page)
    await page.goto(monitorUrl(baseURL, '/cockpit'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '现在先处理什么' })).toBeVisible()
    await expect(page.getByText('观测 Gate')).toBeVisible()

    await page.locator('a[href="#/life/pathologies"]').filter({ hasText: '只看已经形成的异常模式' }).click()
    await expect.poll(() => new URL(page.url()).hash).toBe('#/life/pathologies')
  })

  test('Pentest wizard toggles lab-only controls and keeps readonly path available', async ({ page, baseURL }) => {
    await stubPentestApis(page)
    await page.goto(monitorUrl(baseURL, '/security/pentest'), { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Run Wizard' })).toBeVisible()

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
    await expect(createRunButton).toBeDisabled()

    await modeSelect.selectOption('readonly')
    await expect(createRunButton).toBeEnabled()
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
